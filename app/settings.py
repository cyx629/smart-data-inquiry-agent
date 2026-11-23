"""全局配置:LLM 三元组、检索参数、安全阈值均可通过环境变量 / .env 覆盖。"""
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # LLM(OpenAI 兼容接口:云端 API 与 vLLM 私有化可切换)
    llm_base_url: str = "https://api.deepseek.com/v1"
    llm_api_key: str = ""
    llm_model: str = "deepseek-chat"
    # 总结/改写可用小模型降本,不配则复用主模型
    llm_light_model: str = ""

    # Embedding(可选,OpenAI 兼容 /embeddings;不配置时退化为词面重叠检索)
    embedding_base_url: str = ""
    embedding_api_key: str = ""
    embedding_model: str = ""

    # 路径
    datasources_file: str = "config/datasources.yaml"
    glossary_file: str = "config/glossary.yaml"
    glossary_dir: str = "config/glossaries"   # 按数据源覆盖术语表:{ds}.yaml 存在则优先
    fewshot_seed_dir: str = "config/fewshots"
    data_dir: str = "data"

    # 安全与执行
    max_rows: int = 100          # 无 LIMIT 时强制注入的行数上限
    max_retries: int = 3         # 错误修正重试上限
    query_timeout_s: int = 30

    # RAG 检索
    schema_topk: int = 5
    fewshot_topk: int = 3
    full_schema_table_threshold: int = 15   # 表数 ≤ 该值直接全量注入,跳过检索
    summarize_row_limit: int = 50           # 传给总结节点的最大行数


settings = Settings()
