# 智能问数系统

基于 **LangGraph + SQLAlchemy 多方言接入 + RAG 语义增强** 的自然语言问数系统:
用户中文提问 → 状态机编排(改写→召回→生成→校验→执行→修正→总结)→ 返回 SQL + 数据 + 中文结论。

实现依据:《智能问数系统技术方案设计文档》《智能问数技术调研对比文档》。

> **演示数据说明**:`data/` 下的 SQLite 演示库由 `scripts/seed_demo.py` /
> `scripts/seed_scriptkill.py` 脚本生成,仅用于本地演示与评测,不含任何真实客户或玩家信息;
> 生产环境的数据源接入见下文“接入生产数据源”。

## 快速开始

```bash
# 1. 安装依赖(Python 3.11+)
pip install -r requirements.txt

# 2. 配置 LLM(OpenAI 兼容接口)
cp .env.example .env   # 填入 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL

# 3. 生成演示库(SQLite)
python scripts/seed_demo.py          # 销售库:500客户/40销售/2万订单/6万明细/退货/销售目标
python scripts/seed_scriptkill.py    # 剧本杀连锁库:马鞍山5店/1.7万场次/10万到场明细

# 4a. 启动对话前端(推荐,Chainlit:完整展示执行过程)
chainlit run ui/chainlit_app.py --port 8501
# 打开 http://127.0.0.1:8501,切换数据源后例如提问:
#   demo:       "各地区的销售额是多少?" "2026年6月华东大区的销售额"
#   scriptkill: "今天哪个本卖得最好?" "2026年6月哪个DM场次最多?" "各门店客单价是多少?"

# 4b. 或启动 API 服务(含轻量演示页,供系统集成)
uvicorn app.main:app --host 0.0.0.0 --port 8000
```

运行测试(不依赖 LLM,离线可跑):`pytest tests/ -q`

运行评测(需要真实 LLM,设计文档 §11):

```bash
python scripts/run_eval.py --cases eval/eval_demo.yaml --datasource demo
python scripts/run_eval.py --cases eval/eval_scriptkill.yaml --datasource scriptkill
python scripts/run_eval.py --cases eval/eval_scriptkill.yaml --datasource scriptkill --limit 5
# demo 56 条;scriptkill 68 条(含 9 条越权拦截、2 条 HITL、6 条歧义澄清)
# 输出执行成功率/结果准确率/首次命中率/P95耗时/拦截率,报告写入 data/eval_report.json
# 准确率 < 80% 时退出码非零,可直接接入 CI 作为回归阻断线(§11.3)
# 准确率随模型/提示词变化,请以实际运行报告为准,不要沿用历史数字
```

## 项目结构与设计文档章节对照

| 目录/文件 | 职责 | 对应文档章节 |
|---|---|---|
| `app/core/graph.py` | LangGraph 主图:半 Agent 编排 + 重试闭环(≤3 次)+ 澄清分支 | §3 / §10 |
| `app/core/state.py` | QAState 状态定义,history 支撑多轮追问 | §3.2 |
| `app/core/prompts.py` | 生成/修正/总结/改写 Prompt(含防注入声明与当前日期) | §4.3 / §7 |
| `app/core/chart.py` | 图表建议:规则优先(柱/折/饼),输出 ECharts 配置 | §4.6 |
| `app/semantic/metrics.py` | 指标语义层 + 输出干预:命中即确定性直出,跳过 LLM | 二期(竞品调研 §4) |
| `app/datasource/dialects.py` | 方言配置中心(mysql/oceanbase/dm/kingbase/sqlite) | §5.1 |
| `app/datasource/manager.py` | 数据源注册、连接池、健康检查、只读执行、行数截断、语句超时 | §4.1 |
| `app/security/validator.py` | sqlglot AST 校验:仅单条 SELECT、表白名单、敏感列拦截、LIMIT 注入 | §7 |
| `app/semantic/metadata.py` | 元数据同步:反射 + 人工语义映射 + 枚举 + 样例行 → 增强 schema | §4.2 |
| `app/semantic/store.py` | Schema 召回(≤15 表全量注入)、Few-shot 分池检索、术语表 | §6 |
| `app/audit/db.py` | 全链路审计落库,反馈标注 | §4.7 |
| `app/main.py` | FastAPI:SSE 问数、数据源、反馈飞轮、元数据同步、审计 | §8 |
| `eval/` + `scripts/run_eval.py` | 评测集与评测脚本(指标 + CI 阻断线) | §11 |
| `ui/chainlit_app.py` | Chainlit 对话前端:执行步骤全透出、图表、反馈、HITL 确认 | — |
| `app/static/index.html` | 轻量演示页(执行过程折叠日志 / SQL / 表格 / 确认与采纳按钮) | — |
| `config/` | 数据源、语义映射、指标模板、按数据源术语表、few-shot 种子样例 | §6.1 |

