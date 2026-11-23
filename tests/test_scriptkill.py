"""剧本杀数据源专项:增强 schema、指标快车道、HITL、敏感列与评测集有效性。"""
import re

import yaml

from app.core.graph import build_graph
from app.security.validator import validate_sql
from app.semantic.metadata import build_table_docs
from tests.conftest import FakeLLM, build_test_deps

TABLES = {"stores", "staff", "scripts", "script_licenses", "players",
          "sessions", "session_players", "revenue_targets"}


def test_scriptkill_docs_cover_tables_and_semantics(base_deps):
    docs = {d.table: d for d in build_table_docs(base_deps.dsm.get("scriptkill"))}
    assert set(docs) == TABLES
    sess = docs["sessions"].text
    assert "场次表" in sess
    assert "已完成" in sess
    assert "total_amount" in sess
    assert "没有分配DM" in docs["sessions"].text or "未分配DM" in docs["sessions"].text
    assert "session_players" in docs["sessions"].related_tables


def test_scriptkill_sensitive_phone_masked(base_deps):
    docs = {d.table: d for d in build_table_docs(base_deps.dsm.get("scriptkill"))}
    for table in ("players", "staff"):
        text = docs[table].text
        assert "敏感列" in text
        sample = text.split("样例数据")[-1]
        assert not re.search(r"1[3589]\d{9}", sample)


def test_scriptkill_monthly_metric_hit_skips_llm():
    llm = FakeLLM(["SELECT 'should_not_be_used'"])
    graph = build_graph(build_test_deps(llm), checkpointer_path=None)
    out = graph.invoke({"question": "2026年6月的营收是多少?",
                        "datasource": "scriptkill", "session_id": "sk1"})
    assert out["status"] == "success"
    assert out["metric_hit"] == "月度营收"
    assert "'2026-06'" in out["sql"]
    assert llm._sqls, "命中指标模板时不应消费 LLM 生成"


def test_scriptkill_store_metric_hit():
    llm = FakeLLM(["SELECT 'should_not_be_used'"])
    graph = build_graph(build_test_deps(llm), checkpointer_path=None)
    out = graph.invoke({"question": "2026年6月花山万达店的营收",
                        "datasource": "scriptkill", "session_id": "sk2"})
    assert out["status"] == "success"
    assert out["metric_hit"] == "月度门店营收"
    assert "花山万达店" in out["sql"] and "'2026-06'" in out["sql"]


def test_scriptkill_large_table_needs_confirm():
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT * FROM sessions"])), checkpointer_path=None)
    out = graph.invoke({"question": "列出所有场次",
                        "datasource": "scriptkill", "session_id": "sk3"})
    assert out["status"] == "needs_confirm"
    assert not out.get("result_rows")


def test_scriptkill_sensitive_column_blocked():
    # 每次重试仍生成敏感列,耗尽重试后应失败且不返回数据
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT nickname, phone FROM players"] * 5)), checkpointer_path=None)
    out = graph.invoke({"question": "玩家手机号",
                        "datasource": "scriptkill", "session_id": "sk4"})
    assert out["status"] == "failed"
    assert not out.get("result_rows")


def test_scriptkill_glossary_isolated(base_deps):
    assert "营收/营业额" in base_deps.glossaries["scriptkill"]
    assert "大客户" in base_deps.glossaries["demo"]
    assert "大客户" not in base_deps.glossaries["scriptkill"]


def test_eval_scriptkill_cases_executable(base_deps):
    with open("eval/eval_scriptkill.yaml", encoding="utf-8") as f:
        cases = yaml.safe_load(f)["cases"]
    assert len(cases) == 68
    assert len([c for c in cases if c["id"].startswith("sec_")]) == 9
    ds = base_deps.dsm.get("scriptkill")
    s = base_deps.settings
    for case in cases:
        if case["expect"] != "success":
            continue
        result = validate_sql(case["gold_sql"], ds.profile["sqlglot_dialect"],
                              ds.allowed_tables, s.max_rows,
                              sensitive_columns=ds.sensitive_columns,
                              row_filters=ds.row_filters)
        assert result.ok, f"{case['id']}: {result.reason}"
        rows, _ = base_deps.dsm.run_query(
            "scriptkill", case["gold_sql"], max_rows=s.max_rows)
        assert rows, f"{case['id']} gold_sql 返回空结果"
