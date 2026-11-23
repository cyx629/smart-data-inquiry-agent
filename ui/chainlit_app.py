"""Chainlit 对话前端:执行过程全透出(召回表/指标命中/SQL/校验/重试/耗时),
所见即真实执行链路。

启动:chainlit run ui/chainlit_app.py --port 8501
"""
from __future__ import annotations

import os
import re
import sys
import time
import uuid

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import chainlit as cl                                  # noqa: E402
import plotly.graph_objects as go                      # noqa: E402
from chainlit.input_widget import Select               # noqa: E402

from app.audit.db import AuditLog                      # noqa: E402
from app.core.graph import GraphDeps, build_graph      # noqa: E402
from app.datasource.manager import DataSourceManager   # noqa: E402
from app.llm.client import LLMClient, build_embedder   # noqa: E402
from app.semantic.metadata import build_table_docs     # noqa: E402
from app.semantic.metrics import MetricStore           # noqa: E402
from app.semantic.store import (  # noqa: E402
    FewshotStore, SchemaIndex, load_glossaries, load_glossary,
)
from app.settings import settings                      # noqa: E402


def _build_deps() -> GraphDeps:
    dsm = DataSourceManager(settings.datasources_file)
    deps = GraphDeps(
        settings=settings,
        llm=LLMClient(settings),
        dsm=dsm,
        schema_index=SchemaIndex(build_embedder(settings),
                                 settings.full_schema_table_threshold),
        fewshots=FewshotStore(build_embedder(settings), settings.data_dir,
                              settings.fewshot_seed_dir),
        glossary=load_glossary(settings.glossary_file),
        audit=AuditLog(settings.data_dir),
        metrics=MetricStore(),
        glossaries=load_glossaries(
            [item["name"] for item in dsm.list()],
            settings.glossary_dir, settings.glossary_file),
    )
    for item in deps.dsm.list():
        deps.schema_index.load(item["name"], build_table_docs(deps.dsm.get(item["name"])))
        deps.fewshots.load(item["name"])
        deps.metrics.load(item["name"])
    return deps


DEPS = _build_deps()
GRAPH = build_graph(DEPS, checkpointer_path=os.path.join(settings.data_dir,
                                                         "checkpoints.db"))


def _md_table(rows: list[dict], cap: int = 20) -> str:
    if not rows:
        return "(空结果)"
    cols = list(rows[0].keys())
    esc = lambda v: str(v).replace("|", "\\|") if v is not None else ""
    lines = ["| " + " | ".join(cols) + " |",
             "| " + " | ".join("---" for _ in cols) + " |"]
    for r in rows[:cap]:
        lines.append("| " + " | ".join(esc(r.get(c)) for c in cols) + " |")
    if len(rows) > cap:
        lines.append(f"\n*(仅展示前 {cap} 行,共 {len(rows)} 行)*")
    return "\n".join(lines)


def _echarts_to_plotly(opt: dict) -> go.Figure | None:
    try:
        s0 = opt["series"][0]
        title = opt.get("title", {}).get("text", "")
        if s0["type"] == "pie":
            fig = go.Figure(go.Pie(
                labels=[d["name"] for d in s0["data"]],
                values=[d["value"] for d in s0["data"]], hole=0.35))
        else:
            x = opt["xAxis"]["data"]
            fig = go.Figure()
            for s in opt["series"]:
                trace = (go.Scatter(x=x, y=s["data"], mode="lines+markers",
                                    name=s.get("name", ""))
                         if s["type"] == "line"
                         else go.Bar(x=x, y=s["data"], name=s.get("name", "")))
                fig.add_trace(trace)
        fig.update_layout(title=title, height=360,
                          margin=dict(l=40, r=20, t=48, b=40))
        return fig
    except Exception:
        return None


def _schema_tables(schemas: list[str]) -> list[str]:
    return [doc.split("\n", 1)[0].replace("表名:", "").strip() for doc in schemas]


def _fmt_call(c: dict) -> str:
    return (f"🤖 已调用大模型 `{c.get('model', '?')}`:"
            f"输入 {c.get('prompt_tokens', 0)} / 输出 {c.get('completion_tokens', 0)} tokens")


