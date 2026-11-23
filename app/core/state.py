"""主图状态定义(设计文档 §3.2)。history 走累加 reducer 以支撑多轮追问。"""
from __future__ import annotations

import operator
from typing import Annotated, TypedDict


class QAState(TypedDict, total=False):
    # 输入
    question: str
    datasource: str
    session_id: str

    # 中间态
    rewritten_question: str
    dialect: str
    relevant_schemas: list[str]
    fewshots: list[dict]
    sql: str
    explanation: str
    clarify: str | None
    sql_ok: bool
    validate_msg: str
    metric_hit: str               # 命中的指标/干预名称(空串=未命中,走 LLM 生成)
    confirmed: bool               # 用户已确认执行高风险查询(HITL)
    needs_confirm: bool           # 校验判定需人工确认(大表无过滤)
    sql_error: str | None
    retry_count: int
    attempts: list[dict]          # [{sql, error}] 供审计与修正节点使用
    llm_calls: list[dict]         # 本轮各次 LLM 调用:[{model, prompt/completion/total_tokens}]

    # 输出
    result_rows: list[dict]
    truncated: bool
    chart: dict | None            # ECharts 配置(图表建议节点产出)
    answer: str
    status: str                   # success / failed / clarify / rejected
    audit_id: str
    _start_ts: float              # 本轮开始时间(用于审计耗时统计)

    # 多轮记忆(checkpointer 持久化,追加式)
    history: Annotated[list[dict], operator.add]
