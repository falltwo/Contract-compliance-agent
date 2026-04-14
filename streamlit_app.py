import base64
import json
import os
import time
from collections import Counter
from datetime import date
from io import BytesIO
from pathlib import Path
from typing import Any

import streamlit as st
from dotenv import load_dotenv
from google import genai
from google.genai import types
from streamlit_echarts import st_echarts

from agent_router import route_and_answer
from approval_workflow import (
    apply_step_action,
    build_default_reviewer_specs,
    create_approval_request,
    ensure_single_user_workflow,
    get_current_step,
    mark_sent_for_signature,
    mark_signed,
    restart_after_changes,
    update_obligation_statuses,
)
from contract_drafting import (
    apply_clause_updates,
    get_template,
    list_templates,
    render_template,
    summarize_redline,
)
from document_processing import build_contract_diff, parse_uploaded_document
from eval_log import is_enabled as eval_log_enabled, load_runs, log_run as eval_log_run
from knowledge_base_jobs import load_jobs
from knowledge_base_sync import dataset_stats, ingest_dataset, sync_records_from_file, sync_records_from_json_text
from llm_client import get_chat_client_and_model
from rag_common import (
    chunk_text,
    embed_texts,
    get_clients_and_index,
    stable_id,
)
from rag_common import append_bm25_corpus
from rag_graph import retrieve_only
from sources_registry import load_registry, save_registry, list_sources, update_registry_on_ingest


def _inject_custom_css() -> None:
    """注入自訂 CSS：從 assets/custom.css 讀取，若無則不注入。"""
    css_path = Path(__file__).resolve().parent / "assets" / "custom.css"
    if css_path.is_file():
        st.markdown(
            f"<style>\n{css_path.read_text(encoding='utf-8')}\n</style>",
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


def _workflow_store_key(chat_id: str) -> str:
    return f"approval-workflow-{chat_id}"


def _clean_source_name(source: str) -> str:
    if not source:
        return ""
    return source.replace("\\", "/").split("/")[-1]


def _derive_contract_context(active_conv_id: str) -> dict[str, str]:
    sources = list_sources(chat_id=active_conv_id)
    source_names = [_clean_source_name(item.get("source", "")) for item in sources if item.get("source")]
    source_names = [name for name in source_names if name]
    primary_source = source_names[0] if source_names else ""
    template_name = st.session_state.get(f"draft-template-name-{active_conv_id}", "")
    if primary_source:
        contract_title = f"{primary_source} 審閱案件"
        source_label = primary_source
        source_hint = "案件名稱已優先使用你上傳的合約檔名。"
    elif template_name:
        contract_title = f"{template_name} 草稿審閱案件"
        source_label = f"{template_name} 草稿"
        source_hint = "目前沒有偵測到上傳檔案，案件名稱改用你剛產生的草稿名稱。"
    else:
        contract_title = "目前草稿審閱案件"
        source_label = "目前草稿"
        source_hint = "目前沒有偵測到上傳檔案，因此案件名稱會以目前草稿為主。"
    return {
        "contract_title": contract_title,
        "source_label": source_label,
        "source_hint": source_hint,
    }


def _resolve_approval_base_text(active_conv_id: str, draft_text: str) -> tuple[str, str]:
    cleaned_draft = (draft_text or "").strip()
    if cleaned_draft:
        return cleaned_draft, "draft"
    contract_context = _derive_contract_context(active_conv_id)
    source_label = contract_context.get("source_label", "")
    if not source_label:
        return "", "none"
    context, _sources, chunks, _top_score = retrieve_only(
        question=f"合約全文 {source_label}",
        top_k=8,
        chat_id=active_conv_id,
    )
    if chunks:
        return context.strip(), "uploaded_file"
    return "", "none"


def _approval_status_label(status: str) -> str:
    mapping = {
        "draft": "草稿中",
        "in_review": "審閱中",
        "changes_requested": "待修改",
        "approved": "已核准",
        "rejected": "已拒絕",
        "sent_for_signature": "送簽中",
        "signed": "已簽署",
    }
    return mapping.get(status, status or "未開始")


def _step_status_label(status: str) -> str:
    mapping = {
        "pending": "尚未開始",
        "reviewing": "請你現在處理",
        "approved": "已完成",
        "changes_requested": "已退回修改",
        "rejected": "已拒絕",
    }
    return mapping.get(status, status or "未開始")


def _render_approval_next_action(workflow: dict, current_step: dict | None) -> None:
    status = workflow.get("status", "")
    if status == "in_review" and current_step:
        st.info(
            "你現在要做的事：先閱讀下方「法律專家審閱重點」，在「審閱意見」欄位寫下判斷，"
            "再選擇「核准」、「退回修改」或「拒絕」。"
        )
        return
    if status == "changes_requested":
        st.warning("你現在要做的事：先修改上方合約草稿，確認內容更新後，再按「修訂後重新送審」。")
        return
    if status == "approved":
        st.success("你現在要做的事：這份合約已通過審閱，可以直接送交電子簽署。")
        return
    if status == "sent_for_signature":
        st.info("你現在要做的事：等待簽署完成，若已完成可在下方按「標記已簽署」。")
        return
    if status == "signed":
        st.success("這份合約已完成簽署。接下來可查看下方的到期提醒與義務追蹤。")
        return


def _build_ai_approval_review(
    *,
    chat_client: Any,
    llm_model: str,
    active_conv_id: str,
    draft_text: str,
    legal_focus: str,
    source_label: str,
) -> dict[str, Any]:
    query = f"合約審閱 {source_label} {legal_focus}".strip()
    context, sources, chunks, _top_score = retrieve_only(
        question=query,
        top_k=6,
        chat_id=active_conv_id,
    )
    if not chunks:
        return {
            "status": "unavailable",
            "summary": "目前沒有檢索到本對話上傳文件內容，因此無法產出受 RAG 約束的 AI 審閱分析。",
            "sources": [],
        }

    draft_excerpt = (draft_text or "").strip()
    if len(draft_excerpt) > 1800:
        draft_excerpt = draft_excerpt[:1800] + "..."
    prompt = (
        "你是法律審閱助理。只能根據『RAG 檢索內容』與『目前草稿摘錄』做分析，"
        "不可補充未出現在內容中的事實，不可憑空引用法條。\n\n"
        f"## 審閱焦點\n{legal_focus or '一般商務合約審閱'}\n\n"
        f"## 目前草稿摘錄\n{draft_excerpt or '（無草稿內容）'}\n\n"
        f"## RAG 檢索內容\n{context}\n\n"
        "請輸出 2 到 4 點精簡條列：\n"
        "1. 只寫本份合約目前最值得注意的風險或缺漏\n"
        "2. 每點盡量指出依據來源 tag，例如 source#chunk1\n"
        "3. 若資料不足，直接說資料不足，不要猜測\n"
        "4. 請用繁體中文"
    )
    try:
        out = chat_client.models.generate_content(
            model=llm_model,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction="你只能依據提供的檢索內容與草稿摘錄回答，不可超出 RAG 範圍。"
            ),
        )
        text = (out.text or "").strip()
    except Exception as e:
        return {
            "status": "error",
            "summary": f"AI 審閱分析暫時無法產出：{e}",
            "sources": sources,
        }
    return {
        "status": "ok",
        "summary": text or "AI 審閱分析沒有產出內容。",
        "sources": sources,
    }


