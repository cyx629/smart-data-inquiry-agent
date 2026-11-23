"""FastAPI 接口测试:SSE 问数、数据源、反馈飞轮、元数据同步、审计(FakeLLM 离线)。"""
import json

import pytest
from fastapi.testclient import TestClient

import app.main as main_mod
from app.core.graph import build_graph
from tests.conftest import FakeLLM, build_test_deps


@pytest.fixture()
def client():
    deps = build_test_deps(FakeLLM(
        ["SELECT region AS 地区, COUNT(*) AS 客户数 FROM customers GROUP BY region"]))
    main_mod._deps = deps
    main_mod._graph = build_graph(deps, checkpointer_path=None)
    with TestClient(main_mod.app) as c:
        # TestClient 触发 startup 会重建 _deps(真实 LLM 配置),测试里强制替换回 FakeLLM
        main_mod._deps = deps
        main_mod._graph = build_graph(deps, checkpointer_path=None)
        yield c


def _parse_sse(text: str) -> list[tuple[str, dict]]:
    events = []
    for chunk in text.split("\n\n"):
        ev, data = "message", None
        for line in chunk.split("\n"):
            if line.startswith("event: "):
                ev = line[7:]
            elif line.startswith("data: "):
                data = json.loads(line[6:])
        if data is not None:
            events.append((ev, data))
    return events


def test_datasources(client):
    r = client.get("/api/v1/datasources")
    assert r.status_code == 200
    ds = r.json()["datasources"]
    assert ds[0]["name"] == "demo"
    assert "orders" in ds[0]["tables"]


def test_chat_sse_flow(client):
    r = client.post("/api/v1/chat",
                    json={"datasource": "demo", "question": "各地区客户数"})
    assert r.status_code == 200
    events = _parse_sse(r.text)
    kinds = [e for e, _ in events]
    assert "session" in kinds
    assert "sql" in kinds
    assert "rows" in kinds
    assert "answer" in kinds
    assert kinds[-1] == "done"
    answer = dict(events)[("answer")] if False else [d for e, d in events if e == "answer"][0]
    assert answer["status"] == "success"
    assert answer["audit_id"]


def test_chat_unknown_datasource(client):
    r = client.post("/api/v1/chat", json={"datasource": "nope", "question": "hi"})
    assert r.status_code == 404


def test_feedback_flywheel(client):
    r = client.post("/api/v1/chat",
                    json={"datasource": "demo", "question": "各地区客户数量统计一下"})
    answer = [d for e, d in _parse_sse(r.text) if e == "answer"][0]
    audit_id = answer["audit_id"]

    fb = client.post("/api/v1/feedback", json={"audit_id": audit_id, "adopted": True})
    assert fb.status_code == 200
    assert "回灌" in fb.json()["message"]
    # 回灌后 few-shot 池应能检索到该问题
    hits = main_mod._deps.fewshots.search("demo", "各地区客户数量统计一下", topk=5)
    assert any("各地区客户数量统计一下" in h["question"] for h in hits)


def test_feedback_not_found(client):
    r = client.post("/api/v1/feedback", json={"audit_id": "nonexistent", "adopted": True})
    assert r.status_code == 404


def test_meta_sync(client):
    r = client.post("/api/v1/meta/sync")
    assert r.status_code == 200
    assert r.json()["synced_tables"]["demo"] == 7


def test_audit_endpoint(client):
    client.post("/api/v1/chat", json={"datasource": "demo", "question": "各地区客户数"})
    r = client.get("/api/v1/audit?limit=5")
    assert r.status_code == 200
    assert r.json()["items"]


def test_index_page(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "智能问数" in r.text
