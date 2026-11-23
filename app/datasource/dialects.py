"""方言配置中心(设计文档 §5.1):新增数据源只加配置,不改上层代码。

每个 profile 的键:
- sqlalchemy_uri_tpl:连接串模板;
- sqlglot_dialect:sqlglot 解析/转译方言(达梦近似 oracle,金仓近似 postgres);
- prompt_hint:注入生成 prompt 的方言说明;
- healthcheck:健康检查 SQL;
- row_cap_suffix:行数上限子句(元数据抽样等场景拼在 SQL 尾部,避免全表扫描);
- timeout_sql:会话/语句级超时设置模板({ms} 毫秒、{s} 秒),None 表示该库暂不支持;
- error_hints:常见报错与修正提示(错误修正节点注入)。

sqlite 仅用于本地开发与演示,生产目标为 mysql / dm / kingbase / oceanbase。
"""

DIALECT_PROFILES: dict[str, dict] = {
    "mysql": {
        "sqlalchemy_uri_tpl": "mysql+pymysql://{user}:{password}@{host}:{port}/{database}",
        "sqlglot_dialect": "mysql",
        "prompt_hint": "目标库MySQL 8:LIMIT分页,DATE_FORMAT日期,CONCAT拼接,反引号引用标识符。",
        "healthcheck": "SELECT 1",
        "row_cap_suffix": "LIMIT {n}",
        "timeout_sql": "SET SESSION MAX_EXECUTION_TIME={ms}",   # 仅对 SELECT 生效
        "error_hints": (
            "- Unknown column:检查字段名拼写与所属表别名\n"
            "- You have an error in your SQL syntax:检查MySQL方言语法"
        ),
    },
    "oceanbase": {
        # OceanBase MySQL 模式:兼容 MySQL 协议,直接用 pymysql。
        # 经 obproxy 接入时 user 形如 "用户名@租户名#集群名",端口 2883;直连观察者节点为 2881。
        "sqlalchemy_uri_tpl": "mysql+pymysql://{user}:{password}@{host}:{port}/{database}",
        "sqlglot_dialect": "mysql",
        "prompt_hint": (
            "目标库OceanBase(MySQL模式),语法与MySQL 8基本一致:LIMIT分页,DATE_FORMAT日期,"
            "CONCAT拼接,反引号引用标识符。禁止Oracle专有语法。"
        ),
        "healthcheck": "SELECT 1",
        "row_cap_suffix": "LIMIT {n}",
        "timeout_sql": "SET SESSION ob_query_timeout={us}",      # 单位微秒
        "error_hints": (
            "- Unknown column:检查字段名拼写与所属表别名\n"
            "- Timeout/超时:缩小查询范围或增加过滤条件\n"
            "- 语法与MySQL一致,禁止使用Oracle函数"
        ),
    },
    "oceanbase_oracle": {
        # OceanBase Oracle 模式:Python 生态驱动支持有限(通常经 JDBC/obclient),
        # 此 profile 先占位方言规则,连接层待驱动验证后补充 uri 模板。
        "sqlalchemy_uri_tpl": "oceanbase_oracle://{user}:{password}@{host}:{port}",
        "sqlglot_dialect": "oracle",
        "prompt_hint": (
            "目标库OceanBase(Oracle模式),语法按Oracle风格:TO_CHAR/TO_DATE处理日期,"
            "||拼接字符串,NVL处理空值,对象名默认大写;分页用 FETCH FIRST n ROWS ONLY 或 ROWNUM。"
            "禁止MySQL专有函数。"
        ),
        "healthcheck": "SELECT 1 FROM DUAL",
        "row_cap_suffix": "FETCH FIRST {n} ROWS ONLY",
        "timeout_sql": None,
        "error_hints": (
            "- 无效的列名/表名:检查大小写与双引号\n"
            "- 检查是否误用MySQL函数,改用TO_CHAR/NVL等Oracle风格函数"
        ),
    },
    "dm": {
        "sqlalchemy_uri_tpl": "dm+dmPython://{user}:{password}@{host}:{port}",
        "sqlglot_dialect": "oracle",  # 近似兜底
        "prompt_hint": (
            "目标库达梦DM8,语法按Oracle风格:TO_CHAR/TO_DATE处理日期,ADD_MONTHS做月份加减,"
            "||拼接字符串,NVL处理空值;对象名默认大写,引用需双引号且注意大小写;"
            "分页优先LIMIT,若报错改用 FETCH FIRST n ROWS ONLY。禁止MySQL专有函数。"
        ),
        "healthcheck": "SELECT 1 FROM DUAL",
        "row_cap_suffix": "LIMIT {n}",
        "timeout_sql": None,   # 达梦语句级超时依赖连接参数/驱动配置,POC 靠行数截断兜底
        "error_hints": (
            "- 无效的列名:检查大小写与双引号,达梦对象名默认大写\n"
            "- 无效的表或视图名:确认表名大小写,必要时加双引号\n"
            "- 语法分析出错:检查是否误用MySQL函数(DATE_FORMAT/IFNULL等),改用TO_CHAR/NVL"
        ),
    },
    "kingbase": {
        "sqlalchemy_uri_tpl": "kingbase+ksycopg2://{user}:{password}@{host}:{port}/{database}",
        "sqlglot_dialect": "postgres",
        "prompt_hint": (
            "目标库金仓KingbaseES,语法按PostgreSQL:TO_CHAR日期,||拼接,COALESCE空值,"
            "LIMIT分页,标识符双引号。禁止MySQL专有函数。"
        ),
        "healthcheck": "SELECT 1",
        "row_cap_suffix": "LIMIT {n}",
        "timeout_sql": "SET statement_timeout = {ms}",
        "error_hints": (
            "- column ... does not exist:检查字段名与双引号大小写\n"
            "- 检查是否误用MySQL函数(DATE_FORMAT/IFNULL等),改用TO_CHAR/COALESCE"
        ),
    },
    "sqlite": {
        "sqlalchemy_uri_tpl": "sqlite:///{path}",
        "sqlglot_dialect": "sqlite",
        "prompt_hint": (
            "目标库SQLite:LIMIT分页,strftime('%Y-%m', d)做日期格式化,||拼接字符串,"
            "IFNULL处理空值,日期以TEXT存储(YYYY-MM-DD)。"
        ),
        "healthcheck": "SELECT 1",
        "row_cap_suffix": "LIMIT {n}",
        "timeout_sql": None,   # 通过 Python 层行数截断兜底
        "error_hints": "- no such column/table:检查名称拼写\n- 日期比较请用字符串形式 'YYYY-MM-DD'",
    },
}
