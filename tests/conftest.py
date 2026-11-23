"""测试公共设施:FakeLLM 与共享的 deps fixture(离线,不依赖真实 LLM)。"""
import json
import os
import subprocess
import sys

import pytest

from app.audit.db import AuditLog
from app.core.graph import GraphDeps
from app.datasource.manager import DataSourceManager
from app.semantic.metadata import build_table_docs
from app.semantic.metrics import MetricStore
from app.semantic.store import FewshotStore, SchemaIndex, load_glossaries, load_glossary
from app.settings import Settings

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


class FakeLLM:
    """按调用顺序返回预设 SQL 的假模型;summarize/rewrite 返回固定文本。"""

    def __init__(self, sql_sequence: list[str] | None = None,
                 clarify: str | None = None,
                 rewrite_to: str | None = None):
        self._sqls = list(sql_sequence or [])
        self._clarify = clarify
        self._rewrite_to = rewrite_to
        self.model = "fake"
        self.light_model = "fake"
        self.calls: list[str] = []

    def _record_usage(self, usage_out, model):
        if usage_out is not None:
            usage_out.append({"model": model or "fake", "prompt_tokens": 100,
                              "completion_tokens": 20, "total_tokens": 120})

    def chat(self, system, user, *, model=None, temperature=0.1, usage_out=None):
        self.calls.append(user[:50])
        self._record_usage(usage_out, model)
        if user.startswith("用户在多轮对话中提出了追问"):     # REWRITE_TMPL 开头标记
            return self._rewrite_to or user.split("【当前问题】")[-1].strip()
        if "【查询结果】" in user:                          # SUMMARIZE_TMPL 标记
            return "根据查询结果,共有若干条数据,详见表格。"
        return json.dumps(self.chat_json(system, user), ensure_ascii=False)

    def chat_json(self, system, user, *, model=None, usage_out=None):
        self._record_usage(usage_out, model)
        if self._clarify:
            return {"sql": "", "explanation": "", "clarify": self._clarify}
        sql = self._sqls.pop(0) if self._sqls else "SELECT 1"
        return {"sql": sql, "explanation": "测试", "clarify": None}


def build_test_deps(llm) -> GraphDeps:
    settings = Settings(data_dir="data")
    dsm = DataSourceManager(settings.datasources_file)
    index = SchemaIndex(None, settings.full_schema_table_threshold)
    fewshots = FewshotStore(None, settings.data_dir, settings.fewshot_seed_dir)
    metrics = MetricStore()
    for item in dsm.list():
        index.load(item["name"], build_table_docs(dsm.get(item["name"])))
        fewshots.load(item["name"])
        metrics.load(item["name"])
    return GraphDeps(
        settings=settings, llm=llm, dsm=dsm, schema_index=index,
        fewshots=fewshots, glossary=load_glossary(settings.glossary_file),
        audit=AuditLog(settings.data_dir), metrics=metrics,
        glossaries=load_glossaries(
            [item["name"] for item in dsm.list()],
            settings.glossary_dir, settings.glossary_file),
    )


@pytest.fixture(scope="session", autouse=True)
def seeded_db():
    os.chdir(ROOT)
    if not os.path.exists("data/demo.db"):
        subprocess.run([sys.executable, "scripts/seed_demo.py"], check=True)
    if not os.path.exists("data/scriptkill.db"):
        subprocess.run([sys.executable, "scripts/seed_scriptkill.py"], check=True)
    yield


@pytest.fixture(scope="session")
def base_deps(seeded_db):
    """共享的确定性依赖(schema 索引/样例库等);测试内替换 llm 后使用。"""
    return build_test_deps(FakeLLM())
