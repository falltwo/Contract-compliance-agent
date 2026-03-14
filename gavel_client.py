from __future__ import annotations

"""
Gavel (原 Documate) API client：用來依模板生成合約 PDF / Word。

設計目標：
- 只在這個模組集中處理 Gavel 的 HTTP 呼叫與錯誤處理
- 其他地方（例如未來的 company_tools / expert agent）只需要呼叫 generate_contract_file()

實際的 endpoint、payload 結構請依你的 Gavel 文件調整：
- 預設假設為 POST {GAVEL_BASE_URL}{GAVEL_GENERATE_PATH}
- body 形如：
    {
        "template_id": "...",
        "variables": {...},
        "output_format": "pdf" or "docx"
    }
你可依 Gavel 的正式文件修改此模組的 payload 組裝方式。
"""

import os
from typing import Any, Dict, Literal, Optional, Tuple

import httpx
from dotenv import load_dotenv


load_dotenv()


class GavelError(RuntimeError):
    """包裝 Gavel API 相關錯誤。"""


def _get_config() -> Tuple[str, str, str]:
    api_key = os.getenv("GAVEL_API_KEY") or ""
    base_url = os.getenv("GAVEL_BASE_URL", "https://api.gavel.io").rstrip("/")
    path = os.getenv("GAVEL_GENERATE_PATH", "/api/documents")
    if not api_key:
        raise GavelError("缺少 GAVEL_API_KEY，請在 .env 設定你的 Gavel API key。")
    if not path.startswith("/"):
        path = "/" + path
    return api_key, base_url, path


def generate_contract_file(
    *,
    template_id: str,
    variables: Dict[str, Any],
    output_format: Literal["pdf", "docx"] = "pdf",
    timeout_sec: float = 60.0,
) -> Tuple[bytes, str]:
    """
    呼叫 Gavel API 依模板產生合約檔案。

    回傳：(檔案位元組內容, content_type)，方便之後寫成檔案或直接回傳給前端下載。

    你可以依照自己的 Gavel API 文件調整 payload 鍵名，例如：
    - "template_id" / "template" / "workflow_id"
    - "variables" / "answers" / "data"
    """
    if output_format not in ("pdf", "docx"):
        raise ValueError("output_format 需為 'pdf' 或 'docx'")

    api_key, base_url, path = _get_config()
    url = f"{base_url}{path}"

    payload: Dict[str, Any] = {
        "template_id": template_id,
        "variables": variables,
        "output_format": output_format,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/octet-stream, application/pdf, application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    }

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.post(url, json=payload, headers=headers)
    except Exception as e:  # pragma: no cover - 連線錯誤路徑
        raise GavelError(f"Gavel API 連線失敗：{e!s}") from e

    if resp.status_code >= 400:
        # 嘗試讀取錯誤訊息
        detail: Optional[str] = None
        try:
            data = resp.json()
            detail = (data.get("message") or data.get("error") or "") if isinstance(data, dict) else None
        except Exception:
            pass
        msg = f"Gavel API 回傳錯誤狀態碼 {resp.status_code}"
        if detail:
            msg += f"：{detail}"
        raise GavelError(msg)

    content_type = resp.headers.get("content-type", "")
    return resp.content, content_type

