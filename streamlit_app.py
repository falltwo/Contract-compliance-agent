import base64
import json
import os
import time
from collections import Counter
from io import BytesIO
from pathlib import Path
from typing import Any

import streamlit as st
from google import genai
from streamlit_echarts import st_echarts
from pypdf import PdfReader

from agent_router import route_and_answer
from eval_log import is_enabled as eval_log_enabled, load_runs, log_run as eval_log_run
from rag_common import (
    chunk_text,
    embed_texts,
    get_clients_and_index,
    stable_id,
)
from sources_registry import update_registry_on_ingest


def _inject_custom_css() -> None:
    """注入自訂 CSS：聊天氣泡、側欄層級、版面與動效，符合現代設計語彙。"""
    st.markdown(
        """
        <style>
        /* 主內容區：最大寬度與留白，提升可讀性 */
        .block-container {
            max-width: 900px;
            padding-top: 1.5rem;
            padding-bottom: 4rem;
        }
        /* 標題區下方留白 */
        h1 {
            letter-spacing: -0.02em;
            font-weight: 700;
            margin-bottom: 0.25rem !important;
        }
        /* 聊天訊息卡片：圓角、邊框、微陰影 */
        [data-testid="stChatMessage"] {
            padding: 1rem 1.25rem;
            border-radius: 16px;
            border: 1px solid rgba(148, 163, 184, 0.12);
            background: rgba(30, 41, 59, 0.6);
            margin-bottom: 0.75rem;
            backdrop-filter: blur(8px);
        }
        [data-testid="stChatMessage"] p {
            margin-bottom: 0.5em;
        }
        /* 使用者訊息靠右視覺區隔 */
        [data-testid="stChatMessage"]:has([data-testid="chatAvatarIcon-user"]) {
            margin-left: 2rem;
            border-color: rgba(20, 184, 166, 0.25);
            background: rgba(20, 184, 166, 0.06);
        }
        /* 側欄：區塊標題與分隔 */
        [data-testid="stSidebar"] .stMarkdown {
            margin-bottom: 0.25rem;
        }
        [data-testid="stSidebar"] h2 {
            font-size: 0.95rem !important;
            font-weight: 600;
            text-transform: uppercase;
            letter-spacing: 0.05em;
            color: #94A3B8;
            margin-top: 1.25rem;
            margin-bottom: 0.5rem;
        }
        [data-testid="stSidebar"] hr {
            margin: 1rem 0;
            border-color: rgba(148, 163, 184, 0.15);
        }
        /* 按鈕：主色與圓角 */
        [data-testid="stSidebar"] button {
            border-radius: 10px;
            font-weight: 500;
        }
        /* 展開器：較柔和邊框 */
        [data-testid="stExpander"] {
            border-radius: 12px;
            border: 1px solid rgba(148, 163, 184, 0.12);
            overflow: hidden;
        }
        [data-testid="stExpander"] summary {
            font-weight: 500;
        }
        /* 輸入框與滑桿在側欄的視覺 */
        [data-testid="stSidebar"] .stSlider label,
        [data-testid="stSidebar"] .stCheckbox label {
            font-size: 0.9rem;
        }
        /* 頁尾留白，避免輸入框擋住最後一則訊息 */
        .stChatInput {
            padding-bottom: 1rem;
        }
        /* 標題下方副標：編輯感 */
        .app-tagline {
            font-size: 0.95rem;
            color: #94A3B8;
            margin-bottom: 1.5rem;
            font-weight: 400;
        }
        /* Eval 頁：指標卡片 */
        [data-testid="stMetric"] {
            background: rgba(30, 41, 59, 0.5);
            padding: 1rem;
            border-radius: 12px;
            border: 1px solid rgba(148, 163, 184, 0.1);
        }
        [data-testid="stMetric"] label {
            color: #94A3B8 !important;
            font-size: 0.8rem !important;
        }
        </style>
        """,
        unsafe_allow_html=True,
    )