def _render_approval_workflow(
    active_conv_id: str,
    draft_text: str,
    *,
    chat_client: Any,
    llm_model: str,
) -> None:
    key = _workflow_store_key(active_conv_id)
    workflow = st.session_state.get(key)
    approval_base_text, approval_input_mode = _resolve_approval_base_text(active_conv_id, draft_text)
    if workflow:
        workflow = ensure_single_user_workflow(workflow)
        workflow = update_obligation_statuses(workflow)
        st.session_state[key] = workflow

    with st.expander("審批工作流", expanded=False):
        st.caption("目前以單一使用者模式為主，由你以法律專家視角完成審閱、送簽與後續追蹤。")
        if not approval_base_text.strip():
            st.info("請先上傳並灌入合約，或在上方生成/編修合約草稿，才能建立送審流程。")
            return

        if not workflow:
            contract_context = _derive_contract_context(active_conv_id)
            contract_title = st.text_input(
                "送審標題",
                value=contract_context["contract_title"],
                key=f"approval-title-{active_conv_id}",
            )
            st.caption(f"來源合約：{contract_context['source_label']}")
            st.caption(contract_context["source_hint"])
            if approval_input_mode == "uploaded_file":
                st.caption("目前會直接以你已上傳並灌入的合約內容建立送審流程。")
            else:
                st.caption("目前會以你上方正在編修的草稿內容建立送審流程。")
            created_by = st.text_input(
                "送審人",
                value="專案負責人",
                key=f"approval-created-by-{active_conv_id}",
            )
            legal_focus = st.text_area(
                "法律審閱重點",
                value="請優先審查責任限制、違約責任、終止條款、準據法與管轄約定。",
                height=90,
                key=f"approval-focus-{active_conv_id}",
            )
            st.caption("目前預設只有一個審批步驟：法務審閱 / 法律專家。")
            if st.button("建立送審流程", use_container_width=True, key=f"approval-create-{active_conv_id}"):
                created_workflow = create_approval_request(
                    contract_title=contract_title.strip() or "合約審批案件",
                    draft_text=approval_base_text,
                    created_by=created_by.strip() or "專案負責人",
                    legal_focus=legal_focus.strip(),
                    reviewer_specs=build_default_reviewer_specs(),
                )
                created_workflow["source_label"] = contract_context["source_label"]
                created_workflow["source_hint"] = contract_context["source_hint"]
                created_workflow["approval_input_mode"] = approval_input_mode
                st.session_state[key] = created_workflow
                st.rerun()
            return

        contract_context = _derive_contract_context(active_conv_id)
        workflow.setdefault("source_label", contract_context["source_label"])
        workflow.setdefault("source_hint", contract_context["source_hint"])
        workflow.setdefault("approval_input_mode", approval_input_mode)
        ai_review = workflow.get("ai_review")
        if not ai_review or ai_review.get("draft_snapshot") != approval_base_text:
            workflow["ai_review"] = _build_ai_approval_review(
                chat_client=chat_client,
                llm_model=llm_model,
                active_conv_id=active_conv_id,
                draft_text=approval_base_text,
                legal_focus=workflow.get("legal_focus", ""),
                source_label=workflow.get("source_label", ""),
            ) | {"draft_snapshot": approval_base_text}
        st.session_state[key] = workflow

        st.markdown(f"**案件名稱**：{workflow['title']}")
        c1, c2 = st.columns([1, 3])
        c1.metric("目前狀態", _approval_status_label(workflow.get("status", "draft")))
        current_step = get_current_step(workflow)
        c2.markdown(f"**對應合約**：{workflow.get('source_label', '目前草稿')}")
        c2.caption(workflow.get("source_hint", ""))
        if workflow.get("approval_input_mode") == "uploaded_file":
            st.caption("這份審批目前是直接根據你上傳並灌入的合約內容建立。")
        else:
            st.caption("這份審批目前是根據你上方編修後的草稿建立。")
        st.caption(f"建立時間：{workflow.get('created_at', '')} | 最後更新：{workflow.get('updated_at', '')}")
        _render_approval_next_action(workflow, current_step)

        with st.expander("法律專家審閱重點", expanded=True):
            st.caption("先看這裡。這一區會告訴你法務最需要檢查的風險點。")
            left_col, right_col = st.columns(2)
            with left_col:
                st.markdown("**系統預設審閱重點**")
                st.caption("這是建立送審流程時預先帶入的法務檢查清單。")
                st.info(workflow.get("legal_focus", "一般商務合約審閱"))
            with right_col:
                st.markdown("**AI 檢閱後分析（限本對話文件）**")
                st.caption("這一欄是先檢索你上傳的合約內容，再由 AI 在 RAG 範圍內產出的審閱分析。")
                ai_review = workflow.get("ai_review") or {}
                ai_status = ai_review.get("status")
                ai_summary = ai_review.get("summary", "")
                if ai_status == "ok":
                    st.warning(ai_summary)
                    ai_sources = ai_review.get("sources") or []
                    if ai_sources:
                        st.caption("AI 分析依據：")
                        for tag in ai_sources[:5]:
                            st.markdown(f"- `{tag}`")
                elif ai_status == "unavailable":
                    st.info(ai_summary)
                else:
                    st.error(ai_summary or "AI 審閱分析目前不可用。")

        with st.expander("審批流程時間線", expanded=False):
            for event in reversed(workflow.get("timeline", [])):
                st.markdown(f"**{event.get('label', '')}**")
                st.caption(f"{event.get('at', '')} | {event.get('detail', '')}")

        st.markdown("**目前流程**")
        for step in workflow.get("steps", []):
            status = step.get("status", "pending")
            label = f"{step.get('step_order', '')}. {step.get('reviewer_name', '')} / {step.get('reviewer_role', '')}"
            with st.expander(f"{label} [{_step_status_label(status)}]", expanded=status == "reviewing"):
                st.caption("這是你目前要完成的審閱步驟。")
                if step.get("comment"):
                    st.markdown(f"**上一筆審閱意見**：{step.get('comment')}")
                if step.get("acted_at"):
                    st.caption(f"處理時間：{step.get('acted_at')}")
                if status == "reviewing":
                    st.caption("操作方式：先輸入審閱意見，再選下面其中一個按鈕。")
                    action_comment = st.text_area(
                        "審閱意見",
                        height=100,
                        placeholder="請具體說明條款風險、建議修改方向或核准理由。",
                        key=f"approval-comment-{active_conv_id}-{step['step_id']}",
                    )
                    action_cols = st.columns(3)
                    with action_cols[0]:
                        if st.button("核准", use_container_width=True, key=f"approval-approve-{step['step_id']}"):
                            st.session_state[key] = apply_step_action(
                                workflow,
                                step_id=step["step_id"],
                                action="approve",
                                comment=action_comment,
                            )
                            st.rerun()
                    with action_cols[1]:
                        if st.button("退回修改", use_container_width=True, key=f"approval-change-{step['step_id']}"):
                            st.session_state[key] = apply_step_action(
                                workflow,
                                step_id=step["step_id"],
                                action="request_changes",
                                comment=action_comment,
                            )
                            st.rerun()
                    with action_cols[2]:
                        if st.button("拒絕", use_container_width=True, key=f"approval-reject-{step['step_id']}"):
                            st.session_state[key] = apply_step_action(
                                workflow,
                                step_id=step["step_id"],
                                action="reject",
                                comment=action_comment,
                            )
                            st.rerun()

        if workflow.get("status") == "changes_requested":
            resend_note = st.text_input(
                "重新送審說明",
                value="已依審閱意見修正版本。",
                key=f"approval-resend-note-{active_conv_id}",
            )
            if st.button("修訂後重新送審", use_container_width=True, key=f"approval-resend-{active_conv_id}"):
                st.session_state[key] = restart_after_changes(workflow, note=resend_note)
                st.rerun()

        if workflow.get("status") == "approved":
            with st.expander("電子簽名整合", expanded=True):
                st.caption("只有在審閱通過後才需要處理這一區。")
                provider = st.text_input(
                    "簽署平台",
                    value=workflow.get("signature_provider", "DocuSign"),
                    key=f"approval-sign-provider-{active_conv_id}",
                )
                request_id = st.text_input(
                    "送簽編號",
                    value=workflow.get("signature_request_id", ""),
                    key=f"approval-sign-request-id-{active_conv_id}",
                )
                signed_file_url = st.text_input(
                    "簽署完成檔案連結",
                    value=workflow.get("signed_file_url", ""),
                    key=f"approval-signed-file-url-{active_conv_id}",
                )
                sign_cols = st.columns(2)
                with sign_cols[0]:
                    if st.button("送交簽署", use_container_width=True, key=f"approval-send-signature-{active_conv_id}"):
                        st.session_state[key] = mark_sent_for_signature(workflow, provider=provider, request_id=request_id)
                        st.rerun()
                with sign_cols[1]:
                    if st.button("標記已簽署", use_container_width=True, key=f"approval-mark-signed-{active_conv_id}"):
                        st.session_state[key] = mark_signed(workflow, signed_file_url=signed_file_url)
                        st.rerun()

        if workflow.get("status") in ("sent_for_signature", "signed"):
            st.info(
                f"電子簽署狀態：{workflow.get('signature_status', 'not_sent')} | "
                f"平台：{workflow.get('signature_provider', '—')} | "
                f"送簽編號：{workflow.get('signature_request_id', '—')}"
            )
            if workflow.get("signed_at"):
                st.caption(f"簽署完成時間：{workflow.get('signed_at')}")
            if workflow.get("signed_file_url"):
                st.markdown(f"簽署檔案：{workflow.get('signed_file_url')}")

        with st.expander("到期提醒與義務追蹤", expanded=False):
            st.caption("這一區是簽署後或履約階段再回來查看即可，現在可以先略過。")
            obligations = workflow.get("obligations", [])
            if not obligations:
                st.caption("目前沒有義務項目。")
            else:
                today_iso = date.today().isoformat()
                for item in obligations:
                    due = item.get("due_date", "")
                    status = item.get("status", "")
                    title = item.get("title", "")
                    if due == today_iso and status != "completed":
                        st.warning(f"{title}：今天到期")
                    elif status == "overdue":
                        st.error(f"{title}：已逾期")
                    else:
                        st.markdown(f"**{title}**")
                    st.caption(f"{item.get('owner', '')} | 到期：{due} | 狀態：{status}")
                    st.caption(f"來源：{item.get('source_clause_id', '')}")
                    st.markdown(item.get("description", ""))


