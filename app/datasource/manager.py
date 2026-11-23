"""数据源管理(设计文档 §4.1):配置化注册、SQLAlchemy 连接池、健康检查、只读执行。"""
from __future__ import annotations

import datetime
import decimal
from dataclasses import dataclass, field
from typing import Any

import yaml
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine

from app.datasource.dialects import DIALECT_PROFILES


@dataclass
class DataSource:
    name: str
    dialect: str                 # mysql / oceanbase / dm / kingbase / sqlite
    engine: Engine
    profile: dict
    allowed_tables: set[str]
    sensitive_columns: set[str] = field(default_factory=set)   # 敏感列(table.column 小写)
    row_filters: dict = field(default_factory=dict)            # 行级权限:{表名: 条件模板}
    large_tables: set[str] = field(default_factory=set)        # 大表(无过滤条件时触发 HITL 确认)
    semantics_file: str | None = None
    description: str = ""
    extra: dict = field(default_factory=dict)


def _jsonable(v: Any) -> Any:
    if isinstance(v, (datetime.date, datetime.datetime, datetime.time)):
        return v.isoformat()
    if isinstance(v, decimal.Decimal):
        return float(v)
    if isinstance(v, bytes):
        return v.decode("utf-8", errors="replace")
    return v


class DataSourceManager:
    def __init__(self, config_file: str):
        with open(config_file, encoding="utf-8") as f:
            cfg = yaml.safe_load(f)
        self._sources: dict[str, DataSource] = {}
        for name, item in (cfg.get("datasources") or {}).items():
            dialect = item["dialect"]
            profile = DIALECT_PROFILES[dialect]
            uri = profile["sqlalchemy_uri_tpl"].format(**item["conn"])
            engine = create_engine(uri, pool_pre_ping=True, pool_recycle=3600)
            self._sources[name] = DataSource(
                name=name,
                dialect=dialect,
                engine=engine,
                profile=profile,
                allowed_tables={t.lower() for t in item.get("allowed_tables", [])},
                sensitive_columns={c.lower() for c in item.get("sensitive_columns", [])},
                row_filters={k.lower(): v for k, v in (item.get("row_filters") or {}).items()},
                large_tables={t.lower() for t in item.get("large_tables", [])},
                semantics_file=item.get("semantics_file"),
                description=item.get("description", ""),
                extra=item,
            )

    def get(self, name: str) -> DataSource:
        if name not in self._sources:
            raise KeyError(f"未知数据源: {name}")
        return self._sources[name]

    def list(self) -> list[dict]:
        return [
            {"name": ds.name, "dialect": ds.dialect, "description": ds.description,
             "tables": sorted(ds.allowed_tables)}
            for ds in self._sources.values()
        ]

    def healthcheck(self, name: str) -> bool:
        ds = self.get(name)
        try:
            with ds.engine.connect() as conn:
                conn.execute(text(ds.profile["healthcheck"]))
            return True
        except Exception:
            return False

    def run_query(self, name: str, sql: str, max_rows: int,
                  timeout_s: int = 30) -> tuple[list[dict], bool]:
        """执行只读查询,返回 (行列表, 是否被截断)。异常向上抛,由错误修正节点消费。

        超时:按方言设置语句级超时(timeout_sql);不支持的库靠行数截断兜底。
        """
        ds = self.get(name)
        with ds.engine.connect() as conn:
            if tpl := ds.profile.get("timeout_sql"):
                try:
                    conn.execute(text(tpl.format(
                        ms=timeout_s * 1000, us=timeout_s * 1_000_000, s=timeout_s)))
                except Exception:
                    pass    # 超时设置失败不阻断查询(权限/版本差异)
            result = conn.execute(text(sql))
            keys = list(result.keys())
            raw = result.fetchmany(max_rows + 1)
        truncated = len(raw) > max_rows
        rows = [
            {k: _jsonable(v) for k, v in zip(keys, row)}
            for row in raw[:max_rows]
        ]
        return rows, truncated