def _split_answer_and_refs(content: str) -> tuple[str, str | None]:
    """若回答內含「**參考連結：**」區塊，拆成主文與參考連結兩部分，否則回傳 (content, None)。"""
    if not content or "**參考連結：**" not in content:
        return (content or "", None)
    parts = content.split("**參考連結：**", 1)
    main_part = (parts[0] or "").strip()
    refs_part = (parts[1] or "").strip() if len(parts) > 1 else None
    return (main_part, refs_part if refs_part else None)


def _render_sources_expander(sources: list[str]) -> None:
    """將來源／參考連結以折疊區塊顯示。"""
    if not sources:
        return
    with st.expander("參考連結", expanded=False):
        for s in sources:
            st.markdown(f"- {s}")


def _render_chart_chunks(extra_or_msg: dict[str, Any] | None) -> None:
    """若有圖表依據的檢索片段，顯示可展開區塊「圖表依據的檢索片段（點擊展開）」。"""
    if not extra_or_msg:
        return
    chart_chunks = extra_or_msg.get("chart_chunks")
    if not chart_chunks or not isinstance(chart_chunks, list):
        return
    with st.expander("圖表依據的檢索片段（點擊展開）"):
        for c in chart_chunks:
            if isinstance(c, dict) and c.get("tag") is not None:
                st.markdown(f"**{c.get('tag', '')}**\n\n{c.get('text', '')}")
            else:
                st.markdown(str(c))


@st.cache_resource
def _cached_get_clients_and_index():
    """Streamlit 專用：快取 get_clients_and_index，避免每次重連。"""
    return get_clients_and_index()


