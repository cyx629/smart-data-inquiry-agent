"""数据源管理测试:健康检查、行数截断、JSON 序列化、超时设置不阻断。"""
import pytest

from app.datasource.dialects import DIALECT_PROFILES


def test_healthcheck(base_deps):
    assert base_deps.dsm.healthcheck("demo") is True


def test_run_query_truncation(base_deps):
    rows, truncated = base_deps.dsm.run_query("demo", "SELECT * FROM orders", max_rows=10)
    assert len(rows) == 10
    assert truncated is True


def test_run_query_no_truncation(base_deps):
    rows, truncated = base_deps.dsm.run_query(
        "demo", "SELECT COUNT(*) AS n FROM orders", max_rows=10)
    assert len(rows) == 1
    assert truncated is False
    assert rows[0]["n"] == 20000


def test_run_query_values_jsonable(base_deps):
    rows, _ = base_deps.dsm.run_query(
        "demo", "SELECT name, annual_purchase, created_at FROM customers", max_rows=3)
    import json
    json.dumps(rows, ensure_ascii=False)   # 不抛异常即通过


def test_unknown_datasource(base_deps):
    with pytest.raises(KeyError):
        base_deps.dsm.get("nope")


def test_all_profiles_complete():
    required = {"sqlalchemy_uri_tpl", "sqlglot_dialect", "prompt_hint",
                "healthcheck", "row_cap_suffix", "error_hints"}
    for name, profile in DIALECT_PROFILES.items():
        missing = required - set(profile)
        assert not missing, f"{name} 缺少配置项: {missing}"
        assert "timeout_sql" in profile, f"{name} 需显式声明 timeout_sql(可为 None)"
