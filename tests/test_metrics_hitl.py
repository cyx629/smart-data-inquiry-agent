"""二期功能测试:指标语义层/输出干预、HITL 人工确认、行级权限。"""
from app.core.graph import build_graph
from app.security.validator import validate_sql
from app.semantic.metrics import MetricStore, normalize
from tests.conftest import FakeLLM, build_test_deps

ALLOWED = {"orders", "customers", "products", "order_items"}


# ---------- 指标层 / 输出干预 ----------

def test_normalize():
    assert normalize("客单价是多少??") == normalize("客单价 是 多少")


def test_override_exact_match():
    store = MetricStore()
    store.load("demo")
    hit = store.match("demo", "客单价是多少?")
    assert hit and hit.kind == "override"
    assert "客单价" in hit.sql


def test_metric_template_with_params():
    store = MetricStore()
    store.load("demo")
    hit = store.match("demo", "2026年6月的销售额是多少?")
    assert hit and hit.kind == "metric"
    assert "'2026-06'" in hit.sql


def test_metric_template_region_variant():
    store = MetricStore()
    store.load("demo")
    hit = store.match("demo", "2026年6月华东大区的销售额是多少?")
    assert hit and hit.name == "月度大区销售额"
    assert "华东" in hit.sql and "'2026-06'" in hit.sql


def test_no_match_falls_through():
    store = MetricStore()
    store.load("demo")
    assert store.match("demo", "销量最高的产品是哪个?") is None


def test_metric_hit_skips_llm():
    """命中指标模板时不应调用 LLM 生成(确定性直出)。"""
    llm = FakeLLM(["SELECT 'should_not_be_used'"])
    deps = build_test_deps(llm)
    graph = build_graph(deps, checkpointer_path=None)
    out = graph.invoke({"question": "2026年6月的销售额是多少?",
                        "datasource": "demo", "session_id": "m1"})
    assert out["status"] == "success"
    assert out["metric_hit"] == "月度销售额"
    assert "'2026-06'" in out["sql"]
    assert llm._sqls, "LLM 生成不应被消费"


# ---------- HITL 人工确认 ----------

def test_large_table_no_filter_needs_confirm():
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT * FROM order_items"])), checkpointer_path=None)
    out = graph.invoke({"question": "列出所有订单明细",
                        "datasource": "demo", "session_id": "h1"})
    assert out["status"] == "needs_confirm"
    assert not out.get("result_rows")
    assert "确认" in out["answer"]


def test_confirmed_executes():
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT * FROM order_items"])), checkpointer_path=None)
    out = graph.invoke({"question": "列出所有订单明细", "confirmed": True,
                        "datasource": "demo", "session_id": "h2"})
    assert out["status"] == "success"
    assert out["result_rows"]


def test_filtered_query_on_large_table_no_confirm():
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT COUNT(*) AS n FROM orders WHERE status = '已完成'"])),
        checkpointer_path=None)
    out = graph.invoke({"question": "已完成订单数",
                        "datasource": "demo", "session_id": "h3"})
    assert out["status"] == "success"


def test_small_table_no_confirm():
    graph = build_graph(build_test_deps(
        FakeLLM(["SELECT name FROM products"])), checkpointer_path=None)
    out = graph.invoke({"question": "产品列表",
                        "datasource": "demo", "session_id": "h4"})
    assert out["status"] == "success"


# ---------- 行级权限 ----------

def test_row_filter_injected():
    r = validate_sql("SELECT COUNT(*) FROM orders", "sqlite", ALLOWED,
                     row_filters={"orders": "{t}.status != '已取消'"})
    assert r.ok
    assert "已取消" in r.sql
    assert r.has_where


def test_row_filter_uses_alias():
    r = validate_sql(
        "SELECT o.id FROM orders o JOIN customers c ON o.customer_id = c.id "
        "WHERE c.region = '华东'",
        "sqlite", ALLOWED, row_filters={"orders": "{t}.status != '已取消'"})
    assert r.ok
    assert "o.status <> '已取消'" in r.sql or "o.status != '已取消'" in r.sql


def test_row_filter_appends_to_existing_where():
    r = validate_sql("SELECT id FROM orders WHERE amount > 100", "sqlite", ALLOWED,
                     row_filters={"orders": "{t}.status != '已取消'"})
    assert r.ok
    assert "amount > 100" in r.sql and "已取消" in r.sql


def test_row_filter_untouched_table():
    r = validate_sql("SELECT name FROM products", "sqlite", ALLOWED,
                     row_filters={"orders": "{t}.status != '已取消'"})
    assert r.ok
    assert "已取消" not in r.sql
