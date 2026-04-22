from typing import Any, Literal

from pydantic import BaseModel, Field


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str


class ChatRequest(BaseModel):
    """對齊 `streamlit_app` 呼叫 `answer_with_rag_and_log` 時所帶參數。"""

    message: str = Field(..., min_length=1, description="本輪使用者問題（或一鍵審閱預設句）")
    top_k: int = Field(default=5, ge=1, le=20)
    history: list[ChatMessage] = Field(default_factory=list)
    strict: bool = Field(
        default=False,
        description="True 時等同側欄「嚴格只根據知識庫回答」；False 時走完整路由",
    )
    chat_id: str | None = None
    rag_scope_chat_id: str | None = Field(
        default=None,
        description="若設定，檢索僅限該對話上傳之 chunks（對齊「只搜尋此對話上傳的檔案」）",
    )
    active_source: str | None = Field(
        default=None,
        description="使用者目前正在預覽的文件 source 路徑；合約審閱時只分析此文件",
    )
    original_question: str | None = None
    clarification_reply: str | None = None
    chart_confirmation_question: str | None = None
    chart_confirmation_reply: str | None = None


class ChunkItem(BaseModel):
    """檢索片段；與 rag 流程 `chunks` 一致，允許額外欄位。"""

    model_config = {"extra": "allow"}

    tag: str = ""
    text: str = ""


class ChatResponse(BaseModel):
    answer: str
    sources: list[str]
    chunks: list[ChunkItem]
    tool_name: str
    extra: dict[str, Any] | None = None
    latency_sec: float = Field(..., description="本次呼叫耗時（秒），供觀測用")
    next_original_question_for_clarification: str | None = Field(
        default=None,
        description="當 tool_name 為 ask_web_vs_rag 時，下一輪請帶入 original_question",
    )
    next_chart_confirmation_question: str | None = Field(
        default=None,
        description="當本輪詢問是否產圖時，下一輪請帶入 chart_confirmation_question",
    )