def _render_knowledge_base_management() -> None:
    base_dir = Path(__file__).resolve().parent / "data" / "knowledge_base"
    laws_seed = base_dir / "laws_seed.json"
    cases_seed = base_dir / "cases_seed.json"
    laws_stats = dataset_stats("laws")
    cases_stats = dataset_stats("cases")
    recent_jobs = list(reversed(load_jobs(limit=8)))

    with st.expander("知識庫管理", expanded=False):
        st.caption("管理法條與判例資料的同步，以及將資料寫入目前使用中的 RAG 索引。")
        c1, c2 = st.columns(2)
        with c1:
            st.markdown("**法條資料**")
            st.metric("已保存筆數", laws_stats["record_count"])
            if laws_stats.get("updated_at"):
                st.caption(f"最近更新：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(laws_stats['updated_at']))}")
            if laws_stats["source_groups"]:
                st.caption("已收錄法規：" + "、".join(laws_stats["source_groups"][:3]))
            if laws_stats.get("dataset_path"):
                st.caption(f"資料檔：`{laws_stats['dataset_path']}`")
            if st.button("同步本地法條種子資料", use_container_width=True, key="kb-sync-laws-seed"):
                result = sync_records_from_file(
                    dataset="laws",
                    source_name="local_laws_seed",
                    file_path=laws_seed,
                )
                st.session_state["kb_last_result"] = f"法條同步完成：新增 {result['records_inserted']}，更新 {result['records_updated']}。"
                st.rerun()
            laws_upload = st.file_uploader(
                "匯入法條 JSON",
                type=["json"],
                key="kb-laws-json-upload",
                help="JSON 內容需為陣列，每筆至少包含 law_name、article_no、article_text。",
            )
            if st.button("同步上傳的法條 JSON", use_container_width=True, key="kb-sync-laws-upload", disabled=not laws_upload):
                result = sync_records_from_json_text(
                    dataset="laws",
                    source_name=getattr(laws_upload, "name", "uploaded_laws_json"),
                    json_text=laws_upload.getvalue().decode("utf-8"),
                )
                st.session_state["kb_last_result"] = f"法條 JSON 同步完成：新增 {result['records_inserted']}，更新 {result['records_updated']}。"
                st.rerun()
            if st.button("將法條資料寫入索引", use_container_width=True, key="kb-ingest-laws"):
                result = ingest_dataset("laws")
                st.session_state["kb_last_result"] = f"法條索引更新完成：{result['records']} 筆資料，{result['chunk_count']} 個 chunks。"
                st.rerun()
        with c2:
            st.markdown("**判例資料**")
            st.metric("已保存筆數", cases_stats["record_count"])
            if cases_stats.get("updated_at"):
                st.caption(f"最近更新：{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(cases_stats['updated_at']))}")
            if cases_stats["source_groups"]:
                st.caption("已收錄法院：" + "、".join(cases_stats["source_groups"][:3]))
            if cases_stats.get("dataset_path"):
                st.caption(f"資料檔：`{cases_stats['dataset_path']}`")
            if st.button("同步本地判例種子資料", use_container_width=True, key="kb-sync-cases-seed"):
                result = sync_records_from_file(
                    dataset="cases",
                    source_name="local_cases_seed",
                    file_path=cases_seed,
                )
                st.session_state["kb_last_result"] = f"判例同步完成：新增 {result['records_inserted']}，更新 {result['records_updated']}。"
                st.rerun()
            cases_upload = st.file_uploader(
                "匯入判例 JSON",
                type=["json"],
                key="kb-cases-json-upload",
                help="JSON 內容需為陣列，每筆至少包含 case_number、court_name、full_text。",
            )
            if st.button("同步上傳的判例 JSON", use_container_width=True, key="kb-sync-cases-upload", disabled=not cases_upload):
                result = sync_records_from_json_text(
                    dataset="cases",
                    source_name=getattr(cases_upload, "name", "uploaded_cases_json"),
                    json_text=cases_upload.getvalue().decode("utf-8"),
                )
                st.session_state["kb_last_result"] = f"判例 JSON 同步完成：新增 {result['records_inserted']}，更新 {result['records_updated']}。"
                st.rerun()
            if st.button("將判例資料寫入索引", use_container_width=True, key="kb-ingest-cases"):
                result = ingest_dataset("cases")
                st.session_state["kb_last_result"] = f"判例索引更新完成：{result['records']} 筆資料，{result['chunk_count']} 個 chunks。"
                st.rerun()

        last_result = st.session_state.get("kb_last_result")
        if last_result:
            st.success(last_result)

        with st.expander("最近同步紀錄", expanded=False):
            if not recent_jobs:
                st.caption("目前還沒有同步紀錄。")
            else:
                for job in recent_jobs:
                    st.markdown(f"**{job.get('job_type', '')} / {job.get('source_name', '')}**")
                    st.caption(
                        f"狀態：{job.get('status', '')} | "
                        f"抓取：{job.get('records_fetched', 0)} | "
                        f"新增：{job.get('records_inserted', 0)} | "
                        f"更新：{job.get('records_updated', 0)} | "
                        f"刪除：{job.get('records_deleted', 0)}"
                    )
                    if job.get("error_message"):
                        st.caption(f"錯誤：{job['error_message']}")


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
    rag_scope_chat_id: str | None = None,
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
        rag_scope_chat_id=rag_scope_chat_id,
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
    rag_scope_chat_id: str | None = None,
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
        rag_scope_chat_id=rag_scope_chat_id,
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
    st.caption("每次在對話中問答後，若已啟用記錄，會在此列出問題、回答、使用的 Tool 與延遲，方便事後檢視與除錯。")
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
    st.caption("以固定題集（如 eval_set.json / eval_set_contract.json）執行 `uv run python eval/run_eval.py` 後，可在此選擇某次 Run 檢視 **Routing 準確率**、**Tool 成功率** 與 **延遲**，作為技術驗證與作品完整性佐證。")
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
        with st.expander("📌 指標說明"):
            st.markdown("""
            - **Routing 準確率**：意圖是否被正確路由到預期 Tool（有標註 expected_tool 的題目才計入）。
            - **Tool 成功率**：整次 Run 中無 exception、成功回覆的題目比例。
            - **Latency P95**：單次問答延遲的 95 分位（秒），可代表「多數請求」的響應時間；與 AI 輕量化、作品完整性驗證相關。
            """)

    st.divider()
    st.caption("各題結果（可展開看問題與回答；✓/✗ 表示該題是否成功，括號內為該題延遲）")
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
                st.caption(f"錯誤：{str(r.get('error'))[:500]}")


