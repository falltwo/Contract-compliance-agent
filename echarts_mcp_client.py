"""從 Python 呼叫 ECharts MCP server（npx mcp-echarts）產生圖表。

需安裝 mcp 套件、Node.js 18+ 與 npx。設 USE_ECHARTS_MCP=1 時，Streamlit 圖表路徑會優先嘗試此 MCP。
回傳 PNG base64 或錯誤訊息；Streamlit 端用 st.image(io.BytesIO(base64.b64decode(...))) 顯示。
"""
from __future__ import annotations

import asyncio
import base64
import json
import os
import sys
from typing import Any

from dotenv import load_dotenv


def _is_windows() -> bool:
    return sys.platform.startswith("win")


def _server_params() -> tuple[str, list[str]]:
    """(command, args) 與 .cursor/mcp.json 一致，讓 Streamlit 端可呼叫同一支 MCP。"""
    load_dotenv()
    if _is_windows():
        return "cmd", ["/c", "npx", "-y", "mcp-echarts"]
    return "npx", ["-y", "mcp-echarts"]


async def _call_echarts_mcp_async(
    echarts_option: dict[str, Any],
    *,
    width: int = 800,
    height: int = 500,
    output_type: str = "png",
) -> tuple[bool, str | None, str | None]:
    """呼叫 ECharts MCP 的 generate_echarts，回傳 (成功?, base64 或 url, 錯誤訊息)。

    output_type: png | svg | option。png 時回傳 base64 字串（data:image/png;base64,... 或純 base64）。
    """
    try:
        from mcp import ClientSession
        from mcp.client.stdio import StdioServerParameters, stdio_client
        from mcp.types import ImageContent, TextContent
    except ImportError as e:
        return False, None, f"未安裝 mcp 套件，無法呼叫 ECharts MCP：{e!s}"

    command, args = _server_params()
    server_params = StdioServerParameters(command=command, args=args)
    option_str = json.dumps(echarts_option, ensure_ascii=False)

    try:
        async with stdio_client(server_params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.call_tool(
                    "generate_echarts",
                    arguments={
                        "echartsOption": option_str,
                        "width": width,
                        "height": height,
                        "outputType": output_type,
                        "theme": "default",
                    },
                )
                if getattr(result, "isError", False):
                    err = ""
                    for c in getattr(result, "content", []) or []:
                        if isinstance(c, TextContent):
                            err += c.text
                    return False, None, err or "MCP 回傳錯誤"

                out_b64: str | None = None
                for content in getattr(result, "content", []) or []:
                    if isinstance(content, ImageContent):
                        data = getattr(content, "data", None)
                        if data is not None:
                            out_b64 = data if isinstance(data, str) else base64.b64encode(data).decode("ascii")
                        break
                    if isinstance(content, TextContent):
                        text = (content.text or "").strip()
                        if text.startswith("data:image"):
                            out_b64 = text.split(",", 1)[-1] if "," in text else text
                        elif text:
                            out_b64 = text
                        break
                if out_b64:
                    return True, out_b64, None
                return False, None, "MCP 未回傳圖片內容"
    except asyncio.TimeoutError:
        return False, None, "呼叫 ECharts MCP 逾時（請確認 Node.js/npx 可用）"
    except Exception as e:
        return False, None, f"呼叫 ECharts MCP 失敗：{e!s}"


def call_echarts_mcp(
    echarts_option: dict[str, Any],
    *,
    width: int = 800,
    height: int = 500,
    output_type: str = "png",
) -> tuple[bool, str | None, str | None]:
    """同步包裝：呼叫 ECharts MCP 產生圖表。回傳 (成功?, base64 或 None, 錯誤訊息)。"""
    return asyncio.run(
        _call_echarts_mcp_async(
            echarts_option,
            width=width,
            height=height,
            output_type=output_type,
        )
    )


def use_echarts_mcp() -> bool:
    """是否啟用 ECharts MCP（由環境變數控制）。"""
    load_dotenv()
    return os.getenv("USE_ECHARTS_MCP", "").strip().lower() in ("1", "true", "yes")
