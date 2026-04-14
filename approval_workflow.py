from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import uuid


@dataclass
class ApprovalStep:
    step_id: str
    approval_id: str
    step_order: int
    reviewer_id: str
    reviewer_name: str
    reviewer_role: str
    status: str = "pending"
    comment: str = ""
    acted_at: str | None = None


@dataclass
class ContractObligation:
    obligation_id: str
    contract_id: str
    version_id: str
    title: str
    description: str
    owner: str
    due_date: str
    status: str
    source_clause_id: str
    notes: str = ""


@dataclass
class ApprovalRequest:
    approval_id: str
    contract_id: str
    version_id: str
    title: str
    status: str
    created_by: str
    created_at: str
    updated_at: str
    draft_text: str
    legal_focus: str
    legal_risk_summary: list[str] = field(default_factory=list)
    signoff_notes: str = ""
    signature_provider: str = ""
    signature_request_id: str = ""
    signature_status: str = "not_sent"
    signed_at: str | None = None
    signed_file_url: str = ""
    steps: list[ApprovalStep] = field(default_factory=list)
    obligations: list[ContractObligation] = field(default_factory=list)
    timeline: list[dict[str, str]] = field(default_factory=list)


def _new_id(prefix: str) -> str:
    return f"{prefix}-{uuid.uuid4().hex[:8]}"


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _today_iso(offset_days: int = 0) -> str:
    return (date.today() + timedelta(days=offset_days)).isoformat()


def _event(label: str, detail: str) -> dict[str, str]:
    return {"at": _now_iso(), "label": label, "detail": detail}


def _normalize_step_statuses(steps: list[ApprovalStep]) -> list[ApprovalStep]:
    active_assigned = False
    normalized: list[ApprovalStep] = []
    for step in sorted(steps, key=lambda s: s.step_order):
        if step.status in ("approved", "changes_requested", "rejected"):
            normalized.append(step)
            continue
        if not active_assigned:
            step.status = "reviewing"
            active_assigned = True
        else:
            step.status = "pending"
        normalized.append(step)
    return normalized


def build_default_reviewer_specs() -> list[dict[str, str]]:
    return [
        {
            "reviewer_id": "solo-legal-1",
            "reviewer_name": "法務審閱",
            "reviewer_role": "法律專家",
        }
    ]


def build_legal_risk_summary(draft_text: str, legal_focus: str) -> list[str]:
    text = draft_text or ""
    findings: list[str] = []
    focus = legal_focus or "一般商務合約審閱"
    findings.append(f"法律專家檢核焦點：{focus}")
    if "準據法" not in text and "governing law" not in text.lower():
        findings.append("未明確看到準據法條款，簽署前應補充準據法與管轄法院。")
    if "終止" not in text and "解除" not in text:
        findings.append("未明確看到終止或解除條款，履約爭議時可能欠缺退出機制。")
    if "保密" not in text and "confidential" not in text.lower():
        findings.append("未明確看到保密義務條款，若涉及商業資訊交換建議補上。")
    if "違約" not in text and "賠償" not in text:
        findings.append("未明確看到違約責任或損害賠償條款，風險分配可能不足。")
    return findings


def suggest_obligations(*, contract_id: str, version_id: str, draft_text: str) -> list[ContractObligation]:
    obligations: list[ContractObligation] = [
        ContractObligation(
            obligation_id=_new_id("obl"),
            contract_id=contract_id,
            version_id=version_id,
            title="完成法律審閱意見",
            description="確認準據法、責任限制、違約責任、終止與保密條款是否完整。",
            owner="目前使用者",
            due_date=_today_iso(3),
            status="upcoming",
            source_clause_id="approval/legal-review",
        ),
        ContractObligation(
            obligation_id=_new_id("obl"),
            contract_id=contract_id,
            version_id=version_id,
            title="確認送簽前版本",
            description="送簽前確認目前版本已反映審閱意見，避免錯版送簽。",
            owner="目前使用者",
            due_date=_today_iso(5),
            status="upcoming",
            source_clause_id="approval/pre-signoff",
        ),
    ]
    if "付款" in draft_text or "fee" in draft_text.lower():
        obligations.append(
            ContractObligation(
                obligation_id=_new_id("obl"),
                contract_id=contract_id,
                version_id=version_id,
                title="確認付款與金額條款",
                description="檢查付款期限、金額、違約金與發票時點是否一致。",
                owner="目前使用者",
                due_date=_today_iso(7),
                status="upcoming",
                source_clause_id="draft/payment",
            )
        )
    return obligations