def _fmt_llm_summary(calls: list[dict]) -> str:
    if not calls:
        return "⚡ 本次未调用大模型(指标库/干预确定性直出)"
    models: dict[str, int] = {}
    for c in calls:
        models[c.get("model", "?")] = models.get(c.get("model", "?"), 0) + 1
    total = sum(c.get("total_tokens", 0) for c in calls)
    detail = ", ".join(f"{m}×{n}" for m, n in models.items())
    return f"🤖 大模型调用 {len(calls)} 次({detail},共 {total} tokens)"


async def _send_settings():
    names = [d["name"] for d in DEPS.dsm.list()]
    await cl.ChatSettings([
        Select(id="datasource", label="数据源", values=names, initial_index=0),
    ]).send()


@cl.on_chat_start
async def on_start():
    cl.user_session.set("session_id", uuid.uuid4().hex)
    cl.user_session.set("datasource", DEPS.dsm.list()[0]["name"])
    await _send_settings()
    await cl.Message(
        content=(
            "👋 你好,我是**智能问数助手**。直接用中文提问即可,例如:\n"
            "- 各地区的销售额是多少?\n- 每个月的销售额趋势\n"
            "- 销量前5的产品是哪些?\n- 2026年6月华东大区的销售额\n\n"
            "生成的 SQL、召回的表、每次重试都会**完整展示执行过程**,可展开各步骤核对。"
        )
    ).send()


@cl.on_settings_update
async def on_settings(settings_dict):
    cl.user_session.set("datasource", settings_dict["datasource"])
    await cl.Message(content=f"已切换数据源:**{settings_dict['datasource']}**").send()


async def _run_pipeline(question: str, confirmed: bool = False):
    session_id = cl.user_session.get("session_id")
    datasource = cl.user_session.get("datasource")
    config = {"configurable": {"thread_id": session_id}}
    inputs = {"question": question, "datasource": datasource,
              "session_id": session_id, "confirmed": confirmed}

    final: dict = {}
    t0 = time.time()
    it = GRAPH.stream(inputs, config, stream_mode="updates")

    def _next():
        return next(it, None)

    while True:
        update = await cl.make_async(_next)()
        if update is None:
            break
        for node, delta in update.items():
            if not isinstance(delta, dict):
                continue
            final.update({k: v for k, v in delta.items() if v is not None})
            await _render_step(node, delta)

    await _render_final(question, final, elapsed=time.time() - t0)


async def _render_step(node: str, delta: dict):
    if node == "rewrite":
        async with cl.Step(name="理解问题", type="tool") as st:
            calls = delta.get("llm_calls") or []
            llm_note = (_fmt_call(calls[-1]) if calls
                        else "首轮对话,直接使用原问题(此步未调用大模型)")
            st.output = (f"改写后问题:{delta.get('rewritten_question', '')}\n"
                         f"目标方言:`{delta.get('dialect', '')}`\n{llm_note}")
    elif node == "match_metric":
        async with cl.Step(name="匹配指标库", type="tool") as st:
            hit = delta.get("metric_hit")
            st.output = (f"✅ 命中:{hit}(跳过 LLM 生成,使用治理口径 SQL)"
                         if hit else "未命中指标/干预库,进入 LLM 生成链路")
    elif node == "recall_schema":
        async with cl.Step(name="召回表结构", type="retrieval") as st:
            tables = _schema_tables(delta.get("relevant_schemas") or [])
            st.output = f"召回 {len(tables)} 张表:{'、'.join(tables)}"
    elif node == "recall_fewshot":
        async with cl.Step(name="检索参考样例", type="retrieval") as st:
            fs = delta.get("fewshots") or []
            st.output = ("\n".join(f"- {f['question']}" for f in fs)
                         if fs else "无相似历史样例")
    elif node in ("generate", "fix"):
        name = "生成SQL" if node == "generate" else "修正SQL"
        async with cl.Step(name=name, type="llm") as st:
            calls = delta.get("llm_calls") or []
            llm_note = _fmt_call(calls[-1]) if calls else ""
            if delta.get("clarify"):
                st.output = f"需要澄清:{delta['clarify']}\n{llm_note}"
            else:
                st.output = (f"```sql\n{delta.get('sql', '')}\n```\n"
                             f"{delta.get('explanation', '')}\n{llm_note}")
    elif node == "validate":
        async with cl.Step(name="安全校验", type="tool") as st:
            if delta.get("sql_ok"):
                st.output = "✅ 通过(单条SELECT / 表白名单 / 敏感列 / LIMIT 注入)"
                if delta.get("needs_confirm"):
                    st.output += "\n⚠️ 大表无过滤条件,需人工确认"
            else:
                st.output = f"❌ 拒绝:{delta.get('validate_msg', '')}"
    elif node == "execute":
        async with cl.Step(name="执行查询", type="tool") as st:
            if delta.get("sql_error"):
                st.output = f"❌ 执行失败:{delta['sql_error'][:300]}"
            else:
                rows = delta.get("result_rows") or []
                st.output = (f"✅ 返回 {len(rows)} 行"
                             + ("(已截断)" if delta.get("truncated") else ""))


