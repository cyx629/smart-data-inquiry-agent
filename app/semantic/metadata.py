"""元数据同步(设计文档 §4.2):反射表结构 + 人工语义映射 + 枚举值 + 样例行,
产出按表分块的"增强 schema 文档"。

语义映射文件(YAML)是准确率的生命线:对无 COMMENT 的表/字段补中文语义。
"""
from __future__ import annotations

from dataclasses import dataclass, field

import yaml
from sqlalchemy import inspect, text

from app.datasource.manager import DataSource, _jsonable


@dataclass
class TableDoc:
    table: str
    text: str                       # 注入 prompt 的增强 schema 文本
    related_tables: list[str] = field(default_factory=list)   # 外键关联表(召回时自动补齐)


def _load_semantics(path: str | None) -> dict:
    if not path:
        return {}
    try:
        with open(path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def _cap(ds: DataSource, sql: str, n: int) -> str:
    suffix = ds.profile.get("row_cap_suffix", "LIMIT {n}")
    return f"{sql} {suffix.format(n=n)}"


def _sample_rows(ds: DataSource, table: str, n: int = 3) -> list[dict]:
    try:
        with ds.engine.connect() as conn:
            result = conn.execute(text(_cap(ds, f"SELECT * FROM {table}", n)))
            keys = list(result.keys())
            raw = result.fetchmany(n)
        return [{k: _jsonable(v) for k, v in zip(keys, row)} for row in raw]
    except Exception:
        return []


def _distinct_values(ds: DataSource, table: str, column: str, limit: int = 20) -> list:
    try:
        with ds.engine.connect() as conn:
            result = conn.execute(
                text(_cap(ds, f"SELECT DISTINCT {column} FROM {table}", limit + 1))
            )
            raw = result.fetchmany(limit + 1)
        if len(raw) > limit:      # 基数过高,不作为枚举
            return []
        return [_jsonable(r[0]) for r in raw if r[0] is not None]
    except Exception:
        return []


def build_table_docs(ds: DataSource) -> list[TableDoc]:
    """为一个数据源的白名单表生成增强 schema 文档(每表一块)。"""
    semantics = _load_semantics(ds.semantics_file)
    tables_sem = semantics.get("tables", {})
    inspector = inspect(ds.engine)
    available = {t.lower() for t in inspector.get_table_names()}
    targets = sorted(ds.allowed_tables & available) if ds.allowed_tables else sorted(available)

    docs: list[TableDoc] = []
    for table in targets:
        t_sem = tables_sem.get(table, {})
        col_sem = t_sem.get("columns", {})
        sensitive_cols = {
            s.split(".", 1)[1] for s in ds.sensitive_columns
            if "." in s and s.split(".", 1)[0] == table
        }
        lines = [f"表名: {table}"]
        if comment := t_sem.get("comment", ""):
            lines.append(f"表说明: {comment}")

        lines.append("字段:")
        for col in inspector.get_columns(table):
            name, coltype = col["name"], str(col["type"])
            c = col_sem.get(name, {})
            if isinstance(c, str):
                c = {"comment": c}
            desc = c.get("comment") or col.get("comment") or ""
            entry = f"  - {name} {coltype}"
            if desc:
                entry += f"  -- {desc}"
            if name.lower() in sensitive_cols:
                entry += "(敏感列,禁止出现在SQL中)"
            enum_cfg = c.get("enum")
            if enum_cfg == "auto":
                if values := _distinct_values(ds, table, name):
                    entry += f" 可选值: {values}"
            elif isinstance(enum_cfg, list) and enum_cfg:
                entry += f" 可选值: {enum_cfg}"
            lines.append(entry)

        try:
            pk = inspector.get_pk_constraint(table).get("constrained_columns") or []
        except Exception:
            pk = []
        if pk:
            lines.append(f"主键: {', '.join(pk)}")

        related: list[str] = []
        fk_lines = []
        try:
            fks = inspector.get_foreign_keys(table)
        except Exception:
            fks = []
        for fk in fks:
            ref = fk.get("referred_table", "")
            if ref:
                related.append(ref.lower())
                fk_lines.append(
                    f"  - {table}.{','.join(fk['constrained_columns'])} -> "
                    f"{ref}.{','.join(fk['referred_columns'])}"
                )
        # 语义文件可人工补充隐式关联(无外键约束的库很常见)
        for rel in t_sem.get("related", []) or []:
            if rel.lower() not in related:
                related.append(rel.lower())
                fk_lines.append(f"  - 关联表: {rel}(语义映射)")
        if fk_lines:
            lines.append("关联关系:")
            lines.extend(fk_lines)

        if rows := _sample_rows(ds, table):
            if sensitive_cols:   # 样例行脱敏:敏感列值不进入 prompt
                rows = [
                    {k: ("***" if k.lower() in sensitive_cols else v) for k, v in r.items()}
                    for r in rows
                ]
            lines.append(f"样例数据(前{len(rows)}行): {rows}")

        docs.append(TableDoc(table=table, text="\n".join(lines), related_tables=related))
    return docs
