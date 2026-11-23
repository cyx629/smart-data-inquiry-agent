"""SQL 安全校验测试(设计文档 §11.1 越权类:拦截率要求 100%)。"""
from app.security.validator import validate_sql

ALLOWED = {"orders", "customers", "products", "order_items"}


def test_allow_simple_select():
    r = validate_sql("SELECT * FROM orders", "mysql", ALLOWED, max_rows=100)
    assert r.ok
    assert "LIMIT 100" in r.sql.upper()


def test_keep_existing_limit():
    r = validate_sql("SELECT * FROM orders LIMIT 5", "mysql", ALLOWED)
    assert r.ok
    assert "LIMIT 5" in r.sql.upper()


def test_reject_update():
    assert not validate_sql("UPDATE orders SET amount=0", "mysql", ALLOWED).ok


def test_reject_delete():
    assert not validate_sql("DELETE FROM orders", "mysql", ALLOWED).ok


def test_reject_drop():
    assert not validate_sql("DROP TABLE orders", "mysql", ALLOWED).ok


def test_reject_multi_statement():
    r = validate_sql("SELECT 1; DROP TABLE orders", "mysql", ALLOWED)
    assert not r.ok


def test_reject_unauthorized_table():
    r = validate_sql("SELECT * FROM mysql.user", "mysql", ALLOWED)
    assert not r.ok
    assert "越权" in r.reason


def test_allow_join_and_cte():
    sql = """
    WITH t AS (SELECT customer_id, SUM(amount) s FROM orders GROUP BY customer_id)
    SELECT c.name, t.s FROM t JOIN customers c ON c.id = t.customer_id ORDER BY t.s DESC
    """
    r = validate_sql(sql, "mysql", ALLOWED)
    assert r.ok, r.reason


def test_reject_insert_via_cte_text():
    assert not validate_sql("INSERT INTO orders VALUES (1)", "mysql", ALLOWED).ok


def test_union_allowed_with_whitelist():
    r = validate_sql(
        "SELECT name FROM customers UNION SELECT name FROM products", "mysql", ALLOWED)
    assert r.ok


def test_empty_sql_rejected():
    assert not validate_sql("", "mysql", ALLOWED).ok


# ---------- 敏感列拦截 ----------

SENSITIVE = {"customers.contact_phone"}


def test_sensitive_column_direct():
    r = validate_sql("SELECT contact_phone FROM customers", "sqlite", ALLOWED,
                     sensitive_columns=SENSITIVE)
    assert not r.ok and "敏感列" in r.reason


def test_sensitive_column_aliased():
    r = validate_sql("SELECT c.contact_phone AS 手机号 FROM customers c", "sqlite",
                     ALLOWED, sensitive_columns=SENSITIVE)
    assert not r.ok


def test_sensitive_star_on_table():
    r = validate_sql("SELECT * FROM customers", "sqlite", ALLOWED,
                     sensitive_columns=SENSITIVE)
    assert not r.ok and "SELECT *" in r.reason


def test_sensitive_star_on_other_table_ok():
    r = validate_sql("SELECT * FROM products", "sqlite", ALLOWED,
                     sensitive_columns=SENSITIVE)
    assert r.ok


def test_sensitive_in_subquery_blocked():
    r = validate_sql(
        "SELECT name FROM customers WHERE contact_phone IS NOT NULL",
        "sqlite", ALLOWED, sensitive_columns=SENSITIVE)
    assert not r.ok


# ---------- 更多攻击面 ----------

def test_reject_intersect_out_of_whitelist():
    r = validate_sql(
        "SELECT name FROM customers INTERSECT SELECT name FROM secret_table",
        "mysql", ALLOWED)
    assert not r.ok


def test_comment_does_not_hide_write():
    r = validate_sql("SELECT 1 /* comment */; DROP TABLE orders", "mysql", ALLOWED)
    assert not r.ok


def test_reject_into_outfile():
    r = validate_sql(
        "SELECT * FROM orders INTO OUTFILE '/tmp/x'", "mysql", ALLOWED)
    assert not r.ok


def test_oracle_dialect_limit_injection():
    # 达梦/OB Oracle 模式走 oracle 方言,LIMIT 注入应产出 FETCH 风格且可通过
    r = validate_sql("SELECT name FROM customers", "oracle", ALLOWED, max_rows=50)
    assert r.ok
    assert "50" in r.sql


def test_unparseable_write_blocked_by_fallback():
    # 构造 sqlglot 也能识别的写语句在 fallback 场景(带怪异语法)仍应拦截
    r = validate_sql("DELETE FROM orders WHERE @@weird_syntax !!", "mysql", ALLOWED)
    assert not r.ok


def test_count_star_not_blocked_by_sensitive():
    # 回归:COUNT(*) 的星号不是投影星号,不应触发敏感表拦截
    r = validate_sql(
        "SELECT region, COUNT(*) FROM customers GROUP BY region", "sqlite",
        ALLOWED, sensitive_columns=SENSITIVE)
    assert r.ok, r.reason
