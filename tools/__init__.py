"""Agent 工具層：集中管理 tool 名稱與日後可擴充的 handler。

目前執行邏輯仍在 agent_router.route_and_answer；此包提供單一來源的 tool 清單，
供 router 驗證與文件使用。未來可將各 tool 的執行函式遷入此包（如 tools.rag, tools.web）。
"""
from agent_router import SUPPORTED_TOOLS

__all__ = ["SUPPORTED_TOOLS"]
