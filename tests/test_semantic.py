"""语义资产层测试:元数据文档、检索、few-shot 池、术语表。"""
import os

from app.semantic.metadata import TableDoc, build_table_docs
from app.semantic.store import FewshotStore, SchemaIndex, keyword_score


def test_table_docs_cover_all_whitelist(base_deps):
    docs = build_table_docs(base_deps.dsm.get("demo"))
    names = {d.table for d in docs}
    assert names == {"customers", "employees", "products", "orders",
                     "order_items", "returns", "sales_targets"}


def test_docs_contain_chinese_semantics_and_enums(base_deps):
    docs = {d.table: d for d in build_table_docs(base_deps.dsm.get("demo"))}
    cust = docs["customers"].text
    assert "客户信息表" in cust
    assert "华东" in cust                      # enum: auto 抽取到了大区枚举
    orders = docs["orders"].text
    assert "已完成" in orders                  # 显式枚举
    assert "customers" in docs["orders"].related_tables


def test_sensitive_column_masked_in_samples(base_deps):
    docs = {d.table: d for d in build_table_docs(base_deps.dsm.get("demo"))}
    cust = docs["customers"].text
    assert "敏感列" in cust
    # 样例行中不应出现 11 位手机号原文
    import re
    sample_part = cust.split("样例数据")[-1]
    assert not re.search(r"1[3589]\d{9}", sample_part)


def test_full_injection_under_threshold(base_deps):
    # 7 张表 ≤ 15,应全量注入
    schemas = base_deps.schema_index.recall("demo", "随便问", topk=3)
    assert len(schemas) == 7


def test_retrieval_path_over_threshold():
    # 超过阈值时走检索,且外键关联表被自动补齐
    docs = [TableDoc(table=f"t{i}", text=f"表名: t{i} 业务表{i}") for i in range(20)]
    docs.append(TableDoc(table="sales", text="表名: sales 销售流水表,记录销售金额",
                         related_tables=["dim_shop"]))
    docs.append(TableDoc(table="dim_shop", text="表名: dim_shop 门店维表"))
    index = SchemaIndex(None, full_inject_threshold=15)
    index.load("big", docs)
    hits = index.recall("big", "销售金额是多少", topk=3)
    joined = "\n".join(hits)
    assert "sales" in joined
    assert "dim_shop" in joined, "外键关联表应被补齐"
    assert len(hits) < 22, "不应全量注入"


def test_keyword_score_prefers_relevant():
    assert keyword_score("销售额趋势", "每月销售额统计") > keyword_score("销售额趋势", "员工考勤表")


def test_fewshot_search_and_dedup(base_deps, tmp_path):
    store = FewshotStore(None, str(tmp_path), "config/fewshots")
    store.load("demo")
    hits = store.search("demo", "上个月销售额多少", topk=3)
    assert hits and any("销售额" in h["question"] for h in hits)
    n = len(store._pools["demo"])
    store.add("demo", "测试问题X", "SELECT 1")
    store.add("demo", "测试问题X", "SELECT 1")   # 重复应跳过
    assert len(store._pools["demo"]) == n + 1


def test_glossary_loaded(base_deps):
    assert "大客户" in base_deps.glossary
    assert "退货率" in base_deps.glossary
