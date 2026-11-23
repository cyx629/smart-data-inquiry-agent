"""LLM 抽象(设计文档 §8.2):OpenAI 兼容 base_url + api_key + model 三元组。

云端 API 与 vLLM 私有化模型可通过环境变量切换;生成/修正与总结/改写可分别指定模型。
"""
from __future__ import annotations

import json
import re

from openai import OpenAI

from app.settings import Settings


def extract_json(text: str) -> dict:
    """从模型输出中鲁棒地提取 JSON 对象(容忍 ```json 围栏与前后杂讯)。"""
    text = text.strip()
    if m := re.search(r"```(?:json)?\s*(.*?)```", text, re.DOTALL):
        text = m.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass
    # 退而求其次:截取首个 { 到最后一个 }
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        return json.loads(text[start:end + 1])
    raise ValueError(f"无法从模型输出解析JSON: {text[:200]}")


class LLMClient:
    def __init__(self, settings: Settings):
        self._settings = settings
        self._client = OpenAI(
            base_url=settings.llm_base_url,
            api_key=settings.llm_api_key or "EMPTY",
        )
        self.model = settings.llm_model
        self.light_model = settings.llm_light_model or settings.llm_model

    def chat(self, system: str, user: str, *, model: str | None = None,
             temperature: float = 0.1, usage_out: list | None = None) -> str:
        resp = self._client.chat.completions.create(
            model=model or self.model,
            temperature=temperature,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        if usage_out is not None and resp.usage:
            usage_out.append({
                "model": resp.model or model or self.model,
                "prompt_tokens": resp.usage.prompt_tokens,
                "completion_tokens": resp.usage.completion_tokens,
                "total_tokens": resp.usage.total_tokens,
            })
        return resp.choices[0].message.content or ""

    def chat_json(self, system: str, user: str, *, model: str | None = None,
                  usage_out: list | None = None) -> dict:
        return extract_json(self.chat(system, user, model=model, usage_out=usage_out))


class EmbeddingClient:
    """可选:OpenAI 兼容 /embeddings。未配置时返回 None,检索退化为词面重叠。"""

    def __init__(self, settings: Settings):
        self._client = OpenAI(
            base_url=settings.embedding_base_url,
            api_key=settings.embedding_api_key or "EMPTY",
        )
        self.model = settings.embedding_model

    def embed(self, texts: list[str]) -> list[list[float]]:
        resp = self._client.embeddings.create(model=self.model, input=texts)
        return [d.embedding for d in resp.data]


def build_embedder(settings: Settings) -> EmbeddingClient | None:
    if settings.embedding_base_url and settings.embedding_model:
        return EmbeddingClient(settings)
    return None