async def _render_final(question: str, final: dict, elapsed: float):
    status = final.get("status", "")
    answer = final.get("answer", "")
    sql = final.get("sql", "")
    rows = final.get("result_rows") or []
    audit_id = final.get("audit_id", "")

    parts = [answer]
    if status == "success" and sql:
        parts.append(f"```sql\n{sql}\n```")
    if status == "success" and rows:
        parts.append(_md_table(rows))
    retries = len(final.get("attempts") or [])
    meta = f"\n> ⏱ {elapsed:.1f}s"
    if retries:
        meta += f" · 重试 {retries} 次"
    if final.get("metric_hit"):
        meta += f" · 指标命中:{final['metric_hit']}"
    if audit_id:
        meta += f" · 审计ID `{audit_id[:8]}`"
    meta += f"\n> {_fmt_llm_summary(final.get('llm_calls') or [])}"
    parts.append(meta)

    elements = []
    if final.get("chart"):
        # 优先服务端 PNG(离线/内网必显示);失败再退 Plotly 组件
        from app.core.chart_render import render_png
        if png := render_png(final["chart"]):
            elements.append(cl.Image(name="chart", content=png, display="inline",
                                     size="large"))
        elif fig := _echarts_to_plotly(final["chart"]):
            elements.append(cl.Plotly(name="chart", figure=fig, display="inline"))

    actions = []
    if status == "success" and audit_id:
        actions = [
            cl.Action(name="feedback", payload={"audit_id": audit_id, "adopted": True},
                      label="👍 采纳(回灌样例库)"),
            cl.Action(name="feedback", payload={"audit_id": audit_id, "adopted": False},
                      label="👎 有误"),
        ]
    elif status == "needs_confirm":
        # 记录待确认问题:用户点按钮或直接回复"确认/确定"均可继续
        cl.user_session.set("pending_confirm", question)
        actions = [cl.Action(name="confirm_run", payload={"question": question},
                             label="✅ 确认执行")]

    await cl.Message(content="\n\n".join(p for p in parts if p),
                     elements=elements, actions=actions).send()


_CONFIRM_RE = re.compile(
    r"^(确认执行|确认|确定|执行|继续|是的?|好的?|嗯|可以|ok|okay|yes|y)[!!。.~\s]*$",
    re.IGNORECASE,
)


@cl.on_message
async def on_message(message: cl.Message):
    text = message.content.strip()
    pending = cl.user_session.get("pending_confirm")
    if pending and _CONFIRM_RE.match(text):
        # 文字确认等同点击"确认执行"按钮
        cl.user_session.set("pending_confirm", None)
        await _run_pipeline(pending, confirmed=True)
        return
    cl.user_session.set("pending_confirm", None)   # 问了别的,待确认作废
    await _run_pipeline(text)


@cl.action_callback("confirm_run")
async def on_confirm(action: cl.Action):
    cl.user_session.set("pending_confirm", None)
    await _run_pipeline(action.payload["question"], confirmed=True)


@cl.action_callback("feedback")
async def on_feedback(action: cl.Action):
    audit_id = action.payload["audit_id"]
    adopted = action.payload["adopted"]
    record = DEPS.audit.get(audit_id)
    if not record:
        await cl.Message(content="⚠️ 审计记录不存在").send()
        return
    DEPS.audit.set_feedback(audit_id, "adopted" if adopted else "wrong")
    if adopted and record["final_sql"] and record["status"] == "success":
        DEPS.fewshots.add(record["datasource"],
                          record["rewritten_question"] or record["question"],
                          record["final_sql"])
        await cl.Message(content="✅ 已采纳,该问答已回灌样例库(越用越准)").send()
    else:
        await cl.Message(content="已记录反馈,感谢指正").send()
