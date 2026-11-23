FROM python:3.12-slim

# 不生成 .pyc、日志实时输出、pip 不留缓存
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 中文字体:图表服务端渲染需要
RUN apt-get update && apt-get install -y --no-install-recommends fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY ui ./ui
COPY config ./config
COPY scripts ./scripts
COPY eval ./eval
COPY .chainlit ./.chainlit
COPY chainlit.md .

# 演示库(生产环境可移除,改为挂载 config/ 与外部数据源)
RUN python scripts/seed_demo.py

EXPOSE 8000 8501
# 默认启动 API 服务;对话前端另起:chainlit run ui/chainlit_app.py --host 0.0.0.0 --port 8501
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
