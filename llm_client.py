"""共用 LLM 客戶端：支援 Gemini 與 Groq（Eval 時可選 Groq 避開免費額度限制）。"""

from __future__ import annotations

import os
from typing import Any, Tuple

from dotenv import load_dotenv


class _GroqResponse:
    """模擬 Gemini generate_content 回傳的 .text 介面。"""

    def __init__(self, text: str):
        self.text = text


class GroqAdapter:
    """適配 Groq API，提供與 Gemini 相同的 generate_content 介面（供 agent_router / rag_graph 等呼叫）。"""

    def __init__(self, api_key: str, default_model: str = "llama-3.3-70b-versatile"):
        from groq import Groq

        self._client = Groq(api_key=api_key)
        self._default_model = default_model

    @property
    def models(self) -> Any:
        return self

    def generate_content(
        self,
        model: str | None = None,
        contents: str | None = None,
        config: Any = None,
        **kwargs: Any,
    ) -> _GroqResponse:
        """Groq chat completion，回傳帶 .text 的物件。"""
        system = ""
        if config is not None and hasattr(config, "system_instruction"):
            system = (config.system_instruction or "").strip()
        user_content = (contents or "").strip() if isinstance(contents, str) else ""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content or "(無內容)"})

        model_name = model or self._default_model
        resp = self._client.chat.completions.create(
            model=model_name,
            messages=messages,
        )
        text = ""
        if resp.choices and len(resp.choices) > 0:
            msg = resp.choices[0].message
            if msg and getattr(msg, "content", None):
                text = (msg.content or "").strip()
        return _GroqResponse(text=text)


def get_chat_client_and_model() -> Tuple[Any, str]:
    """回傳 (chat_client, model_name)。  
    EVAL_USE_GROQ=1 且 GROQ_API_KEY 有設時用 Groq，否則用 Gemini。  
    chat_client 具備 .models.generate_content(model, contents, config) 且回傳 .text。
    """
    load_dotenv()
    use_groq = os.getenv("EVAL_USE_GROQ", "").strip().lower() in ("1", "true", "yes")
    groq_key = os.getenv("GROQ_API_KEY", "").strip()

    if use_groq and groq_key:
        model = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
        return GroqAdapter(api_key=groq_key, default_model=model), model

    from google import genai

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise RuntimeError("缺少環境變數 GOOGLE_API_KEY（請放在 .env）")
    model = os.getenv("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
    client = genai.Client(api_key=google_api_key)
    return client, model
