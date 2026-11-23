"""SQL 安全校验(设计文档 §7):sqlglot AST 校验 + LIMIT 注入 + 敏感列拦截。

规则:仅单条 SELECT;禁止 DDL/DML/DCL 与 INTO OUTFILE;引用表必须在白名单;
禁止查询敏感列(含对敏感表 SELECT *);无 LIMIT 自动注入。
sqlglot 解析失败时(达梦等非 100% 覆盖)降级为关键词黑名单兜底,
只告警不拦截,交由执行层与只读账号兜底。
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import sqlglot
from sqlglot import exp

# AST 中出现即拒绝的节点类型
_FORBIDDEN_NODES = [
    exp.Insert, exp.Update, exp.Delete, exp.Merge,
    exp.Create, exp.Drop, exp.Alter,
    exp.Grant, exp.Command, exp.Transaction, exp.Set,
]
if hasattr(exp, "TruncateTable"):
    _FORBIDDEN_NODES.append(exp.TruncateTable)

# 解析失败时的正则兜底黑名单
_FALLBACK_BLACKLIST = re.compile(
    r"\b(insert|update|delete|drop|create|alter|truncate|grant|revoke|merge|call|exec|execute"
    r"|into\s+outfile|into\s+dumpfile|load_file)\b",
    re.IGNORECASE,
)


from dataclasses import field as _field


@dataclass
class ValidationResult:
    ok: bool
    reason: str
    sql: str          # 校验/改写后的最终 SQL(注入 LIMIT / 行级过滤后)
    warning: str = ""
    tables: set[str] = _field(default_factory=set)   # 引用的物理表(供风险评估)
    has_where: bool = False                          # 是否含 WHERE(供 HITL 全表扫描判定)


def _strip_strings_and_comments(sql: str) -> str:
    sql = re.sub(r"'(?:[^']|'')*'", "''", sql)
    sql = re.sub(r'"(?:[^"])*"', '""', sql)
    sql = re.sub(r"--[^\n]*", "", sql)
    sql = re.sub(r"/\*.*?\*/", "", sql, flags=re.DOTALL)
    return sql


def _fallback_check(sql: str, parse_err: str) -> ValidationResult:
    body = _strip_strings_and_comments(sql)
    if ";" in body.rstrip().rstrip(";"):
        return ValidationResult(False, "仅允许单条SELECT语句", sql)
    head = body.lstrip().split(None, 1)
    if not head or head[0].lower() not in ("select", "with"):
        return ValidationResult(False, "仅允许SELECT查询", sql)
    if m := _FALLBACK_BLACKLIST.search(body):
        return ValidationResult(False, f"包含禁止的关键词: {m.group(0)}", sql)
    return ValidationResult(True, "OK", sql, warning=f"PARSE_WARN:{parse_err}")


def _referenced_tables(stmt: exp.Expression) -> set[str]:
    """真实引用的物理表(排除 CTE 名)。"""
    cte_names = {cte.alias_or_name.lower() for cte in stmt.find_all(exp.CTE)}
    return {
        t.name.lower()
        for t in stmt.find_all(exp.Table)
        if t.name and t.name.lower() not in cte_names
    }


def _has_row_limit(stmt: exp.Expression) -> bool:
    return bool(stmt.args.get("limit") or stmt.args.get("fetch"))


# 兼容 sqlglot 版本差异:新版 Except/Intersect 基类为 SetOperation,旧版为 Union
_QUERY_TYPES = (exp.Select, getattr(exp, "SetOperation", exp.Union), exp.Union)


def _has_projection_star(stmt: exp.Expression) -> bool:
    """是否存在投影位置的星号(SELECT * / t.*)。COUNT(*) 等聚合内的星号不算。"""
    for select in stmt.find_all(exp.Select):
        for e in select.expressions:
            if isinstance(e, exp.Star):
                return True
            if isinstance(e, exp.Column) and isinstance(e.this, exp.Star):
                return True
    return False


def _check_sensitive(stmt: exp.Expression, sensitive: set[str],
                     tables: set[str]) -> str | None:
    """敏感列拦截:命中列名或对含敏感列的表 SELECT * 均拒绝。返回拒绝原因或 None。

    sensitive 元素为 "table.column"(小写);列名匹配不要求带表前缀
    (别名/子查询下无法可靠解析归属表,按列名从严拦截)。
    """
    if not sensitive:
        return None
    sensitive_cols = {s.split(".")[-1] for s in sensitive}
    sensitive_tables = {s.split(".")[0] for s in sensitive if "." in s}
    for col in stmt.find_all(exp.Column):
        if col.name and col.name.lower() in sensitive_cols:
            return f"敏感列禁止查询: {col.name}"
    if _has_projection_star(stmt) and tables & sensitive_tables:
        return f"含敏感列的表禁止 SELECT *: {sorted(tables & sensitive_tables)}"
    return None


def _apply_row_filters(stmt: exp.Expression, row_filters: dict[str, str],
                       dialect: str) -> exp.Expression:
    """行级权限:对引用了受控表的每个 SELECT 作用域追加 AND 过滤条件。

    row_filters:{表名: 条件模板},模板中 {t} 会替换为该表在查询中的别名。
    例:{"orders": "{t}.region = '华东'"}
    """
    for table in list(stmt.find_all(exp.Table)):
        tpl = row_filters.get(table.name.lower())
        if not tpl:
            continue
        select = table.find_ancestor(exp.Select)
        if select is None:
            continue
        cond = sqlglot.parse_one(tpl.format(t=table.alias_or_name), read=dialect)
        select.where(cond, copy=False)
    return stmt


def validate_sql(
    sql: str,
    sqlglot_dialect: str,
    allowed_tables: set[str],
    max_rows: int = 100,
    sensitive_columns: set[str] | None = None,
    row_filters: dict[str, str] | None = None,
) -> ValidationResult:
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        return ValidationResult(False, "SQL为空", sql)

    try:
        stmts = sqlglot.parse(sql, read=sqlglot_dialect)
    except Exception as e:  # 解析失败仅兜底告警(设计文档 §5.2 / §7)
        return _fallback_check(sql, str(e))

    stmts = [s for s in stmts if s is not None]
    if len(stmts) != 1:
        return ValidationResult(False, "仅允许单条SELECT语句", sql)
    stmt = stmts[0]

    if not isinstance(stmt, _QUERY_TYPES):
        return ValidationResult(False, "仅允许SELECT查询", sql)

    for node_type in _FORBIDDEN_NODES:
        if list(stmt.find_all(node_type)):
            return ValidationResult(False, f"包含禁止的操作: {node_type.__name__}", sql)

    if stmt.args.get("into"):  # SELECT ... INTO OUTFILE / 变量
        return ValidationResult(False, "禁止 SELECT INTO", sql)

    tables = _referenced_tables(stmt)
    if allowed_tables:
        if illegal := tables - {t.lower() for t in allowed_tables}:
            return ValidationResult(False, f"越权表: {sorted(illegal)}", sql)

    if reason := _check_sensitive(stmt, sensitive_columns or set(), tables):
        return ValidationResult(False, reason, sql)

    warning = ""
    if row_filters:
        try:
            stmt = _apply_row_filters(stmt, row_filters, sqlglot_dialect)
        except Exception as e:
            return ValidationResult(False, f"行级权限注入失败:{e}", sql)

    if not _has_row_limit(stmt):
        try:
            stmt = stmt.limit(max_rows)
        except Exception as e:
            warning = f"LIMIT注入失败:{e}"

    has_where = bool(list(stmt.find_all(exp.Where)))
    try:
        final_sql = stmt.sql(dialect=sqlglot_dialect)
    except Exception:
        final_sql = sql
    return ValidationResult(True, "OK", final_sql, warning=warning,
                            tables=tables, has_where=has_where)
