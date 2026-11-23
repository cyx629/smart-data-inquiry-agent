# 智能问数竞品调研:阿里系 / Smartbi 及 OceanBase 接入

> 版本:V1.0 | 日期:2026-08-06 | 依据:公开产品文档与开源资料在线调研
> 配套:《智能问数技术调研对比文档》《智能问数系统技术方案设计文档》

---

## 1. 阿里系智能问数 / ChatBI 产品

### 1.1 析言GBI(XiYan-GBI,阿里云百炼官方应用)

- **定位**:基于通义大模型在数据分析领域增强的"AI 原生数据助理",NL2SQL + 数据问答 + 分析洞察;
- **数据源**:公网 MySQL/PostgreSQL 协议库、VPC 内网库(AnalyticDB PG)、API 接入的"虚拟数据库"、Excel;
- **知识/语义配置四模块**:名词解释(术语与口径)、同义词管理(关联表列)、业务逻辑(输出校正指导)、Schema 配置(表描述/主外键/列描述/枚举映射)——与本系统的"术语表 + 语义映射 YAML"同构;
- **模型优化三级机制**:案例库(问法→标准 SQL,即人工 few-shot)、通配符管理(问法泛化)、模型输出干预(特定问法强制指定 SQL);
- **交互**:五步链路展示(问题理解→表选择→SQL生成→执行结果→可视化),图表柱/折/饼切换,结果导出 CSV;
- 参考:help.aliyun.com/zh/model-studio/xiyan-gbi/

### 1.2 开源 XiYan-SQL(XGenerationLab/XiYan-SQL)

- **三阶段框架**:Schema Linking → 多生成器候选生成(SFT 微调模型 + ICL 各出候选)→ 微调 selection 模型选优;
- **M-Schema**:半结构化 schema 表示(表/列/类型/主键/样例值紧凑文本),prompt 层核心创新;
- **榜单**:BIRD 测试集 EX 75.63%(曾居榜首)、Spider 89.65%;开源 XiYanSQL-QwenCoder 3B~32B 系列;
- 参考:arxiv.org/abs/2411.08599、github.com/XGenerationLab/XiYan-SQL

### 1.3 Quick BI 智能小Q

- Quick BI 增值模块,五类 Agent:小Q问数 / 解读 / 报告 / 搭建 / 洞察;
- 问数基于指定数据集:推理分析、SQL 生成、OLAP 取数、自动选表、可视化;"智能规划模式"做多步复杂问题拆解与多轮对话;
- 参考:help.aliyun.com/zh/quick-bi/product-overview/intelligentq

## 2. Smartbi 对话式分析(AIChat / 白泽)

- **架构**:RAG + 大模型 + 语义模型 + AI Agent;数据层是"统一指标体系 + 语义模型(Cube)",AI 层可私有化接入 Qwen/DeepSeek 等;
- **处理链路(与直接 NL2SQL 的本质差异)**:问题先经 Schema Mapper 检索业务知识库——命中指标则直接用指标计算公式经数据模型取数;未命中才对语义层做 RAG,再由大模型生成 **DSL 中间语言**(非裸 SQL),由解析引擎翻译执行;
- **功能**:对话问数(多轮)、智能制图、归因分析、趋势预测、智能时间计算(复杂同环比)、异常预警;
- **落地主张**:"成败在指标库,不在对话"——指标先治理,宣称对已定义指标问答准确率 >95%;三阶段路径:指标治理建模 → 场景模板化 → 对话增强;
- 参考:smartbi.com.cn/aichat、smartbi.com.cn/article/201

## 3. OceanBase Python/SQLAlchemy 接入结论

### MySQL 模式(推荐,已在本系统落地)

- pymysql 可直连;SQLAlchemy 直接用 `mysql+pymysql://` 方言,OceanBase 官方有 SQLAlchemy 示例(ob-samples);
- 连接:经 obproxy 端口 **2883**,用户名 `user@tenant#cluster`;直连 observer 端口 **2881**,用户名 `user@tenant`;
- **已知坑**:
  1. OB 3.1.x + SQLAlchemy 1.4 存在 server 版本号解析报错,OB 3.2.3+ 修复,建议 4.x LTS;
  2. information_schema 与 MySQL 不完全一致,SQLAlchemy 反射(索引/外键元数据)可能有偏差,接入时需实测元数据同步模块;
  3. 兼容 MySQL 5.7/8.0 "大部分"特性,个别函数有差异(错误修正闭环可自愈)。

### Oracle 模式(暂不承诺)

- 官方仅提供 Java(Connector/J)与 C(OBCI)驱动,**Python 无官方驱动**;社区做法为 JayDeBeApi + JDK + Connector/J(JDBC 桥);
- 无可用 SQLAlchemy 方言 → 本系统 `oceanbase_oracle` profile 仅预置方言提示词,连接层待驱动验证;建议优先请对方开 MySQL 租户。

### pyobvector

- OceanBase 官方向量存储 SDK(Milvus 兼容 API),与 BI 取数无关;若未来把 few-shot/schema 向量库迁到 OB,可用它做向量检索层。

## 4. 对本系统的启示与差距清单

| # | 竞品机制 | 本系统现状 | 建议 |
|---|---|---|---|
| 1 | XiYan M-Schema 紧凑 schema 表示 | 自研"增强 schema 文档"(结构相近:中文语义+枚举+样例行) | 可对齐 M-Schema 格式做 A/B 评测,成本低 |
| 2 | 析言"案例库+通配符+输出干预"三级校正 | 已有 few-shot 案例库与采纳回灌 | 二期补"输出干预"(特定问法强制 SQL)与通配符泛化 |
| 3 | Smartbi 指标层优先,命中指标走模板取数 | 术语表口径注入(轻量) | 二期语义层:高频指标沉淀为"指标→SQL模板",命中直出,未命中降级 LLM 生成(与设计文档二期规划一致) |
| 4 | XiYan 多候选生成+选择器 | 单次生成+报错重试 | 准确率遇到瓶颈时再上:多温度采样 N 候选 + 执行校验选优 |
| 5 | 析言五步链路透出 / Quick BI 规划模式 | SSE 已透出 思考/SQL/数据/图表 各阶段 | 前端可加"表选择"展示(召回了哪些表),增强可解释性 |

---

*本文档为在线调研结论,事实以各产品官方文档为准。*
