"""FastAPI 服务(设计文档 §8):SSE 问数主接口 + 数据源 / 反馈 / 元数据同步 / 审计。"""
from __future__ import annotations

import json
import os
import uuid

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel

from app.audit.db import AuditLog
from app.core.graph import GraphDeps, build_graph
from app.datasource.manager import DataSourceManager
from app.llm.client import LLMClient, build_embedder
from app.semantic.metadata import build_table_docs
from app.semantic.metrics import MetricStore
from app.semantic.store import FewshotStore, SchemaIndex, load_glossaries, load_glossary
from app.settings import settings

app = FastAPI(title="智能问数系统", version="0.1.0")

_deps: GraphDeps | None = None
_graph = None


def _sync_metadata(deps: GraphDeps) -> dict[str, int]:
    """抽取各数据源元数据,重建增强 schema 索引与 few-shot 池。"""
    counts = {}
    for item in deps.dsm.list():
        name = item["name"]
        docs = build_table_docs(deps.dsm.get(name))
        deps.schema_index.load(name, docs)
        deps.fewshots.load(name)
        if deps.metrics is not None:
            deps.metrics.load(name)
        counts[name] = len(docs)
    return counts


@app.on_event("startup")
def startup() -> None:
    global _deps, _graph
    os.makedirs(settings.data_dir, exist_ok=True)
    embedder = build_embedder(settings)
    dsm = DataSourceManager(settings.datasources_file)
    glossaries = load_glossaries(
        [item["name"] for item in dsm.list()],
        settings.glossary_dir, settings.glossary_file)
    _deps = GraphDeps(
        settings=settings,
        llm=LLMClient(settings),
        dsm=dsm,
        schema_index=SchemaIndex(embedder, settings.full_schema_table_threshold),
        fewshots=FewshotStore(embedder, settings.data_dir, settings.fewshot_seed_dir),
        glossary=load_glossary(settings.glossary_file),
        audit=AuditLog(settings.data_dir),
        metrics=MetricStore(),
        glossaries=glossaries,
    )
    _sync_metadata(_deps)
    _graph = build_graph(
        _deps, checkpointer_path=os.path.join(settings.data_dir, "checkpoints.db"))


class ChatRequest(BaseModel):
    session_id: str | None = None
    datasource: str
    question: str
    confirmed: bool = False   # HITL:大表无过滤查询需二次确认后携带 true 重发


class FeedbackRequest(BaseModel):
    audit_id: str
    adopted: bool


_NODE_LABELS = {
    "rewrite": "理解问题",
    "match_metric": "匹配指标库",
    "recall_schema": "召回相关表结构",
    "recall_fewshot": "检索参考样例",
    "generate": "生成SQL",
    "validate": "安全校验",
    "execute": "执行查询",
    "fix": "修正SQL",
    "summarize": "总结结果",
    "suggest_chart": "生成图表建议",
}


def _sse(event: str, data) -> str:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n"


def _llm_summary(calls: list[dict]) -> str:
    if not calls:
        return "⚡ 本次未调用大模型(指标库/干预确定性直出)"
    models: dict[str, int] = {}
    for c in calls:
        models[c.get("model", "?")] = models.get(c.get("model", "?"), 0) + 1
    total = sum(c.get("total_tokens", 0) for c in calls)
    detail = ", ".join(f"{m}×{n}" for m, n in models.items())
    return f"🤖 大模型调用 {len(calls)} 次({detail},共 {total} tokens)"


@app.post("/api/v1/chat")
def chat(req: ChatRequest):
    """问数主接口,SSE 流式返回:thinking / sql / rows / answer / error / done。"""
    if _graph is None:
        raise HTTPException(503, "服务尚未就绪")
    try:
        _deps.dsm.get(req.datasource)
    except KeyError as e:
        raise HTTPException(404, str(e))

    session_id = req.session_id or uuid.uuid4().hex

    def stream():
        yield _sse("session", {"session_id": session_id})
        final: dict = {}
        try:
            config = {"configurable": {"thread_id": session_id}}
            inputs = {"question": req.question, "datasource": req.datasource,
                      "session_id": session_id, "confirmed": req.confirmed}
            for update in _graph.stream(inputs, config, stream_mode="updates"):
                for node, delta in update.items():
                    if node in _NODE_LABELS:
                        yield _sse("thinking", {"step": _NODE_LABELS[node]})
                    if not isinstance(delta, dict):
                        continue
                    final.update({k: v for k, v in delta.items() if v is not None})
                    if node in ("generate", "fix") and delta.get("sql"):
                        yield _sse("sql", {"sql": delta["sql"],
                                           "explanation": delta.get("explanation", "")})
                    if node == "execute" and delta.get("sql_error"):
                        yield _sse("thinking", {"step": f"执行失败,准备修正:{delta['sql_error'][:120]}"})
                    if node == "validate" and not delta.get("sql_ok", True):
                        yield _sse("thinking", {"step": f"校验拒绝:{delta.get('validate_msg', '')}"})
                    if node == "execute" and delta.get("result_rows") is not None:
                        yield _sse("rows", {"rows": delta["result_rows"],
                                            "truncated": delta.get("truncated", False)})
                    if node == "suggest_chart" and delta.get("chart"):
                        yield _sse("chart", {"option": delta["chart"]})
            yield _sse("answer", {
                "answer": final.get("answer", ""),
                "status": final.get("status", ""),
                "sql": final.get("sql", ""),
                "audit_id": final.get("audit_id", ""),
                "llm_summary": _llm_summary(final.get("llm_calls") or []),
            })
        except Exception as e:
            yield _sse("error", {"message": str(e)})
        yield _sse("done", {})

    return StreamingResponse(stream(), media_type="text/event-stream")


@app.get("/api/v1/datasources")
def datasources():
    return {"datasources": _deps.dsm.list()}


@app.post("/api/v1/feedback")
def feedback(req: FeedbackRequest):
    """采纳标注 → QA 对回灌 few-shot 样例库(准确率飞轮,设计文档 §6.3)。"""
    record = _deps.audit.get(req.audit_id)
    if not record:
        raise HTTPException(404, "审计记录不存在")
    _deps.audit.set_feedback(req.audit_id, "adopted" if req.adopted else "wrong")
    if req.adopted and record["final_sql"] and record["status"] == "success":
        _deps.fewshots.add(
            record["datasource"],
            record["rewritten_question"] or record["question"],
            record["final_sql"],
        )
        return {"ok": True, "message": "已采纳并回灌样例库"}
    return {"ok": True, "message": "已记录反馈"}


@app.post("/api/v1/meta/sync")
def meta_sync():
    return {"ok": True, "synced_tables": _sync_metadata(_deps)}


@app.get("/api/v1/audit")
def audit(limit: int = 50):
    return {"items": _deps.audit.query(limit)}


@app.get("/")
def index():
    return FileResponse(os.path.join(os.path.dirname(__file__), "static", "index.html"))