def ingest_uploaded_files(
    *,
    chat_client: Any,
    embed_client: genai.Client,
    index: Any,
    index_dim: int,
    embed_model: str,
    uploaded_files: list[Any],
    chat_id: str | None = None,
) -> tuple[int, list[dict[str, Any]]]:
    # 支援 .txt / .md / .pdf / .docx
    all_sources: list[str] = []
    all_texts: list[str] = []
    all_chunk_indexes: list[int] = []
    all_ids: list[str] = []
    parse_results: list[dict[str, Any]] = []

    for uf in uploaded_files:
        name = getattr(uf, "name", "uploaded")
        lower_name = name.lower()
        if not (lower_name.endswith(".txt") or lower_name.endswith(".md") or lower_name.endswith(".pdf") or lower_name.endswith(".docx")):
            continue

        source = f"uploaded/{chat_id}/{name}" if chat_id else f"uploaded/{name}"
        parsed = parse_uploaded_document(
            uploaded_file=uf,
            source=source,
            chat_client=chat_client,
            ocr_model=os.getenv("GEMINI_CHAT_MODEL", "gemini-3.1-flash-lite-preview"),
            enable_ocr=True,
        )
        if not parsed or not parsed.text.strip():
            parse_results.append(
                {
                    "name": name,
                    "status": "skipped",
                    "parser": "unsupported",
                    "used_ocr": False,
                    "chunk_count": 0,
                    "warnings": ["無法從檔案中擷取文字。"],
                    "page_count": None,
                }
            )
            continue
        parts = chunk_text(parsed.text)
        parse_results.append(
            {
                "name": parsed.name,
                "status": "ready",
                "parser": parsed.parser,
                "used_ocr": parsed.used_ocr,
                "chunk_count": len(parts),
                "warnings": list(parsed.warnings),
                "page_count": parsed.page_count,
            }
        )

        for i, part in enumerate(parts):
            all_sources.append(parsed.source)
            all_texts.append(part)
            all_chunk_indexes.append(i)
            all_ids.append(stable_id(parsed.source, i, part))

    if not all_texts:
        return 0, parse_results

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
    # BM25 語料：上傳灌入時追加（含 chat_id）
    append_bm25_corpus([
        {
            "id": all_ids[j],
            "text": all_texts[j],
            "source": all_sources[j],
            "chunk_index": all_chunk_indexes[j],
            "chat_id": chat_id,
        }
        for j in range(len(all_texts))
    ])
    return len(all_texts), parse_results