def create_approval_request(
    *,
    contract_title: str,
    draft_text: str,
    created_by: str,
    legal_focus: str,
    reviewer_specs: list[dict[str, str]],
) -> dict:
    approval_id = _new_id("approval")
    contract_id = _new_id("contract")
    version_id = _new_id("v")
    created_at = _now_iso()
    steps = [
        ApprovalStep(
            step_id=_new_id("step"),
            approval_id=approval_id,
            step_order=i + 1,
            reviewer_id=spec["reviewer_id"],
            reviewer_name=spec["reviewer_name"],
            reviewer_role=spec["reviewer_role"],
        )
        for i, spec in enumerate(reviewer_specs)
    ]
    steps = _normalize_step_statuses(steps)
    workflow = ApprovalRequest(
        approval_id=approval_id,
        contract_id=contract_id,
        version_id=version_id,
        title=contract_title,
        status="in_review" if steps else "draft",
        created_by=created_by,
        created_at=created_at,
        updated_at=created_at,
        draft_text=draft_text,
        legal_focus=legal_focus,
        legal_risk_summary=build_legal_risk_summary(draft_text, legal_focus),
        steps=steps,
        obligations=suggest_obligations(contract_id=contract_id, version_id=version_id, draft_text=draft_text),
        timeline=[
            _event("送審建立", f"建立審批流程：{contract_title}"),
            _event("法律審閱重點", legal_focus or "一般商務合約審閱"),
        ],
    )
    return approval_to_dict(workflow)


def approval_from_dict(data: dict) -> ApprovalRequest:
    steps = [ApprovalStep(**step) for step in data.get("steps", [])]
    obligations = [ContractObligation(**item) for item in data.get("obligations", [])]
    return ApprovalRequest(
        approval_id=data["approval_id"],
        contract_id=data["contract_id"],
        version_id=data["version_id"],
        title=data["title"],
        status=data["status"],
        created_by=data["created_by"],
        created_at=data["created_at"],
        updated_at=data["updated_at"],
        draft_text=data.get("draft_text", ""),
        legal_focus=data.get("legal_focus", ""),
        legal_risk_summary=list(data.get("legal_risk_summary", [])),
        signoff_notes=data.get("signoff_notes", ""),
        signature_provider=data.get("signature_provider", ""),
        signature_request_id=data.get("signature_request_id", ""),
        signature_status=data.get("signature_status", "not_sent"),
        signed_at=data.get("signed_at"),
        signed_file_url=data.get("signed_file_url", ""),
        steps=steps,
        obligations=obligations,
        timeline=list(data.get("timeline", [])),
    )


def approval_to_dict(workflow: ApprovalRequest) -> dict:
    out = asdict(workflow)
    out["steps"] = [asdict(step) for step in workflow.steps]
    out["obligations"] = [asdict(item) for item in workflow.obligations]
    return out


def ensure_single_user_workflow(workflow_data: dict) -> dict:
    workflow = approval_from_dict(workflow_data)
    primary = workflow.steps[0] if workflow.steps else ApprovalStep(
        step_id=_new_id("step"),
        approval_id=workflow.approval_id,
        step_order=1,
        reviewer_id="solo-legal-1",
        reviewer_name="法務審閱",
        reviewer_role="法律專家",
    )
    primary.step_order = 1
    primary.reviewer_id = "solo-legal-1"
    primary.reviewer_name = "法務審閱"
    primary.reviewer_role = "法律專家"
    workflow.steps = _normalize_step_statuses([primary])
    for item in workflow.obligations:
        item.owner = "目前使用者"
    if workflow.status == "in_review" and workflow.steps and workflow.steps[0].status == "approved":
        workflow.status = "approved"
    workflow.updated_at = _now_iso()
    return approval_to_dict(workflow)


