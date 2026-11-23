"""主图端到端测试:FakeLLM 离线跑通 生成→校验→执行→修正→总结→图表 全链路。"""
import dataclasses
import os

from app.core.graph import build_graph
from tests.conftest import FakeLLM, build_test_deps


def _graph_with(llm, checkpointer_path=None):
    deps = build_test_deps(llm)
    return build_graph(deps, checkpointer_path=checkpointer_path), deps


def test_happy_path():
    graph, _ = _graph_with(FakeLLM(
        ["SELECT region AS 地区, COUNT(*) AS 客户数 FROM customers GROUP BY region"]))
    out = graph.invoke({"question": "各地区客户数", "datasource": "demo", "session_id": "t1"})
    assert out["status"] == "success"
    assert out["result_rows"]
    assert out["audit_id"]
    assert "LIMIT" in out["sql"].upper()
    assert out["chart"] and out["chart"]["series"][0]["type"] == "bar"


def test_error_then_fix():
    graph, _ = _graph_with(FakeLLM([
        "SELECT no_such_col FROM customers",
        "SELECT name AS 客户名称 FROM customers",
    ]))
    out = graph.invoke({"question": "客户名单", "datasource": "demo", "session_id": "t2"})
    assert out["status"] == "success"
    assert out["attempts"], "应记录失败尝试"
    assert out["result_rows"]


def test_dangerous_sql_blocked():
    graph, _ = _graph_with(FakeLLM(["DELETE FROM orders"] * 5))
    out = graph.invoke({"question": "删掉订单", "datasource": "demo", "session_id": "t3"})
    assert out["status"] == "failed"
    assert not out.get("result_rows")


def test_sensitive_column_blocked():
    graph, _ = _graph_with(FakeLLM(["SELECT contact_phone FROM customers"] * 5))
    out = graph.invoke({"question": "客户手机号", "datasource": "demo", "session_id": "t4"})
    assert out["status"] == "failed"
    assert not out.get("result_rows")
    assert any("敏感列" in a["error"] for a in out["attempts"])


def test_clarify_branch():
    graph, _ = _graph_with(FakeLLM(clarify="您想看哪个时间段的哪个指标?"))
    out = graph.invoke({"question": "销售情况怎么样", "datasource": "demo", "session_id": "t5"})
    assert out["status"] == "clarify"
    assert "指标" in out["answer"]
    assert out["audit_id"], "澄清也应落审计"


def test_multi_turn_history(tmp_path):
    """同一 session 两轮提问:第二轮 rewrite 节点应带上历史并改写。"""
    ckpt = os.path.join(tmp_path, "ckpt.db")
    llm = FakeLLM(
        ["SELECT COUNT(*) AS 客户数 FROM customers",
         "SELECT region AS 地区, COUNT(*) AS 客户数 FROM customers GROUP BY region"],
        rewrite_to="各地区的客户数量分别是多少?",
    )
    graph, _ = _graph_with(llm, checkpointer_path=ckpt)
    cfg = {"configurable": {"thread_id": "s1"}}
    out1 = graph.invoke({"question": "一共多少客户?", "datasource": "demo",
                         "session_id": "s1"}, cfg)
    assert out1["status"] == "success"
    out2 = graph.invoke({"question": "按地区拆分一下", "datasource": "demo",
                         "session_id": "s1"}, cfg)
    assert out2["status"] == "success"
    assert out2["rewritten_question"] == "各地区的客户数量分别是多少?"
    assert len(out2["history"]) == 2


def test_retry_exhaustion_reports_failure():
    graph, deps = _graph_with(FakeLLM(["SELECT bad_col FROM customers"] * 10))
    out = graph.invoke({"question": "坏查询", "datasource": "demo", "session_id": "t6"})
    assert out["status"] == "failed"
    assert len(out["attempts"]) == deps.settings.max_retries
    assert "未能生成有效查询" in out["answer"]


def test_audit_written(base_deps):
    graph, deps = _graph_with(FakeLLM(
        ["SELECT COUNT(*) AS 总数 FROM orders WHERE status = '已完成'"]))
    out = graph.invoke({"question": "已完成订单总数", "datasource": "demo",
                        "session_id": "t7"})
    record = deps.audit.get(out["audit_id"])
    assert record and record["status"] == "success"
    assert record["question"] == "已完成订单总数"
    assert record["final_sql"] == out["sql"]


def test_llm_calls_tracked():
    """LLM 调用(模型+token)应被记录到状态与审计。"""
    graph, deps = _graph_with(FakeLLM(
        ["SELECT region AS 地区, COUNT(*) AS 客户数 FROM customers GROUP BY region"]))
    out = graph.invoke({"question": "各地区客户数", "datasource": "demo",
                        "session_id": "t8"})
    calls = out["llm_calls"]
    assert len(calls) == 2, "生成 + 总结应各记一次调用"
    assert all(c["total_tokens"] > 0 for c in calls)
    import json as _json
    record = deps.audit.get(out["audit_id"])
    assert len(_json.loads(record["llm_calls"])) == 2


def test_metric_hit_zero_llm_calls():
    """命中指标模板时,除总结外不应有生成调用;干预/模板路径 llm_calls 仅含总结。"""
    graph, _ = _graph_with(FakeLLM(["SELECT 'unused'"]))
    out = graph.invoke({"question": "2026年6月的销售额是多少?",
                        "datasource": "demo", "session_id": "t9"})
    assert out["metric_hit"] == "月度销售额"
    models = [c for c in out["llm_calls"]]
    assert len(models) == 1, "仅总结节点一次调用(生成被跳过)"
