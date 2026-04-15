"""Chat model adapters and provider selection.

This module keeps the existing `client.models.generate_content(...) -> .text`
shape so upper layers (`agent_router`, `rag_graph`, Streamlit, FastAPI) do not
need to care which provider is active.
"""

from __future__ import annotations

import json
import os
from typing import Any, Tuple

from dotenv import load_dotenv


class _TextResponse:
    """Minimal response object compatible with Gemini usage sites."""

    def __init__(self, text: str):
        self.text = text


def _normalize_ollama_base_url(base_url: str) -> str:
    base = (base_url or "http://127.0.0.1:11434").rstrip("/")
    if not base.endswith("/v1"):
        base = f"{base}/v1"
    return base


def _extract_text_from_openai_message_content(content: Any) -> str:
    if isinstance(content, str):
        return content.strip()
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    parts.append(str(text))
                continue
            text = getattr(part, "text", None)
            if text:
                parts.append(str(text))
        return "\n".join(p.strip() for p in parts if p and p.strip())
    return str(content or "").strip()


def _normalize_contents(contents: Any) -> str:
    if isinstance(contents, str):
        return contents.strip()
    if contents is None:
        return ""
    try:
        return json.dumps(contents, ensure_ascii=False)
    except Exception:
        return str(contents)


class GroqAdapter:
    """Adapt Groq to the same interface used by Gemini callsites."""

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
    ) -> _TextResponse:
        system = ""
        if config is not None and hasattr(config, "system_instruction"):
            system = (config.system_instruction or "").strip()
        user_content = (contents or "").strip() if isinstance(contents, str) else ""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": user_content or "(empty prompt)"})

        model_name = model or self._default_model
        resp = self._client.chat.completions.create(
            model=model_name,
            messages=messages,
        )
        text = ""
        if resp.choices:
            msg = resp.choices[0].message
            if msg and getattr(msg, "content", None):
                text = (msg.content or "").strip()
        return _TextResponse(text=text)


class OllamaAdapter:
    """Use Ollama via OpenAI-compatible API while preserving Gemini-like shape."""

    def __init__(
        self,
        *,
        base_url: str,
        default_model: str = "gemma3:27b",
        api_key: str = "ollama",
        timeout_sec: float = 240.0,
    ):
        from openai import OpenAI

        self._client = OpenAI(
            base_url=_normalize_ollama_base_url(base_url),
            api_key=api_key,
            timeout=timeout_sec,
        )
        self._default_model = default_model

    @property
    def models(self) -> Any:
        return self

    def generate_content(
        self,
        model: str | None = None,
        contents: Any = None,
        config: Any = None,
        **kwargs: Any,
    ) -> _TextResponse:
        model_name = model or self._default_model
        prompt = _normalize_contents(contents)
        if not prompt:
            prompt = "(empty prompt)"

        system = ""
        if config is not None and hasattr(config, "system_instruction"):
            system = str(getattr(config, "system_instruction") or "").strip()

        messages: list[dict[str, str]] = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        req: dict[str, Any] = {
            "model": model_name,
            "messages": messages,
        }

        if config is not None:
            temperature = getattr(config, "temperature", None)
            top_p = getattr(config, "top_p", None)
            max_tokens = getattr(config, "max_output_tokens", None)
            response_mime_type = getattr(config, "response_mime_type", None)
            if temperature is not None:
                req["temperature"] = float(temperature)
            if top_p is not None:
                req["top_p"] = float(top_p)
            if max_tokens is not None:
                req["max_tokens"] = int(max_tokens)
            if response_mime_type == "application/json":
                req["response_format"] = {"type": "json_object"}

        resp = self._client.chat.completions.create(**req)
        text = ""
        if resp.choices:
            msg = resp.choices[0].message
            text = _extract_text_from_openai_message_content(getattr(msg, "content", None))
        return _TextResponse(text=text)


def get_chat_client_and_model() -> Tuple[Any, str]:
    """Return `(chat_client, model_name)` for configured provider.

    Providers:
    - `CHAT_PROVIDER=ollama`: local Ollama (recommended for DGX local deployment)
    - `EVAL_USE_GROQ=1` with `GROQ_API_KEY`: Groq
    - default fallback: Gemini
    """

    load_dotenv()

    chat_provider = os.getenv("CHAT_PROVIDER", "").strip().lower()
    if chat_provider in ("ollama", "local"):
        model = os.getenv("OLLAMA_CHAT_MODEL", "gemma3:27b").strip() or "gemma3:27b"
        base_url = os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434").strip() or "http://127.0.0.1:11434"
        timeout_sec = float(os.getenv("OLLAMA_TIMEOUT_SEC", "240").strip() or "240")
        api_key = os.getenv("OLLAMA_API_KEY", "ollama").strip() or "ollama"
        return OllamaAdapter(
            base_url=base_url,
            default_model=model,
            api_key=api_key,
            timeout_sec=timeout_sec,
        ), model

    use_groq = os.getenv("EVAL_USE_GROQ", "").strip().lower() in ("1", "true", "yes")
    groq_key = os.getenv("GROQ_API_KEY", "").strip()
    if use_groq and groq_key:
        model = os.getenv("GROQ_CHAT_MODEL", "llama-3.3-70b-versatile")
        return GroqAdapter(api_key=groq_key, default_model=model), model

    from google import genai

    google_api_key = os.getenv("GOOGLE_API_KEY")
    if not google_api_key:
        raise RuntimeError("Missing GOOGLE_API_KEY in .env (or set CHAT_PROVIDER=ollama).")
    model = os.getenv("GEMINI_CHAT_MODEL", "gemini-3.1-flash-lite-preview")
    client = genai.Client(api_key=google_api_key)
    return client, model

