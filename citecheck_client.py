from __future__ import annotations

"""
CiteCheck AI client：用來檢查答案中的引文／來源是否有疑慮，降低虛假引文風險。

注意：
- 這是一個「範本實作」，實際的 endpoint / payload 結構請依 CiteCheck 官方文件調整。
- 若未設定 CITECHECK_API_KEY，check_citations() 會直接回傳「未啟用」，不影響主流程。
"""

import os
from typing import Any, Dict, List, Optional, Tuple

import httpx
from dotenv import load_dotenv


load_dotenv()


class CiteCheckError(RuntimeError):
    """包裝 CiteCheck API 相關錯誤。"""


def _get_config() -> Tuple[Optional[str], str, str]:
    api_key = os.getenv("CITECHECK_API_KEY") or None
    base_url = os.getenv("CITECHECK_BASE_URL", "https://api.citecheck.ai").rstrip("/")
    path = os.getenv("CITECHECK_VERIFY_PATH", "/api/verify")
    if not path.startswith("/"):
        path = "/" + path
    return api_key, base_url, path


def check_citations(
    *,
    answer: str,
    sources: List[str],
    chunks: List[Dict[str, Any]],
    timeout_sec: float = 30.0,
) -> Dict[str, Any]:
    """
    呼叫 CiteCheck AI 檢查答案中的引文。

    回傳統一結構，例如：
    {
        "enabled": bool,          # 是否有實際呼叫 CiteCheck
        "ok": bool | None,        # True=看起來都合理, False=有疑慮, None=未檢查
        "warning": str | None,    # 給前端顯示的小警語
        "detail": dict | None,    # 原始 CiteCheck 回應或解析後資訊
    }
    """
    api_key, base_url, path = _get_config()
    if not api_key:
        # 未啟用 CiteCheck：保持靜默，不中斷主流程
        return {
            "enabled": False,
            "ok": None,
            "warning": None,
            "detail": None,
        }

    url = f"{base_url}{path}"

    payload: Dict[str, Any] = {
        # 以下欄位名稱僅為示意，請依 CiteCheck 官方文件調整
        "answer": answer,
        "sources": sources,
        "chunks": chunks,
    }

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    try:
        with httpx.Client(timeout=timeout_sec) as client:
            resp = client.post(url, json=payload, headers=headers)
    except Exception as e:  # pragma: no cover - 連線錯誤路徑
        # 若 CiteCheck 出錯，不要影響主流程，只回傳警語給 log 用
        return {
            "enabled": True,
            "ok": None,
            "warning": f"CiteCheck 檢查時發生錯誤：{e!s}",
            "detail": None,
        }

    try:
        data = resp.json()
    except Exception:
        data = None

    if resp.status_code >= 400:
        msg = None
        if isinstance(data, dict):
            msg = (data.get("message") or data.get("error") or "") or None
        warning = f"CiteCheck 回傳錯誤狀態碼 {resp.status_code}"
        if msg:
            warning += f"：{msg}"
        return {
            "enabled": True,
            "ok": None,
            "warning": warning,
            "detail": data,
        }

    # 這裡假設 CiteCheck 回傳結構包含一個類似 "all_citations_valid" 的布林欄位
    ok: Optional[bool] = None
    warning: Optional[str] = None
    if isinstance(data, dict):
        ok_field = data.get("all_citations_valid")
        if isinstance(ok_field, bool):
            ok = ok_field
            if not ok:
                warning = "部分引文或來源經 CiteCheck 稽核有疑慮，請人工再次確認原始文件。"
        # 若 API 使用不同欄位名稱，可在此擴充解析邏輯

    return {
        "enabled": True,
        "ok": ok,
        "warning": warning,
        "detail": data,
    }