def get_current_step(workflow: dict) -> dict | None:
    for step in workflow.get("steps", []):
        if step.get("status") == "reviewing":
            return step
    return None


def apply_step_action(workflow_data: dict, *, step_id: str, action: str, comment: str) -> dict:
    workflow = approval_from_dict(workflow_data)
    updated_steps: list[ApprovalStep] = []
    acted_label = ""
    for step in workflow.steps:
        if step.step_id != step_id:
            updated_steps.append(step)
            continue
        step.comment = comment.strip()
        step.acted_at = _now_iso()
        if action == "approve":
            step.status = "approved"
            acted_label = f"{step.reviewer_name}核准"
        elif action == "request_changes":
            step.status = "changes_requested"
            workflow.status = "changes_requested"
            acted_label = f"{step.reviewer_name}退回修改"
        elif action == "reject":
            step.status = "rejected"
            workflow.status = "rejected"
            acted_label = f"{step.reviewer_name}拒絕"
        updated_steps.append(step)
    if action == "approve":
        updated_steps = _normalize_step_statuses(updated_steps)
        current = next((step for step in updated_steps if step.status == "reviewing"), None)
        workflow.status = "in_review" if current else "approved"
    workflow.steps = updated_steps
    workflow.updated_at = _now_iso()
    workflow.timeline.append(_event(acted_label or "審批更新", comment.strip() or "無補充意見"))
    return approval_to_dict(workflow)


def restart_after_changes(workflow_data: dict, *, note: str) -> dict:
    workflow = approval_from_dict(workflow_data)
    for step in workflow.steps:
        if step.status in ("changes_requested", "reviewing"):
            step.status = "pending"
            step.acted_at = None
    workflow.steps = _normalize_step_statuses(workflow.steps)
    workflow.status = "in_review"
    workflow.updated_at = _now_iso()
    workflow.timeline.append(_event("修訂後重新送審", note.strip() or "已依審閱意見更新版本"))
    return approval_to_dict(workflow)


def mark_sent_for_signature(workflow_data: dict, *, provider: str, request_id: str) -> dict:
    workflow = approval_from_dict(workflow_data)
    workflow.status = "sent_for_signature"
    workflow.signature_provider = provider.strip()
    workflow.signature_request_id = request_id.strip()
    workflow.signature_status = "sent"
    workflow.updated_at = _now_iso()
    workflow.timeline.append(_event("送交電子簽署", f"{provider.strip() or '未指定平台'} / {request_id.strip() or '未填送簽編號'}"))
    return approval_to_dict(workflow)


def mark_signed(workflow_data: dict, *, signed_file_url: str) -> dict:
    workflow = approval_from_dict(workflow_data)
    workflow.status = "signed"
    workflow.signature_status = "signed"
    workflow.signed_at = _now_iso()
    workflow.signed_file_url = signed_file_url.strip()
    workflow.updated_at = _now_iso()
    workflow.timeline.append(_event("簽署完成", signed_file_url.strip() or "已完成簽署"))
    return approval_to_dict(workflow)


def update_obligation_statuses(workflow_data: dict) -> dict:
    workflow = approval_from_dict(workflow_data)
    today = date.today()
    for item in workflow.obligations:
        if item.status == "completed":
            continue
        try:
            due = date.fromisoformat(item.due_date)
        except ValueError:
            continue
        if due < today:
            item.status = "overdue"
        elif due == today:
            item.status = "due_today"
        else:
            item.status = "upcoming"
    return approval_to_dict(workflow)
