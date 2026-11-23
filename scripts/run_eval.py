"""评测脚本(设计文档 §11):跑评测集,输出执行成功率/结果准确率/首次命中率/P95 耗时/拦截率。

用法:
    python scripts/run_eval.py [--cases eval/eval_demo.yaml] [--datasource demo]
                               [--limit N] [--out data/eval_report.json]

结果比对:gold_sql 与 Agent 结果均归一化后比较;
- 严格:行数与每行值多重集完全一致;
- 宽松:行数一致且每条 gold 行的值都包含于某条未匹配的 Agent 行(Agent 多返回列不判错)。
准确率按"严格或宽松通过"计。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import Counter

import yaml

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.audit.db import AuditLog                      # noqa: E402
from app.core.graph import GraphDeps, build_graph      # noqa: E402
from app.datasource.manager import DataSourceManager   # noqa: E402
from app.llm.client import LLMClient, build_embedder   # noqa: E402
from app.semantic.metadata import build_table_docs     # noqa: E402
from app.semantic.store import (  # noqa: E402
    FewshotStore, SchemaIndex, load_glossaries, load_glossary,
)
from app.settings import Settings                      # noqa: E402


def _norm(v):
    if isinstance(v, bool):
        return str(v)
    if isinstance(v, (int, float)):
        return f"{float(v):.2f}"
    return str(v).strip()


def _row_multiset(row_values) -> Counter:
    return Counter(_norm(v) for v in row_values)


def strict_match(gold: list[tuple], agent: list[dict]) -> bool:
    if len(gold) != len(agent):
        return False
    g = sorted(sorted(_row_multiset(r).elements()) for r in gold)
    a = sorted(sorted(_row_multiset(r.values()).elements()) for r in agent)
    return g == a


def lenient_match(gold: list[tuple], agent: list[dict]) -> bool:
    """gold 每行的值都能在某条未匹配的 agent 行中找到(agent 允许多返回列)。"""
    if len(gold) != len(agent):
        return False
    remaining = [_row_multiset(r.values()) for r in agent]
    for grow in gold:
        gm = _row_multiset(grow)
        for i, am in enumerate(remaining):
            if all(am[k] >= c for k, c in gm.items()):
                remaining.pop(i)
                break
        else:
            return False
    return True


def single_value_match(gold: list[tuple], agent: list[dict]) -> bool:
    if len(gold) == 1 and len(gold[0]) == 1:
        target = _norm(gold[0][0])
        return any(_norm(v) == target for r in agent for v in r.values())
    return False


def build_deps() -> GraphDeps:
    settings = Settings()
    dsm = DataSourceManager(settings.datasources_file)
    embedder = build_embedder(settings)
    index = SchemaIndex(embedder, settings.full_schema_table_threshold)
    fewshots = FewshotStore(embedder, settings.data_dir, settings.fewshot_seed_dir)
    for item in dsm.list():
        index.load(item["name"], build_table_docs(dsm.get(item["name"])))
        fewshots.load(item["name"])
    return GraphDeps(
        settings=settings, llm=LLMClient(settings), dsm=dsm, schema_index=index,
        fewshots=fewshots, glossary=load_glossary(settings.glossary_file),
        audit=AuditLog(settings.data_dir),
        glossaries=load_glossaries(
            [item["name"] for item in dsm.list()],
            settings.glossary_dir, settings.glossary_file),
    )


def run_case(graph, deps: GraphDeps, datasource: str, case: dict) -> dict:
    t0 = time.time()
    try:
        out = graph.invoke({
            "question": case["question"], "datasource": datasource,
            "session_id": f"eval-{case['id']}",
        })
    except Exception as e:
        return {"id": case["id"], "category": case["category"], "passed": False,
                "status": "error", "detail": str(e), "elapsed_s": time.time() - t0}
    elapsed = time.time() - t0
    status = out.get("status", "")
    expect = case["expect"]
    detail, passed, accurate, first_hit = "", False, None, None

    if expect == "clarify":
        passed = status == "clarify"
        detail = f"status={status}"
    elif expect == "needs_confirm":
        passed = status == "needs_confirm"
        detail = f"status={status}"
    elif expect == "not_success":
        passed = status != "success"
        detail = f"status={status}"
    elif expect == "success_only":
        passed = status == "success"
        detail = f"status={status}"
    else:  # success:执行成功且结果一致
        if status != "success":
            detail = f"status={status} err={str(out.get('sql_error'))[:100]}"
        else:
            with deps.dsm.get(datasource).engine.connect() as conn:
                from sqlalchemy import text
                gold_rows = [tuple(r) for r in conn.execute(text(case["gold_sql"])).fetchall()]
            agent_rows = out.get("result_rows") or []
            accurate = (strict_match(gold_rows, agent_rows)
                        or lenient_match(gold_rows, agent_rows)
                        or single_value_match(gold_rows, agent_rows))
            passed = accurate
            first_hit = accurate and not out.get("attempts")
            if not accurate:
                detail = (f"结果不一致 gold={gold_rows[:3]} "
                          f"agent={[list(r.values()) for r in agent_rows[:3]]}")
    return {
        "id": case["id"], "category": case["category"], "question": case["question"],
        "expect": expect, "status": status, "passed": passed, "accurate": accurate,
        "first_hit": first_hit, "retries": len(out.get("attempts") or []),
        "sql": out.get("sql", ""), "elapsed_s": round(elapsed, 2), "detail": detail,
    }


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default="eval/eval_demo.yaml")
    ap.add_argument("--datasource", default="demo")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--out", default="data/eval_report.json")
    args = ap.parse_args()

    with open(args.cases, encoding="utf-8") as f:
        cases = yaml.safe_load(f)["cases"]
    if args.limit:
        cases = cases[: args.limit]

    deps = build_deps()
    graph = build_graph(deps, checkpointer_path=None)   # 评测不需要多轮记忆

    results = []
    for i, case in enumerate(cases, 1):
        r = run_case(graph, deps, args.datasource, case)
        results.append(r)
        mark = "✅" if r["passed"] else "❌"
        print(f"[{i}/{len(cases)}] {mark} {r['id']:<10} {r['category']:<12} "
              f"{r['elapsed_s']:>6}s retries={r.get('retries', 0)} {r['detail'][:80]}")

    # 指标汇总(设计文档 §11.2)
    qa = [r for r in results if r["expect"] in ("success", "success_only")]
    acc_cases = [r for r in results if r["expect"] == "success"]
    guard = [r for r in results if r["expect"] in ("not_success", "clarify", "needs_confirm")]
    exec_ok = [r for r in qa if r["status"] == "success"]
    accurate = [r for r in acc_cases if r.get("accurate")]
    first = [r for r in acc_cases if r.get("first_hit")]
    times = sorted(r["elapsed_s"] for r in results)
    p95 = times[max(0, int(len(times) * 0.95) - 1)] if times else 0

    summary = {
        "总用例": len(results),
        "执行成功率": f"{len(exec_ok)}/{len(qa)} = {len(exec_ok) / len(qa):.1%}" if qa else "-",
        "结果准确率": f"{len(accurate)}/{len(acc_cases)} = {len(accurate) / len(acc_cases):.1%}" if acc_cases else "-",
        "首次命中率": f"{len(first)}/{len(acc_cases)} = {len(first) / len(acc_cases):.1%}" if acc_cases else "-",
        "拦截/澄清通过": f"{sum(r['passed'] for r in guard)}/{len(guard)}" if guard else "-",
        "P95耗时(s)": p95,
        "总通过": f"{sum(r['passed'] for r in results)}/{len(results)}",
    }
    print("\n===== 评测汇总 =====")
    for k, v in summary.items():
        print(f"{k}: {v}")

    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", encoding="utf-8") as f:
        json.dump({"summary": summary, "results": results}, f, ensure_ascii=False, indent=2)
    print(f"\n报告已写入 {args.out}")

    # CI 阻断线(文档 §11.3):准确率 < 80% 返回非零
    if acc_cases and len(accurate) / len(acc_cases) < 0.8:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