def main() -> None:
    st.set_page_config(
        page_title="智慧問答合約／採購法遵審閱助理",
        page_icon="💬",
        layout="centered",
        initial_sidebar_state="expanded",
    )
    _inject_custom_css()

    try:
        chat_client, embed_client, index, index_dim, _cached_llm, embed_model, index_name = _cached_get_clients_and_index()
        # 強制載入專案根目錄 .env，確保側欄與請求使用正確的 GEMINI_CHAT_MODEL
        load_dotenv(Path(__file__).resolve().parent / ".env")
        _, llm_model = get_chat_client_and_model()
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
        strict_mode = st.checkbox("嚴格只根據知識庫回答", value=False, help="勾選時一律只依知識庫回答、不經合約／法條工具。合約審閱建議不勾選以啟用合約專家與法條查詢。")
        # 若此對話有上傳過檔案，預設勾選「只搜尋此對話上傳的檔案」，避免參考連結／檢索片段參雜其他來源
        has_uploads_here = len(list_sources(chat_id=active_conv_id)) > 0
        filter_by_chat = st.checkbox(
            "只搜尋此對話上傳的檔案",
            value=has_uploads_here,
            help="勾選時，參考連結與檢索片段僅來自本對話上傳的檔案；不勾選則搜尋整個知識庫。",
        )
        rag_scope_chat_id = active_conv_id if filter_by_chat else None
        with st.expander("合約審閱提示", expanded=True):
            st.caption("上傳合約後可問：「請審閱這份合約的風險條款」「合約風險評估並查相關法條」，或使用下方一鍵審閱。")
            if st.button("一鍵審閱（僅知識庫）", use_container_width=True, key="one_click_knowledge"):
                st.session_state["one_click_review_question"] = "請根據目前已灌入的文件做合約條款分析與風險標註，僅依文件內容、不查外部法條。"
                st.session_state["one_click_review_chat_id"] = active_conv_id
                st.rerun()
            if st.button("一鍵審閱（含法條查詢）", use_container_width=True, key="one_click_law"):
                st.session_state["one_click_review_question"] = "請審閱這份合約的風險條款並查相關法條。"
                st.session_state["one_click_review_chat_id"] = active_conv_id
                st.rerun()

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

        st.divider()
        if st.button("清空資料庫", type="secondary", use_container_width=True, key="btn_clear_db"):
            try:
                index.delete(delete_all=True)
                save_registry([])
                st.success("已清空向量庫與來源註冊表。")
            except Exception as e:
                st.error(f"清空失敗：{e}")
            st.rerun()

    # 主標題；Eval 頁改為情境化小標，對話頁保留完整副標
    st.title("合約／法遵審閱助理")
    if view == "對話":
        st.markdown(
            '<p class="app-tagline">RAG · 合約風險 · 法條查詢 · 知識庫問答 · 多輪對話</p>',
            unsafe_allow_html=True,
        )
    elif view == "Eval 運行記錄":
        st.caption("檢視執行記錄")
    elif view == "Eval 批次結果":
        st.caption("檢視批次結果")

    if view == "Eval 運行記錄":
        _render_eval_view()
        return
    if view == "Eval 批次結果":
        _render_eval_batch_view()
        return

    if "messages" not in current_conv:
        current_conv["messages"] = []

    # 空對話時顯示引導文案（強調合約審閱流程）
    if not current_conv["messages"]:
        st.info(
            "**合約審閱**：先展開「為此對話上傳並灌入文件」上傳合約 .pdf / .docx / .txt / .md，按「灌入到向量庫」後，"
            "在側欄點「一鍵審閱」或輸入「請審閱這份合約的風險條款」即可。"
        )
        st.markdown("")  # 小留白

    # 整理給模型用的對話歷史（只保留 role + content），傳入 RAG/專家以記得上下文
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
            _is_contract_tool = msg.get("tool_name") in ("contract_risk_agent", "contract_risk_with_law_search")
            if _is_contract_tool and msg.get("chunks"):
                st.caption("以下為合約風險分析，可展開檢索片段對照原文。")
            if msg.get("chunks"):
                with st.expander("查看檢索片段", expanded=_is_contract_tool):
                    for c in msg["chunks"]:
                        st.markdown(f"**{c['tag']}**\n\n{c['text']}")

    with st.expander("為此對話上傳並灌入文件"):
        st.caption(
            "支援 `.txt` / `.md` / `.pdf` / `.docx`。系統會顯示解析方式、是否使用 OCR，"
            "也能在灌入前先比對兩個版本。"
        )
        uploads = st.file_uploader(
            "選擇檔案",
            type=["txt", "md", "pdf", "docx"],
            accept_multiple_files=True,
            key=f"uploads-{active_conv_id}",
        )
        if uploads and len(uploads) >= 2:
            compare_left = st.selectbox(
                "Diff 左側版本",
                options=list(range(len(uploads))),
                format_func=lambda idx: uploads[idx].name,
                key=f"diff-left-{active_conv_id}",
            )
            compare_right = st.selectbox(
                "Diff 右側版本",
                options=list(range(len(uploads))),
                index=min(1, len(uploads) - 1),
                format_func=lambda idx: uploads[idx].name,
                key=f"diff-right-{active_conv_id}",
            )
            if compare_left != compare_right:
                left_file = uploads[compare_left]
                right_file = uploads[compare_right]
                left_doc = parse_uploaded_document(
                    uploaded_file=left_file,
                    source=f"uploaded/{active_conv_id}/{left_file.name}",
                    chat_client=chat_client,
                    ocr_model=llm_model,
                    enable_ocr=True,
                )
                right_doc = parse_uploaded_document(
                    uploaded_file=right_file,
                    source=f"uploaded/{active_conv_id}/{right_file.name}",
                    chat_client=chat_client,
                    ocr_model=llm_model,
                    enable_ocr=True,
                )
                if left_doc and right_doc:
                    diff_summary = build_contract_diff(
                        left_name=left_doc.name,
                        left_text=left_doc.text,
                        right_name=right_doc.name,
                        right_text=right_doc.text,
                    )
                    col1, col2, col3 = st.columns(3)
                    col1.metric("變更行數", diff_summary.changed_lines)
                    col2.metric("新增", diff_summary.added_lines)
                    col3.metric("刪除", diff_summary.removed_lines)
                    with st.expander("版本差異預覽", expanded=False):
                        st.components.v1.html(diff_summary.html, height=420, scrolling=True)
                else:
                    st.info("至少有一份檔案無法解析，暫時無法做版本比對。")
        if st.button(
            "灌入到向量庫",
            use_container_width=True,
            disabled=not uploads,
            key=f"ingest-{active_conv_id}",
        ):
            try:
                with st.spinner("向量化並寫入 Pinecone 中…（檔案越大越久）"):
                    n, parse_results = ingest_uploaded_files(
                        chat_client=chat_client,
                        embed_client=embed_client,
                        index=index,
                        index_dim=index_dim,
                        embed_model=embed_model,
                        uploaded_files=list(uploads or []),
                        chat_id=active_conv_id,
                    )
                if parse_results:
                    st.caption("解析摘要")
                    for item in parse_results:
                        page_info = f" | pages={item['page_count']}" if item.get("page_count") else ""
                        ocr_info = " | OCR" if item.get("used_ocr") else ""
                        st.markdown(
                            f"- `{item['name']}`: {item['status']} | {item['parser']} | chunks={item['chunk_count']}{page_info}{ocr_info}"
                        )
                        for warning in item.get("warnings") or []:
                            st.caption(f"{item['name']}: {warning}")
                if n == 0:
                    st.warning("沒有可灌入的內容（請確認檔案不是空的）。")
                else:
                    st.success(f"已灌入 {n} 個 chunks，可直接在下方問答。")
            except Exception as e:
                st.error(f"灌入失敗：{e}")

    with st.expander("合約生成與編修", expanded=False):
        template_options = list_templates()
        template_map = {template.name: template for template in template_options}
        selected_template_name = st.selectbox(
            "選擇模板",
            options=list(template_map.keys()),
            key=f"draft-template-{active_conv_id}",
        )
        selected_template = template_map[selected_template_name]
        st.caption(selected_template.description)

        values: dict[str, str] = {}
        for field in selected_template.fields:
            values[field.key] = st.text_input(
                field.label,
                value=field.default,
                key=f"draft-{active_conv_id}-{selected_template.template_id}-{field.key}",
            )

        if st.button("生成合約草稿", use_container_width=True, key=f"draft-generate-{active_conv_id}"):
            draft = render_template(selected_template.template_id, values)
            st.session_state[f"draft-original-{active_conv_id}"] = draft
            st.session_state[f"draft-current-{active_conv_id}"] = draft
            st.session_state[f"draft-template-name-{active_conv_id}"] = selected_template.name

        draft_original = st.session_state.get(f"draft-original-{active_conv_id}", "")
        draft_current = st.session_state.get(f"draft-current-{active_conv_id}", draft_original)
        if draft_original:
            edited_draft = st.text_area(
                "草稿內容",
                value=draft_current,
                height=360,
                key=f"draft-editor-{active_conv_id}",
            )
            clause_updates = st.text_area(
                "條款修訂需求",
                value="",
                height=120,
                placeholder="例如：新增台灣法律準據法、補上違約金條款、增加終止條款。",
                key=f"draft-update-{active_conv_id}",
            )
            col_apply, col_reset = st.columns(2)
            with col_apply:
                if st.button("套用修訂內容", use_container_width=True, key=f"draft-apply-{active_conv_id}"):
                    revised = apply_clause_updates(edited_draft, clause_updates)
                    st.session_state[f"draft-current-{active_conv_id}"] = revised
                    edited_draft = revised
            with col_reset:
                if st.button("重設為原始草稿", use_container_width=True, key=f"draft-reset-{active_conv_id}"):
                    st.session_state[f"draft-current-{active_conv_id}"] = draft_original
                    edited_draft = draft_original

            redline = summarize_redline(
                draft_original,
                edited_draft,
                original_name="原始草稿",
                revised_name="修訂草稿",
            )
            metric_col1, metric_col2, metric_col3 = st.columns(3)
            metric_col1.metric("變更行數", redline.changed_lines)
            metric_col2.metric("新增", redline.added_lines)
            metric_col3.metric("刪除", redline.removed_lines)
            with st.expander("Redline 預覽", expanded=False):
                if not redline.blocks:
                    st.info("目前原始草稿與修訂草稿沒有差異。")
                else:
                    for idx, block in enumerate(redline.blocks, start=1):
                        st.markdown(f"**{idx}. {block.title}**")
                        if block.before_text:
                            st.caption("原條文")
                            st.code(block.before_text, language="markdown")
                        if block.after_text:
                            st.caption("修訂條文")
                            st.code(block.after_text, language="markdown")
                        st.divider()
            st.download_button(
                "下載目前草稿",
                data=edited_draft.encode("utf-8"),
                file_name=f"{selected_template.template_id}_draft.md",
                mime="text/markdown",
                use_container_width=True,
                key=f"draft-download-{active_conv_id}",
            )

    draft_for_approval = st.session_state.get(f"draft-current-{active_conv_id}", "")
    _render_approval_workflow(
        active_conv_id,
        draft_for_approval,
        chat_client=chat_client,
        llm_model=llm_model,
    )

    question = st.chat_input("輸入你的問題…")
    # 一鍵審閱：側欄按鈕觸發後，以預設問題當作本輪使用者輸入
    if question is None and st.session_state.get("one_click_review_chat_id") == active_conv_id and st.session_state.get("one_click_review_question"):
        question = st.session_state.pop("one_click_review_question", "")
        st.session_state.pop("one_click_review_chat_id", None)
    if not question:
        return

    current_conv["messages"].append({"role": "user", "content": question})
    # 第一則使用者問題時，將對話標題設為問題前 20 字
    if current_conv.get("title") == "新對話" and len(current_conv["messages"]) == 1:
        q = (question or "").strip()
        current_conv["title"] = (q[:20] + ("…" if len(q) > 20 else "")) or "新對話"
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
                    rag_scope_chat_id=rag_scope_chat_id,
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
            "tool_name": tool_name,
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
                    rag_scope_chat_id=rag_scope_chat_id,
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
            "tool_name": tool_name,
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
                rag_scope_chat_id=rag_scope_chat_id,
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
        _contract_tool = tool_name in ("contract_risk_agent", "contract_risk_with_law_search")
        if _contract_tool and chunks:
            st.caption("以下為合約風險分析，可展開檢索片段對照原文。")
        if chunks:
            with st.expander("查看檢索片段", expanded=_contract_tool):
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
        "tool_name": tool_name,
        "chart_option": (extra or {}).get("chart_option"),
        "chart_image_base64": (extra or {}).get("chart_image_base64"),
        "chart_chunks": (extra or {}).get("chart_chunks"),
        "chart_sources": (extra or {}).get("chart_sources"),
    })


if __name__ == "__main__":
    main()

