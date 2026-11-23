"""语义资产层(设计文档 §6):增强 Schema 召回、Few-shot 样例库、业务术语表。

检索策略:配置了 embedding 服务时用向量余弦相似度;否则退化为中文二元组
词面重叠打分(POC 零依赖可跑)。表 ≤ 阈值的数据源直接全量注入,跳过检索。
"""
from __future__ import annotations

import json
import math
import os
from dataclasses import dataclass

import yaml

from app.llm.client import EmbeddingClient
from app.semantic.metadata import TableDoc


# ---------- 打分 ----------

def _bigrams(s: str) -> set[str]:
    s = "".join(s.split()).lower()
    return {s[i:i + 2] for i in range(len(s) - 1)} | set(s)


def keyword_score(query: str, doc: str) -> float:
    q, d = _bigrams(query), _bigrams(doc)
    if not q:
        return 0.0
    return len(q & d) / math.sqrt(len(q) * max(len(d), 1))


def cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    return dot / (na * nb) if na and nb else 0.0


class _Retriever:
    """通用小型检索器:向量优先,词面兜底。"""

    def __init__(self, embedder: EmbeddingClient | None):
        self._embedder = embedder
        self._texts: list[str] = []
        self._vectors: list[list[float]] | None = None

    def index(self, texts: list[str]) -> None:
        self._texts = texts
        self._vectors = None
        if self._embedder and texts:
            try:
                self._vectors = self._embedder.embed(texts)
            except Exception:
                self._vectors = None    # embedding 服务异常时静默退化

    def search(self, query: str, topk: int) -> list[int]:
        if not self._texts:
            return []
        if self._vectors is not None:
            try:
                qv = self._embedder.embed([query])[0]
                scores = [cosine(qv, v) for v in self._vectors]
            except Exception:
                scores = [keyword_score(query, t) for t in self._texts]
        else:
            scores = [keyword_score(query, t) for t in self._texts]
        order = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)
        return order[:topk]


# ---------- 增强 Schema 索引 ----------

class SchemaIndex:
    def __init__(self, embedder: EmbeddingClient | None, full_inject_threshold: int = 15):
        self._embedder = embedder
        self._threshold = full_inject_threshold
        self._docs: dict[str, list[TableDoc]] = {}
        self._retrievers: dict[str, _Retriever] = {}

    def load(self, datasource: str, docs: list[TableDoc]) -> None:
        self._docs[datasource] = docs
        r = _Retriever(self._embedder)
        r.index([d.text for d in docs])
        self._retrievers[datasource] = r

    def table_count(self, datasource: str) -> int:
        return len(self._docs.get(datasource, []))

    def recall(self, datasource: str, question: str, topk: int = 5) -> list[str]:
        docs = self._docs.get(datasource, [])
        if not docs:
            return []
        if len(docs) <= self._threshold:      # 全量注入
            return [d.text for d in docs]
        idxs = self._retrievers[datasource].search(question, topk)
        picked = {docs[i].table for i in idxs}
        # 外键关联补齐:JOIN 需要的维表常常不含问题关键词
        by_name = {d.table: d for d in docs}
        for i in list(idxs):
            for rel in docs[i].related_tables:
                if rel in by_name:
                    picked.add(rel)
        return [d.text for d in docs if d.table in picked]


# ---------- Few-shot 样例库(按数据源分池,支持回灌飞轮) ----------

@dataclass
class Fewshot:
    question: str
    sql: str


class FewshotStore:
    def __init__(self, embedder: EmbeddingClient | None, data_dir: str, seed_dir: str):
        self._embedder = embedder
        self._data_dir = os.path.join(data_dir, "fewshots")
        self._seed_dir = seed_dir
        os.makedirs(self._data_dir, exist_ok=True)
        self._pools: dict[str, list[Fewshot]] = {}
        self._retrievers: dict[str, _Retriever] = {}

    def _runtime_file(self, datasource: str) -> str:
        return os.path.join(self._data_dir, f"{datasource}.jsonl")

    def load(self, datasource: str) -> None:
        items: list[Fewshot] = []
        for path in (os.path.join(self._seed_dir, f"{datasource}.jsonl"),
                     self._runtime_file(datasource)):
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line:
                            obj = json.loads(line)
                            items.append(Fewshot(obj["question"], obj["sql"]))
        self._pools[datasource] = items
        self._reindex(datasource)

    def _reindex(self, datasource: str) -> None:
        r = _Retriever(self._embedder)
        r.index([fs.question for fs in self._pools.get(datasource, [])])
        self._retrievers[datasource] = r

    def search(self, datasource: str, question: str, topk: int = 3) -> list[dict]:
        pool = self._pools.get(datasource, [])
        if not pool:
            return []
        idxs = self._retrievers[datasource].search(question, topk)
        return [{"question": pool[i].question, "sql": pool[i].sql} for i in idxs]

    def add(self, datasource: str, question: str, sql: str) -> None:
        """答对回灌(Vanna 式飞轮):采纳的 QA 对写入样例库并重建索引。重复问题跳过。"""
        if any(fs.question == question for fs in self._pools.get(datasource, [])):
            return
        with open(self._runtime_file(datasource), "a", encoding="utf-8") as f:
            f.write(json.dumps({"question": question, "sql": sql}, ensure_ascii=False) + "\n")
        self._pools.setdefault(datasource, []).append(Fewshot(question, sql))
        self._reindex(datasource)


# ---------- 业务术语表 ----------

def load_glossary(path: str) -> str:
    try:
        with open(path, encoding="utf-8") as f:
            items = yaml.safe_load(f) or []
    except FileNotFoundError:
        return ""
    return "\n".join(f"- {it['term']}: {it['definition']}" for it in items)


def load_glossaries(datasources: list[str], glossary_dir: str, fallback_file: str) -> dict[str, str]:
    """按数据源加载术语表:config/glossaries/{ds}.yaml 存在则用,否则回落全局术语表。"""
    result: dict[str, str] = {}
    for name in datasources:
        path = os.path.join(glossary_dir, f"{name}.yaml")
        result[name] = load_glossary(path if os.path.exists(path) else fallback_file)
    return result