## 接口

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/v1/chat` | POST(SSE) | `{session_id?, datasource, question, confirmed?}`;事件流:`session/thinking/sql/rows/chart/answer/error/done`;`status=needs_confirm` 时携带 `confirmed=true` 重发即确认执行 |
| `/api/v1/datasources` | GET | 可用数据源列表 |
| `/api/v1/feedback` | POST | `{audit_id, adopted}`;采纳 → QA 对回灌 few-shot 库(准确率飞轮) |
| `/api/v1/meta/sync` | POST | 重新抽取元数据、重建检索索引 |
| `/api/v1/audit` | GET | 审计查询 |

多轮追问:携带同一 `session_id` 再次提问即可("再按月份拆分一下"),
问题改写节点会结合历史合并为独立问题(checkpointer 持久化在 `data/checkpoints.db`)。

## 接入生产数据源(MySQL / OceanBase / 达梦 / 金仓)

1. 在 `config/datasources.yaml` 增加条目(文件内有注释模板),使用**只读账号** + `allowed_tables` 白名单 + `sensitive_columns` 敏感列;
2. 为该数据源建 `config/semantics/<name>.yaml` 语义映射(表/字段中文说明,准确率的生命线);
3. 可选:`config/metrics/<name>.yaml` 指标模板与输出干预,`config/glossaries/<name>.yaml` 业务术语表(按数据源隔离);
4. 可选:`config/fewshots/<name>.jsonl` 放 3~5 条种子问题-SQL 对(按目标库方言书写);
5. 驱动安装:
   - MySQL:`pymysql` 已内置;
   - OceanBase(MySQL 模式):`pymysql` 直连即可,无需额外驱动。经 obproxy 端口 2883、用户名 `user@tenant#cluster`(直连 observer 为 2881、`user@tenant`);建议 OB 4.x LTS,接入后实测元数据反射(OB 的 information_schema 与 MySQL 有细微差异)。Oracle 模式无官方 Python 驱动(需 JDBC 桥),暂不承诺,建议开 MySQL 租户;
   - 达梦 DM8:从达梦安装介质 `drivers/python` 安装 `dmPython` 与 SQLAlchemy 方言包(注意与 SQLAlchemy 2.0 的兼容性,不兼容时降级 1.4 并记录);
   - 金仓 KingbaseES:从官方渠道安装 `ksycopg2` 与方言包;确认实例兼容模式(PG/Oracle),若为 Oracle 模式,把 `dialects.py` 中 kingbase 的 `sqlglot_dialect`/`prompt_hint` 切到 oracle 侧;
5. 重启服务或调用 `POST /api/v1/meta/sync`。

Docker 部署(POC 单容器):`docker compose up -d --build`(LLM 配置经环境变量注入,`config/` 外挂可热改数据源)。

## 安全机制

- 任何 SQL 必须过 sqlglot AST 校验:仅单条 SELECT、禁止 DDL/DML/DCL 与 `INTO OUTFILE`、表白名单、无 LIMIT 自动注入(默认 100 行);
- 敏感列防护(`sensitive_columns` 配置):SQL 中出现敏感列即拒绝(含对敏感表 `SELECT *`;`COUNT(*)` 不误伤),元数据样例行同步脱敏,敏感值不进入 LLM prompt;
- 执行防护:语句级超时按方言下发(MySQL `MAX_EXECUTION_TIME` / OB `ob_query_timeout` / 金仓 `statement_timeout`),行数截断兜底;
- 解析失败(达梦等方言 sqlglot 非 100% 覆盖)降级为关键词黑名单兜底 + 只读账号兜底;
- 检索到的 schema/样例/数据在 prompt 中声明为数据而非指令(防 Prompt 注入);写操作请求由模型走澄清拒绝,且修正节点禁止编造结果;
- 行级权限(`row_filters` 配置):按表配置过滤条件模板,AST 级注入到查询的每个作用域(`{t}` 为表别名);
- HITL 人工确认(`large_tables` 配置):大表无过滤条件的查询先返回 `needs_confirm`,用户确认后才执行(设计文档 §7);
- 全链路审计:问题、改写、各次重试 SQL 与报错、行数、耗时、状态均落库可回放。

## 指标语义层与输出干预(config/metrics/<数据源>.yaml)

- **overrides(输出干预)**:高频问法的"问题 → 强制 SQL"精确映射(忽略标点),用于纠正模型总答错的口径;
- **metrics(指标模板)**:正则命名分组抽参 + SQL 模板(如"{year}年{month}月{region}的销售额"),命中即跳过 LLM 确定性直出;
- 未命中自动降级到 LLM 生成主链路;命中情况在前端"匹配指标库"步骤与回答元信息中展示。

