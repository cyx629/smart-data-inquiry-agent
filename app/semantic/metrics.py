"""指标语义层 + 输出干预(借鉴 Smartbi 指标层与析言GBI输出干预,见竞品调研 §4)。

- overrides(输出干预):问题归一化后精确匹配 → 强制使用管理员配置的 SQL;
- metrics(指标模板):正则(命名分组)匹配 → 参数代入 SQL 模板。

命中即跳过 LLM 生成,确定性直出——已治理指标的口径一致性与准确率由模板保证,
未命中降级走 LLM 生成主链路。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass

import yaml

_PUNCT = re.compile(r"[\s??!!。,.、::;;""''\"']+")


def normalize(q: str) -> str:
    return _PUNCT.sub("", q).lower()


@dataclass
class MetricHit:
    name: str
    sql: str
    kind: str        # override / metric


class MetricStore:
    """按数据源加载 config/metrics/{ds}.yaml。"""

    def __init__(self, config_dir: str = "config/metrics"):
        self._dir = config_dir
        self._overrides: dict[str, dict[str, dict]] = {}   # ds -> {normalized_q: {name, sql}}
        self._metrics: dict[str, list[dict]] = {}          # ds -> [{name, regex, sql_template}]

    def load(self, datasource: str) -> None:
        path = os.path.join(self._dir, f"{datasource}.yaml")
        self._overrides[datasource] = {}
        self._metrics[datasource] = []
        if not os.path.exists(path):
            return
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        for item in cfg.get("overrides") or []:
            self._overrides[datasource][normalize(item["question"])] = {
                "name": item.get("name") or item["question"], "sql": item["sql"],
            }
        for item in cfg.get("metrics") or []:
            try:
                item["_regex"] = re.compile(item["pattern"])
            except re.error:
                continue
            self._metrics[datasource].append(item)

    def match(self, datasource: str, question: str) -> MetricHit | None:
        # 1) 输出干预:归一化精确匹配
        if hit := self._overrides.get(datasource, {}).get(normalize(question)):
            return MetricHit(name=hit["name"], sql=hit["sql"], kind="override")
        # 2) 指标模板:正则 + 参数代入
        for item in self._metrics.get(datasource, []):
            if m := item["_regex"].search(question):
                params = {k: v for k, v in m.groupdict().items() if v is not None}
                # 数值参数补零(month=6 -> "06")便于拼 YYYY-MM
                for k, v in list(params.items()):
                    if isinstance(v, str) and v.isdigit():
                        params[k + "2"] = f"{int(v):02d}"
                try:
                    sql = item["sql_template"].format(**params)
                except (KeyError, IndexError):
                    continue     # 参数缺失,降级 LLM
                return MetricHit(name=item["name"], sql=sql, kind="metric")
        return None

    def count(self, datasource: str) -> dict:
        return {"overrides": len(self._overrides.get(datasource, {})),
                "metrics": len(self._metrics.get(datasource, []))}