def answer_with_rag(
    *,
    question: str,
    top_k: int,
    history: list[dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
    original_question: str | None = None,
    clarification_reply: str | None = None,
    chart_confirmation_question: str | None = None,
    chart_confirmation_reply: str | None = None,
) -> tuple[str, list[str], list[dict[str, Any]], str, dict[str, Any] | None]:
    """走總管 Agent，回傳 (answer, sources, chunks, tool_name, extra)。extra 在 create_chart 時為 {"chart_option": ...}，其餘見各 tool。"""
    answer, sources, chunks, tool_name, extra = route_and_answer(
        question=question,
        top_k=top_k,
        history=history or [],
        strict=strict,
        chat_id=chat_id,
        original_question=original_question,
        clarification_reply=clarification_reply,
        chart_confirmation_question=chart_confirmation_question,
        chart_confirmation_reply=chart_confirmation_reply,
    )
    return answer, sources, chunks, tool_name, extra


def _answer_with_rag_and_log(
    *,
    question: str,
    top_k: int,
    history: list[dict[str, Any]] | None = None,
    strict: bool = True,
    chat_id: str | None = None,
    original_question: str | None = None,
    clarification_reply: str | None = None,
    chart_confirmation_question: str | None = None,
    chart_confirmation_reply: str | None = None,
) -> tuple[str, list[str], list[dict[str, Any]], str, dict[str, Any] | None]:
    """呼叫 answer_with_rag 並計時；若啟用 Eval 記錄則寫入一筆 log。"""
    t0 = time.perf_counter()
    answer, sources, chunks, tool_name, extra = answer_with_rag(
        question=question,
        top_k=top_k,
        history=history or [],
        strict=strict,
        chat_id=chat_id,
        original_question=original_question,
        clarification_reply=clarification_reply,
        chart_confirmation_question=chart_confirmation_question,
        chart_confirmation_reply=chart_confirmation_reply,
    )
    latency = time.perf_counter() - t0
    if eval_log_enabled():
        eval_log_run(
            question=question,
            answer=answer or "",
            tool_name=tool_name,
            latency_sec=latency,
            top_k=top_k,
            source_count=len(sources),
            chat_id=chat_id,
        )
    return answer, sources, chunks, tool_name, extra


def _render_eval_view() -> None:
    """Eval 運行記錄頁：讀取 log、篩選、表格、展開看詳情。"""
    st.subheader("Eval 運行記錄")
    if not eval_log_enabled():
        st.info("請在 .env 設定 `EVAL_LOG_ENABLED=1` 並重新執行問答，才會寫入記錄。日誌路徑：`EVAL_LOG_PATH`（預設 eval_runs.jsonl）。")
    runs = load_runs(limit=500)
    if not runs:
        st.caption("尚無記錄。")
        return
    # 篩選區：用 container + 小標包起來，層級更清楚
    with st.container():
        st.caption("**篩選**")
        col1, col2 = st.columns(2)
        with col1:
            tool_filter = st.selectbox(
                "Tool",
                options=["全部"] + sorted({r.get("tool_name") or "" for r in runs if r.get("tool_name")}),
                key="eval_tool_filter",
            )
        with col2:
            keyword = st.text_input("問題關鍵字", key="eval_keyword", placeholder="留空不篩選")
        if tool_filter and tool_filter != "全部":
            runs = [r for r in runs if r.get("tool_name") == tool_filter]
        if keyword.strip():
            runs = [r for r in runs if keyword.strip() in (r.get("question") or "")]
        st.caption(f"共 {len(runs)} 筆（顯示最近 500 筆）")
    for i, r in enumerate(runs):
        ts = r.get("timestamp", "")[:19] if r.get("timestamp") else ""
        tool_name = r.get("tool_name") or ""
        lat = r.get("latency_sec")
        lat_str = f"{lat:.1f}s" if isinstance(lat, (int, float)) else ""
        q = (r.get("question") or "")[:80] + ("…" if len(r.get("question") or "") > 80 else "")
        with st.expander(f"{ts} | {tool_name} | {lat_str} | {q}"):
            st.markdown("**問題**")
            st.text(r.get("question") or "")
            st.markdown("**回答**")
            st.text_area("", value=(r.get("answer") or "")[:3000], height=120, disabled=True, key=f"eval_ans_{i}")
            st.caption(f"Tool: {tool_name} | 延遲: {lat_str} | top_k: {r.get('top_k')} | 來源數: {r.get('source_count')}")


def _render_eval_batch_view() -> None:
    """Eval 批次結果頁：讀取 eval/runs/*.jsonl，選 run 後顯示每題問題與回答。"""
    st.subheader("Eval 批次結果")
    runs_dir = Path(os.getenv("EVAL_RUNS_DIR", "eval/runs"))
    if not runs_dir.is_dir():
        st.info(f"尚無批次結果目錄：`{runs_dir}`。請先執行 `uv run python eval/run_eval.py`（可加 `--groq`）產生結果。")
        return

    results_files = sorted(runs_dir.glob("run_*_results.jsonl"), key=lambda p: p.name, reverse=True)
    if not results_files:
        st.info(f"目錄 `{runs_dir}` 中沒有找到 run_*_results.jsonl 檔案。")
        return

    run_options = [f.stem.replace("_results", "") for f in results_files]
    selected = st.selectbox("選擇一次 Eval Run", options=run_options, key="eval_batch_run")
    if not selected:
        return

    results_path = runs_dir / f"{selected}_results.jsonl"
    metrics_path = runs_dir / f"{selected}_metrics.json"
    if not results_path.exists():
        st.warning(f"找不到 {results_path}")
        return

    with st.spinner("載入 Run…"):
        results: list[dict[str, Any]] = []
        with results_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    results.append(json.loads(line))
                except json.JSONDecodeError:
                    continue

        metrics: dict[str, Any] = {}
        if metrics_path.exists():
            try:
                metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
            except Exception:
                pass

    if metrics:
        st.caption("核心指標")
        c1, c2, c3, c4 = st.columns(4)
        with c1:
            st.metric("總題數", metrics.get("total", 0))
        with c2:
            acc = metrics.get("routing_accuracy")
            n = metrics.get("routing_accuracy_n", 0)
            st.metric("Routing 準確率", f"{acc}%" if acc is not None else "—", f"n={n}")
        with c3:
            rate = metrics.get("tool_success_rate")
            st.metric("Tool 成功率", f"{rate}%" if rate is not None else "—")
        with c4:
            p95 = metrics.get("latency_p95_sec")
            st.metric("Latency P95", f"{p95}s" if p95 is not None else "—")

    st.divider()
    st.caption("各題結果（可展開看問題與回答）")
    for idx, r in enumerate(results):
        rid = r.get("id", "")
        q = (r.get("question") or "")[:60] + ("…" if len(r.get("question") or "") > 60 else "")
        pred = r.get("predicted_tool") or "—"
        exp = r.get("expected_tool") or "—"
        ok = "✓" if r.get("success") else "✗"
        lat = r.get("latency_sec")
        lat_str = f"{lat}s" if isinstance(lat, (int, float)) else "—"
        label = f"#{rid} {ok} {pred} ({lat_str}) | {q}"
        with st.expander(label):
            st.markdown("**問題**")
            st.text(r.get("question") or "")
            st.markdown("**預期 Tool / 實際 Tool**")
            st.text(f"{exp} → {pred}")
            st.markdown("**回答**")
            answer_text = r.get("answer")
            if answer_text is None or (isinstance(answer_text, str) and not answer_text.strip()):
                answer_text = "(此 run 未記錄答案內容，僅有 answer_len)"
                if r.get("answer_len") is not None:
                    answer_text += f" 字數：{r.get('answer_len')}"
            st.text_area("", value=answer_text, height=180, disabled=True, key=f"batch_ans_{selected}_{idx}")
            if r.get("error"):
                st.caption(f"錯誤：{r.get('error')[:500]}")


def ingest_uploaded_files(
    *,
    embed_client: genai.Client,
    index: Any,
    index_dim: int,
    embed_model: str,
    uploaded_files: list[Any],
    chat_id: str | None = None,
) -> int:
    # 只支援純文字（.txt/.md）；其他格式先不處理，避免額外依賴
    all_sources: list[str] = []
    all_texts: list[str] = []
    all_chunk_indexes: list[int] = []
    all_ids: list[str] = []

    for uf in uploaded_files:
        name = getattr(uf, "name", "uploaded")
        lower_name = name.lower()
        if not (lower_name.endswith(".txt") or lower_name.endswith(".md") or lower_name.endswith(".pdf")):
            continue

        raw = uf.getvalue()
        if lower_name.endswith(".pdf"):
            try:
                reader = PdfReader(BytesIO(raw))
                pages_text: list[str] = []
                for page in reader.pages:
                    t = page.extract_text() or ""
                    if t:
                        pages_text.append(t)
                text = "\n\n".join(pages_text)
            except Exception:
                text = ""
        else:
            text = raw.decode("utf-8", errors="ignore")

        if not text.strip():
            continue
        parts = chunk_text(text)
        if chat_id:
            source = f"uploaded/{chat_id}/{name}"
        else:
            source = f"uploaded/{name}"

        for i, part in enumerate(parts):
            all_sources.append(source)
            all_texts.append(part)
            all_chunk_indexes.append(i)
            all_ids.append(stable_id(source, i, part))

    if not all_texts:
        return 0

    vectors = embed_texts(
        embed_client,
        all_texts,
        model=embed_model,
        output_dimensionality=index_dim,
    )

    batch_size = 100
    for i in range(0, len(all_texts), batch_size):
        to_upsert = []
        for j in range(i, min(len(all_texts), i + batch_size)):
            metadata = {
                "text": all_texts[j],
                "source": all_sources[j],
                "chunk_index": all_chunk_indexes[j],
            }
            if chat_id is not None:
                metadata["chat_id"] = chat_id
            to_upsert.append((all_ids[j], vectors[j], metadata))
        index.upsert(vectors=to_upsert)

    source_counts = Counter(all_sources)
    update_registry_on_ingest(
        [
            {"source": s, "chunk_count": c, "chat_id": chat_id}
            for s, c in source_counts.items()
        ]
    )
    return len(all_texts)


def main() -> None:
    st.set_page_config(
        page_title="Agent-DEMO",
        page_icon="🔎",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    _inject_custom_css()
    st.title("Agent-DEMO")
    st.markdown(
        '<p class="app-tagline">RAG · 圖表 · 知識庫問答 · 多輪對話</p>',
        unsafe_allow_html=True,
    )

    try:
        chat_client, embed_client, index, index_dim, llm_model, embed_model, index_name = _cached_get_clients_and_index()
    except Exception as e:
        st.error(f"初始化失敗：{e}")
        st.stop()

    # 初始化多對話狀態
    if "conversations" not in st.session_state:
        st.session_state.conversations = {}
    if "active_conv_id" not in st.session_state or st.session_state.active_conv_id not in st.session_state.conversations:
        first_id = "chat-1"
        st.session_state.conversations[first_id] = {"title": "新對話", "messages": []}
        st.session_state.active_conv_id = first_id

    conversations = st.session_state.conversations
    active_conv_id = st.session_state.active_conv_id
    current_conv = conversations[active_conv_id]

    with st.sidebar:
        view = st.radio("畫面", ["對話", "Eval 運行記錄", "Eval 批次結果"], key="nav_view")
        st.subheader("設定")
        st.caption(f"Pinecone index：`{index_name}`（dim={index_dim}）")
        st.caption(f"Chat model：`{llm_model}`")
        st.caption(f"Embed model：`{embed_model}`")
        top_k = st.slider("TOP_K", min_value=1, max_value=20, value=int(os.getenv("TOP_K", "5")), step=1)
        strict_mode = st.checkbox("嚴格只根據知識庫回答", value=True)

        st.divider()
        st.subheader("對話")
        conv_ids = list(conversations.keys())
        current_index = conv_ids.index(active_conv_id)
        selected_id = st.radio(
            "選擇對話",
            options=conv_ids,
            index=current_index,
            format_func=lambda cid: conversations[cid].get("title") or "未命名對話",
        )
        if selected_id != active_conv_id:
            st.session_state.active_conv_id = selected_id
            st.rerun()

        if st.button("＋ 新對話", use_container_width=True):
            new_id = f"chat-{len(conversations) + 1}"
            conversations[new_id] = {"title": "新對話", "messages": []}
            st.session_state.active_conv_id = new_id
            st.rerun()

        if st.button("清除此對話", use_container_width=True):
            # 刪除目前對話欄位本身
            if len(conversations) > 1:
                conversations.pop(active_conv_id, None)
                # 切到剩餘的第一個對話
                st.session_state.active_conv_id = next(iter(conversations.keys()))
            else:
                # 若只剩一個，則重置成新的空對話
                conversations[active_conv_id] = {"title": "新對話", "messages": []}
            st.rerun()

    if view == "Eval 運行記錄":
        _render_eval_view()
        return
    if view == "Eval 批次結果":
        _render_eval_batch_view()
        return

    if "messages" not in current_conv:
        current_conv["messages"] = []

    # 空對話時顯示引導文案
    if not current_conv["messages"]:
        st.info("在下方輸入問題，或先展開「為此對話上傳並灌入文件」上傳 .txt / .md / .pdf 灌入知識庫後再問答。")
        st.markdown("")  # 小留白

    # 整理給模型用的對話歷史（只保留 role + content）
    history_for_model: list[dict[str, Any]] = []
    for i, msg in enumerate(current_conv["messages"]):
        role = msg.get("role")
        content = (msg.get("content") or "").strip()
        if role in ("user", "assistant") and content:
            history_for_model.append({"role": role, "content": content})
        with st.chat_message(msg["role"]):
            main_content, refs_content = _split_answer_and_refs(msg.get("content") or "")
            st.markdown(main_content or "(空)")
            if refs_content:
                with st.expander("參考連結", expanded=False):
                    st.markdown(refs_content)
            if msg.get("chart_image_base64"):
                try:
                    st.image(BytesIO(base64.b64decode(msg["chart_image_base64"])), use_container_width=True)
                except Exception:
                    pass
            elif msg.get("chart_option"):
                st_echarts(options=msg["chart_option"], height="400px")
            _render_chart_chunks(msg)
            if msg.get("sources"):
                _render_sources_expander(msg["sources"])
            if msg.get("chunks"):
                with st.expander("查看檢索片段"):
                    for c in msg["chunks"]:
                        st.markdown(f"**{c['tag']}**\n\n{c['text']}")

    with st.expander("為此對話上傳並灌入文件"):
        st.caption("支援 `.txt` / `.md` / `.pdf`。上傳後按「灌入到向量庫」，即可立刻用來問答。")
        uploads = st.file_uploader(
            "選擇檔案",
            type=["txt", "md", "pdf"],
            accept_multiple_files=True,
            key=f"uploads-{active_conv_id}",
        )
        if st.button(
            "灌入到向量庫",
            use_container_width=True,
            disabled=not uploads,
            key=f"ingest-{active_conv_id}",
        ):
            try:
                with st.spinner("向量化並寫入 Pinecone 中…（檔案越大越久）"):
                    n = ingest_uploaded_files(
                        embed_client=embed_client,
                        index=index,
                        index_dim=index_dim,
                        embed_model=embed_model,
                        uploaded_files=list(uploads or []),
                        chat_id=active_conv_id,
                    )
                if n == 0:
                    st.warning("沒有可灌入的內容（請確認檔案不是空的）。")
                else:
                    st.success(f"已灌入 {n} 個 chunks，可直接在下方問答。")
            except Exception as e:
                st.error(f"灌入失敗：{e}")

    question = st.chat_input("輸入你的問題…")
    if not question:
        return

    current_conv["messages"].append({"role": "user", "content": question})
    with st.chat_message("user"):
        st.markdown(question)

    # 若上一輪已問「需要幫我生成圖表嗎？」，本輪使用者說要 → 直接產圖
    pending_chart = current_conv.pop("pending_chart_question", None)
    if pending_chart is not None:
        with st.chat_message("assistant"):
            with st.spinner("正在生成圖表…"):
                answer, sources, chunks, tool_name, extra = _answer_with_rag_and_log(
                    question=question,
                    top_k=top_k,
                    history=history_for_model,
                    strict=strict_mode,
                    chat_id=active_conv_id,
                    chart_confirmation_question=pending_chart,
                    chart_confirmation_reply=question,
                )
            main_content, refs_content = _split_answer_and_refs(answer or "")
            st.markdown(main_content or "(空回覆)")
            if refs_content:
                with st.expander("參考連結", expanded=False):
                    st.markdown(refs_content)
            if extra and extra.get("chart_image_base64"):
                try:
                    st.image(BytesIO(base64.b64decode(extra["chart_image_base64"])), use_container_width=True)
                except Exception:
                    pass
            elif extra and extra.get("chart_option"):
                st_echarts(options=extra["chart_option"], height="400px")
            _render_chart_chunks(extra)
            if sources:
                _render_sources_expander(sources)
            if chunks:
                with st.expander("查看檢索片段"):
                    for c in chunks:
                        st.markdown(f"**{c['tag']}**\n\n{c['text']}")
        current_conv["messages"].append({
            "role": "assistant",
            "content": answer or "(空回覆)",
            "sources": sources,
            "chunks": chunks,
            "chart_option": (extra or {}).get("chart_option"),
            "chart_image_base64": (extra or {}).get("chart_image_base64"),
            "chart_chunks": (extra or {}).get("chart_chunks"),
            "chart_sources": (extra or {}).get("chart_sources"),
        })
        return

    # 若上一輪是「知識庫 vs 網路」澄清，本輪使用使用者的回覆決定執行哪個 tool
    pending = current_conv.pop("pending_web_vs_rag_question", None)
    if pending is not None:
        with st.chat_message("assistant"):
            with st.spinner("依您的選擇執行中…"):
                answer, sources, chunks, tool_name, extra = _answer_with_rag_and_log(
                    question=question,
                    top_k=top_k,
                    history=history_for_model,
                    strict=strict_mode,
                    chat_id=active_conv_id,
                    original_question=pending,
                    clarification_reply=question,
                )
            main_content, refs_content = _split_answer_and_refs(answer or "")
            st.markdown(main_content or "(空回覆)")
            if refs_content:
                with st.expander("參考連結", expanded=False):
                    st.markdown(refs_content)
            if extra and extra.get("chart_image_base64"):
                try:
                    st.image(BytesIO(base64.b64decode(extra["chart_image_base64"])), use_container_width=True)
                except Exception:
                    pass
            elif extra and extra.get("chart_option"):
                st_echarts(options=extra["chart_option"], height="400px")
            _render_chart_chunks(extra)
            if sources:
                _render_sources_expander(sources)
            if chunks:
                with st.expander("查看檢索片段"):
                    for c in chunks:
                        st.markdown(f"**{c['tag']}**\n\n{c['text']}")
        current_conv["messages"].append({
            "role": "assistant",
            "content": answer or "(空回覆)",
            "sources": sources,
            "chunks": chunks,
            "chart_option": (extra or {}).get("chart_option"),
            "chart_image_base64": (extra or {}).get("chart_image_base64"),
            "chart_chunks": (extra or {}).get("chart_chunks"),
            "chart_sources": (extra or {}).get("chart_sources"),
        })
        return

    with st.chat_message("assistant"):
        with st.spinner("檢索並生成答案中…"):
            answer, sources, chunks, tool_name, extra = _answer_with_rag_and_log(
                question=question,
                top_k=top_k,
                history=history_for_model,
                strict=strict_mode,
                chat_id=active_conv_id,
            )
        main_content, refs_content = _split_answer_and_refs(answer or "")
        st.markdown(main_content or "(空回覆)")
        if refs_content:
            with st.expander("參考連結", expanded=False):
                st.markdown(refs_content)
        if extra and extra.get("chart_image_base64"):
            try:
                st.image(BytesIO(base64.b64decode(extra["chart_image_base64"])), use_container_width=True)
            except Exception:
                pass
        elif extra and extra.get("chart_option"):
            st_echarts(options=extra["chart_option"], height="400px")
        _render_chart_chunks(extra)
        if sources:
            _render_sources_expander(sources)
        if chunks:
            with st.expander("查看檢索片段"):
                for c in chunks:
                    st.markdown(f"**{c['tag']}**\n\n{c['text']}")

    # 若本輪是「意圖模糊」追問，下一輪要帶 original_question + clarification_reply
    if tool_name == "ask_web_vs_rag":
        current_conv["pending_web_vs_rag_question"] = question
    # 若本輪是「分析並詢問是否產圖」，下一輪使用者說「要」時會走 chart_confirmation 產圖
    if tool_name == "analyze_and_chart" and extra and extra.get("asked_chart_confirmation"):
        current_conv["pending_chart_question"] = extra.get("chart_query") or question

    current_conv["messages"].append({
        "role": "assistant",
        "content": answer or "(空回覆)",
        "sources": sources,
        "chunks": chunks,
        "chart_option": (extra or {}).get("chart_option"),
        "chart_image_base64": (extra or {}).get("chart_image_base64"),
        "chart_chunks": (extra or {}).get("chart_chunks"),
        "chart_sources": (extra or {}).get("chart_sources"),
    })


if __name__ == "__main__":
    main()

