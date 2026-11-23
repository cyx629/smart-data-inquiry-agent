"""LangGraph 主图(设计文档 §3.1 / §10):半 Agent 编排。

确定性节点(schema 召回、few-shot 检索、安全校验、执行)与 LLM 节点
(改写、生成、修正、总结)分离;错误修正闭环最多 max_retries 轮。
"""
from __future__ import annotations

import datetime
import json
import sqlite3
import time
from dataclasses import dataclass

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.graph import END, StateGraph

from app.audit.db import AuditLog
from app.core import prompts
from app.core import chart as chart_rules
from app.core.state import QAState
from app.datasource.manager import DataSourceManager
from app.llm.client import LLMClient
from app.security.validator import validate_sql
from app.semantic.metrics import MetricStore
from app.semantic.store import FewshotStore, SchemaIndex
from app.settings import Settings


@dataclass
class GraphDeps:
    settings: Settings
    llm: LLMClient
    dsm: DataSourceManager
    schema_index: SchemaIndex
    fewshots: FewshotStore
    glossary: str
    audit: AuditLog
    metrics: MetricStore | None = None
    glossaries: dict[str, str] | None = None   # 按数据源覆盖 glossary,缺省回落 glossary


def build_graph(deps: GraphDeps, checkpointer_path: str | None = None):
    s = deps.settings

    # ---------- 节点 ----------

    def rewrite(state: QAState) -> dict:
        """多轮改写(LLM)+ 重置本轮瞬态字段。"""
        question = state["question"]
        rewritten = question
        usage: list = []
        history = state.get("history") or []
        if history:
            hist_text = "\n".join(
                f"问:{h['question']}\n答:{h.get('answer', '')[:200]}" for h in history[-3:]
            )
            try:
                rewritten = deps.llm.chat(
                    prompts.SYSTEM_BASE,
                    prompts.REWRITE_TMPL.format(history=hist_text, question=question),
                    model=deps.llm.light_model, usage_out=usage,
                ).strip() or question
            except Exception:
                rewritten = question
        ds = deps.dsm.get(state["datasource"])
        return {
            "rewritten_question": rewritten,
            "dialect": ds.dialect,
            "sql": "", "explanation": "", "clarify": None,
            "relevant_schemas": [], "fewshots": [],
            "sql_ok": False, "validate_msg": "", "sql_error": None,
            "metric_hit": "", "needs_confirm": False,
            "confirmed": bool(state.get("confirmed")),
            "retry_count": 0, "attempts": [], "llm_calls": usage,
            "result_rows": [], "truncated": False, "chart": None,
            "answer": "", "status": "", "audit_id": "",
            "_start_ts": time.time(),
        }

    def match_metric(state: QAState) -> dict:
        """指标语义层/输出干预:命中即确定性直出,跳过 LLM 生成(竞品调研 §4)。"""
        if deps.metrics is None:
            return {"metric_hit": ""}
        hit = deps.metrics.match(state["datasource"], state["rewritten_question"])
        if hit is None:
            return {"metric_hit": ""}
        return {
            "metric_hit": hit.name,
            "sql": hit.sql.strip(),
            "explanation": f"命中{'输出干预' if hit.kind == 'override' else '指标模板'}:{hit.name}",
        }

    def recall_schema(state: QAState) -> dict:
        return {"relevant_schemas": deps.schema_index.recall(
            state["datasource"], state["rewritten_question"], topk=s.schema_topk)}

    def recall_fewshot(state: QAState) -> dict:
        return {"fewshots": deps.fewshots.search(
            state["datasource"], state["rewritten_question"], topk=s.fewshot_topk)}

    def _fewshot_text(fewshots: list[dict]) -> str:
        if not fewshots:
            return "(无)"
        return "\n\n".join(f"问题:{fs['question']}\nSQL:{fs['sql']}" for fs in fewshots)

    def generate(state: QAState) -> dict:
        ds = deps.dsm.get(state["datasource"])
        usage: list = []
        out = deps.llm.chat_json(
            prompts.SYSTEM_BASE,
            prompts.GENERATE_TMPL.format(
                today=datetime.date.today().isoformat(),
                dialect_hint=ds.profile["prompt_hint"],
                schemas="\n\n".join(state["relevant_schemas"]) or "(无)",
                fewshots=_fewshot_text(state["fewshots"]),
                glossary=(deps.glossaries or {}).get(
                    state["datasource"], deps.glossary) or "(无)",
                question=state["rewritten_question"],
            ),
            usage_out=usage,
        )
        return {
            "sql": (out.get("sql") or "").strip(),
            "explanation": out.get("explanation") or "",
            "clarify": out.get("clarify") or None,
            "llm_calls": state.get("llm_calls", []) + usage,
        }

    def clarify_end(state: QAState) -> dict:
        return {"answer": state["clarify"], "status": "clarify"}

    def confirm_end(state: QAState) -> dict:
        return {
            "status": "needs_confirm",
            "answer": (
                "⚠️ 该查询将在无过滤条件下扫描大表,可能耗时较长。\n\n"
                f"```sql\n{state['sql']}\n```\n\n"
                "点击\"确认执行\"按钮,或直接回复\"确认\"继续执行。"
            ),
        }

    def validate(state: QAState) -> dict:
        ds = deps.dsm.get(state["datasource"])
        result = validate_sql(
            state["sql"], ds.profile["sqlglot_dialect"], ds.allowed_tables,
            s.max_rows, sensitive_columns=ds.sensitive_columns,
            row_filters=ds.row_filters)
        delta: dict = {"sql_ok": result.ok, "validate_msg": result.reason}
        if result.ok:
            delta["sql"] = result.sql
            # HITL(设计文档 §7):大表无过滤条件 → 转人工确认
            risky = bool(result.tables & ds.large_tables) and not result.has_where
            delta["needs_confirm"] = risky and not state.get("confirmed")
        else:
            delta["sql_error"] = f"安全校验拒绝:{result.reason}"
            delta["attempts"] = state.get("attempts", []) + [
                {"sql": state["sql"], "error": delta["sql_error"]}]
            delta["retry_count"] = state.get("retry_count", 0) + 1
        return delta

    def execute(state: QAState) -> dict:
        try:
            rows, truncated = deps.dsm.run_query(
                state["datasource"], state["sql"], s.max_rows,
                timeout_s=s.query_timeout_s)
            return {"result_rows": rows, "truncated": truncated, "sql_error": None}
        except Exception as e:
            return {
                "sql_error": str(e),
                "attempts": state.get("attempts", []) + [
                    {"sql": state["sql"], "error": str(e)}],
                "retry_count": state.get("retry_count", 0) + 1,
            }

    def fix(state: QAState) -> dict:
        """携带完整报错重写(设计文档 §4.5)。"""
        ds = deps.dsm.get(state["datasource"])
        usage: list = []
        out = deps.llm.chat_json(
            prompts.SYSTEM_BASE,
            prompts.FIX_TMPL.format(
                today=datetime.date.today().isoformat(),
                dialect_hint=ds.profile["prompt_hint"],
                error_hints=ds.profile.get("error_hints", "(无)"),
                schemas="\n\n".join(state["relevant_schemas"]) or "(无)",
                sql=state["sql"],
                error=state["sql_error"],
                question=state["rewritten_question"],
            ),
            usage_out=usage,
        )
        return {"sql": (out.get("sql") or "").strip(),
                "explanation": out.get("explanation") or "",
                "clarify": out.get("clarify") or None,
                "llm_calls": state.get("llm_calls", []) + usage}

    def summarize(state: QAState) -> dict:
        rows = state.get("result_rows") or []
        usage: list = []
        if state.get("sql_error"):
            status = "failed"
            answer = (
                "未能生成有效查询,请尝试换一种问法或联系管理员。\n"
                f"最后一次SQL:{state.get('sql', '')}\n最后一次错误:{state['sql_error']}"
            )
        else:
            status = "success"
            shown = rows[: s.summarize_row_limit]
            truncated_note = ",已截断" if (state.get("truncated") or len(rows) > len(shown)) else ""
            try:
                answer = deps.llm.chat(
                    prompts.SYSTEM_BASE,
                    prompts.SUMMARIZE_TMPL.format(
                        question=state["rewritten_question"],
                        sql=state["sql"],
                        row_count=len(rows),
                        truncated_note=truncated_note,
                        rows=json.dumps(shown, ensure_ascii=False),
                    ),
                    model=deps.llm.light_model, usage_out=usage,
                )
            except Exception as e:
                answer = f"查询成功(共{len(rows)}行),但结果总结失败:{e}"
        return {"answer": answer, "status": status,
                "llm_calls": state.get("llm_calls", []) + usage}

    def suggest_chart(state: QAState) -> dict:
        """图表建议(确定性规则,设计文档 §4.6)。"""
        if state.get("sql_error") or not state.get("result_rows"):
            return {"chart": None}
        return {"chart": chart_rules.suggest_chart(
            state["rewritten_question"], state["result_rows"])}

    def finalize(state: QAState) -> dict:
        """审计落库 + 写入多轮历史。"""
        start_ts = state.get("_start_ts") or time.time()
        audit_id = deps.audit.record(
            session_id=state.get("session_id", ""),
            datasource=state["datasource"],
            question=state["question"],
            rewritten_question=state.get("rewritten_question", ""),
            final_sql=state.get("sql", ""),
            attempts=state.get("attempts", []),
            row_count=len(state.get("result_rows") or []),
            duration_ms=int((time.time() - start_ts) * 1000),
            status=state.get("status", ""),
            answer=state.get("answer", ""),
            llm_calls=state.get("llm_calls", []),
        )
        return {
            "audit_id": audit_id,
            "history": [{
                "question": state["question"],
                "rewritten_question": state.get("rewritten_question", ""),
                "sql": state.get("sql", ""),
                "answer": state.get("answer", ""),
            }],
        }

    # ---------- 编排 ----------

    g = StateGraph(QAState)
    g.add_node("rewrite", rewrite)
    g.add_node("match_metric", match_metric)
    g.add_node("recall_schema", recall_schema)
    g.add_node("recall_fewshot", recall_fewshot)
    g.add_node("generate", generate)
    g.add_node("clarify_end", clarify_end)
    g.add_node("confirm_end", confirm_end)
    g.add_node("validate", validate)
    g.add_node("execute", execute)
    g.add_node("fix", fix)
    g.add_node("summarize", summarize)
    g.add_node("suggest_chart", suggest_chart)
    g.add_node("finalize", finalize)

    g.set_entry_point("rewrite")
    g.add_edge("rewrite", "match_metric")
    g.add_conditional_edges(
        "match_metric",
        lambda st: "validate" if st.get("metric_hit") else "recall_schema",
    )
    g.add_edge("recall_schema", "recall_fewshot")
    g.add_edge("recall_fewshot", "generate")
    g.add_conditional_edges(
        "generate",
        lambda st: "clarify_end" if st.get("clarify") else "validate",
    )
    g.add_conditional_edges(
        "validate",
        lambda st: ("confirm_end" if st.get("needs_confirm") else "execute")
        if st["sql_ok"]
        else ("fix" if st.get("retry_count", 0) < s.max_retries else "summarize"),
    )
    g.add_conditional_edges(
        "execute",
        lambda st: "summarize" if not st.get("sql_error")
        else ("fix" if st.get("retry_count", 0) < s.max_retries else "summarize"),
    )
    g.add_conditional_edges(
        "fix",
        lambda st: "clarify_end" if st.get("clarify") else "validate",
    )
    g.add_edge("summarize", "suggest_chart")
    g.add_edge("suggest_chart", "finalize")
    g.add_edge("clarify_end", "finalize")
    g.add_edge("confirm_end", "finalize")
    g.add_edge("finalize", END)

    checkpointer = None
    if checkpointer_path:
        conn = sqlite3.connect(checkpointer_path, check_same_thread=False)
        checkpointer = SqliteSaver(conn)
    return g.compile(checkpointer=checkpointer)
