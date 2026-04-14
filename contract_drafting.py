from __future__ import annotations

from dataclasses import dataclass
from difflib import HtmlDiff, SequenceMatcher
import re


@dataclass(frozen=True)
class TemplateField:
    key: str
    label: str
    default: str = ""
    required: bool = True


@dataclass(frozen=True)
class ContractTemplate:
    template_id: str
    name: str
    description: str
    fields: tuple[TemplateField, ...]
    body: str


@dataclass(frozen=True)
class RedlineBlock:
    change_type: str
    title: str
    before_text: str
    after_text: str


@dataclass(frozen=True)
class RedlineSummary:
    html: str
    changed_lines: int
    added_lines: int
    removed_lines: int
    blocks: tuple[RedlineBlock, ...] = ()


TEMPLATES: dict[str, ContractTemplate] = {
    "nda": ContractTemplate(
        template_id="nda",
        name="雙方保密協議（NDA）",
        description="正式版雙方保密協議模板，包含保密義務、例外、強制揭露、權利保留、違約責任與管轄條款。",
        fields=(
            TemplateField("party_a", "甲方名稱", "甲方股份有限公司"),
            TemplateField("party_b", "乙方名稱", "乙方股份有限公司"),
            TemplateField("effective_date", "簽署日期", "2026年4月12日"),
            TemplateField("purpose", "合作目的", "雙方就潛在合作、商業往來及技術評估進行討論"),
            TemplateField("term_years", "保密年限", "5"),
            TemplateField("governing_law", "準據法", "中華民國法律"),
            TemplateField("jurisdiction", "管轄法院", "臺灣臺北地方法院"),
        ),
        body="""# 雙方保密協議

本雙方保密協議（以下稱「本協議」）由下列當事人於 {effective_date} 簽署：

甲方：{party_a}
乙方：{party_b}

甲乙雙方為推動下列事項：{purpose}（以下稱「合作目的」），預期將相互揭露部分非公開資訊。為明確雙方權利義務，特訂立本協議，以資共同遵循。

## 第一條 定義
一、「揭露方」係指提供保密資訊之一方；「受領方」係指接受保密資訊之一方。
二、「保密資訊」係指揭露方以書面、口頭、電子、影像、樣品、模型、數據或其他任何形式向受領方揭露之資訊，且依其性質或揭露情境足認屬非公開、機密或具商業價值之資訊。

## 第二條 保密義務
一、受領方應就保密資訊盡善良管理人之注意義務，並採取不低於保護其自身同類重要資訊之保護措施。
二、除為合作目的所必要者外，受領方不得使用、複製、揭露、散布、摘要或以其他方式處分保密資訊。

## 第三條 保密資訊之例外
下列資訊不屬於保密資訊：
一、於揭露時已為公眾所知，或其後非因受領方違反本協議而成為公眾所知之資訊。
二、受領方於揭露前已合法持有，且得提出合理證明者。
三、受領方自第三人合法取得，且該第三人對揭露方不負保密義務者。

## 第四條 強制揭露
受領方如因法令、法院、主管機關或其他有權機關之要求，必須揭露保密資訊者，應於法律許可範圍內，儘速事先通知揭露方，並配合揭露方採取適當保護措施。

## 第五條 權利保留
本協議之簽署及保密資訊之揭露，不應解釋為揭露方授與受領方任何明示或默示之專利、著作權、商標、營業秘密或其他智慧財產權之授權。

## 第六條 返還與銷毀
揭露方得隨時以書面要求受領方返還、刪除或銷毀全部或一部保密資訊及其複本、摘要、備份與衍生資料。

## 第七條 保密期間
本協議自簽署日起生效。雙方保密義務自各次保密資訊揭露日起，持續有效 {term_years} 年。

## 第八條 違約責任
受領方如違反本協議，應賠償揭露方因此所受之一切損害，包括但不限於訴訟費用、律師費及合理支出。

## 第九條 準據法與管轄
本協議之解釋、效力、履行及相關爭議，均以 {governing_law} 為準據法。如因本協議所生爭議，雙方同意以 {jurisdiction} 為第一審管轄法院。
""",
    ),
    "service_agreement": ContractTemplate(
        template_id="service_agreement",
        name="服務契約",
        description="正式版服務契約模板，適用委外、顧問或專案服務，涵蓋驗收、責任限制、終止與智慧財產權條款。",
        fields=(
            TemplateField("client_name", "委託方名稱", "甲方股份有限公司"),
            TemplateField("vendor_name", "服務方名稱", "乙方股份有限公司"),
            TemplateField("effective_date", "簽署日期", "2026年4月12日"),
            TemplateField("service_scope", "服務內容", "依雙方確認之工作說明書、報價單或需求文件提供服務"),
            TemplateField("service_term", "服務期間", "自簽署日起一年"),
            TemplateField("fee_terms", "費用與付款條件", "服務總價為新台幣 1,200,000 元，按月開立發票並於發票日起三十日內付款"),
            TemplateField("acceptance_terms", "驗收標準", "以雙方書面確認之交付規格、功能需求或里程碑文件為準"),
            TemplateField("liability_cap", "責任上限", "乙方於本契約項下之累積損害賠償責任總額，以甲方於爭議發生日前十二個月內已實際支付之服務費總額為上限"),
            TemplateField("governing_law", "準據法", "中華民國法律"),
            TemplateField("jurisdiction", "管轄法院", "臺灣臺北地方法院"),
        ),
        body="""# 服務契約

本服務契約（以下稱「本契約」）由下列當事人於 {effective_date} 簽署：

委託方（甲方）：{client_name}
服務方（乙方）：{vendor_name}

## 第一條 服務內容
乙方應依本契約及雙方另行確認之相關文件，向甲方提供下列服務：
{service_scope}

## 第二條 契約期間
本契約之有效期間為 {service_term}。契約期間屆滿前，如雙方同意續約，應另以書面為之。

## 第三條 驗收
甲方應依下列驗收標準進行審核：
{acceptance_terms}

## 第四條 費用與付款
甲方應依下列條件向乙方支付對價：
{fee_terms}

## 第五條 智慧財產權
除雙方另有書面約定外，乙方於履約前已持有之技術、方法、工具、模板及背景智慧財產權，仍歸乙方所有。

## 第六條 保密義務
雙方對於因本契約而知悉之他方非公開資訊，均應負保密義務，除為履約必要、依法令規定或經他方事前書面同意外，不得揭露予第三人。

## 第七條 責任限制
除因故意、重大過失、違反保密義務或依法不得限制責任之情形外，{liability_cap}。

## 第八條 契約終止
一方重大違反本契約，經他方書面催告後於合理期間內仍未改善者，他方得終止本契約。

## 第九條 準據法與管轄
本契約之解釋、效力、履行及相關爭議，均以 {governing_law} 為準據法，並以 {jurisdiction} 為第一審管轄法院。
""",
    ),
    "employment": ContractTemplate(
        template_id="employment",
        name="勞動契約",
        description="正式版一般聘僱勞動契約模板，涵蓋職務、工時、薪資、保密與終止條款。",
        fields=(
            TemplateField("employer_name", "雇主名稱", "甲方股份有限公司"),
            TemplateField("employee_name", "勞工姓名", "王小明"),
            TemplateField("effective_date", "到職日期", "2026年4月12日"),
            TemplateField("job_title", "職稱", "法務專員"),
            TemplateField("work_location", "工作地點", "台北市信義區"),
            TemplateField("work_hours", "工時安排", "依公司正常工時制度及排班規定辦理"),
            TemplateField("salary", "薪資條件", "每月工資為新台幣 60,000 元，按月於次月五日前發放"),
            TemplateField("probation_terms", "試用約定", "試用期間為三個月，試用期間之工作表現、考核與待遇依公司制度及法令辦理"),
        ),
        body="""# 勞動契約

本勞動契約（以下稱「本契約」）由下列當事人於 {effective_date} 訂立：

雇主（甲方）：{employer_name}
勞工（乙方）：{employee_name}

## 第一條 聘僱與職務
甲方聘僱乙方擔任 {job_title}，乙方同意依甲方合法合理之指揮監督提供勞務。

## 第二條 到職日期與試用
乙方自 {effective_date} 起到職。關於試用安排，雙方約定如下：
{probation_terms}

## 第三條 工作地點
乙方之主要工作地點為 {work_location}。甲方基於業務需要，得於符合法令之前提下為合理調整。

## 第四條 工作時間
乙方之工作時間及出勤規則如下：
{work_hours}

## 第五條 工資與給付
甲方應依下列條件給付乙方工資：
{salary}

## 第六條 保密義務
乙方對任職期間知悉之營業秘密、客戶資訊、技術資料及其他非公開資訊，應負保密義務。

## 第七條 契約終止
本契約之終止、資遣、離職預告及相關權利義務，依勞動基準法及相關法令辦理。
""",
    ),
}


def list_templates() -> list[ContractTemplate]:
    return list(TEMPLATES.values())


def get_template(template_id: str) -> ContractTemplate:
    return TEMPLATES[template_id]


def render_template(template_id: str, values: dict[str, str]) -> str:
    template = get_template(template_id)
    rendered = template.body
    for field in template.fields:
        value = (values.get(field.key) or field.default).strip()
        rendered = rendered.replace(f"{{{field.key}}}", value)
    return rendered


def _split_update_requests(clause_updates: str) -> list[str]:
    parts = re.split(r"[\n,，；;]+", clause_updates)
    return [part.strip() for part in parts if part.strip()]


def _generate_formal_clause(request: str) -> tuple[str, str]:
    text = request.strip()
    if "台灣法律" in text or "台湾法律" in text or "準據法" in text:
        return (
            "準據法",
            "## 準據法\n本契約之解釋、效力、履行及相關爭議，應以中華民國法律為準據法。",
        )
    if "違約金" in text or "违约金" in text:
        return (
            "違約責任條款",
            "## 違約責任\n任一方違反本契約約定，致他方受有損害者，應負損害賠償責任。"
            "如雙方另有約定違約金者，違約方並應依約給付違約金；違約金不足填補損害時，守約方仍得請求其差額。",
        )
    if "保密" in text:
        return (
            "保密條款",
            "## 保密義務\n除本契約另有約定外，任一方對於因締約、履約或合作關係所知悉之他方非公開資訊，"
            "均應盡保密義務，非經他方事前書面同意，不得揭露予第三人或挪作他用。",
        )
    if "付款" in text:
        return (
            "付款條款",
            "## 付款安排\n除雙方另有書面約定外，付款義務人應於收到合法請款文件後三十日內完成付款；"
            "逾期者，收款方得催告限期給付。",
        )
    if "終止" in text or "解除" in text:
        return (
            "終止條款",
            "## 契約終止\n任一方如有重大違約情事，經他方書面催告後於合理期間內仍未改善者，"
            "他方得終止或解除本契約，並得依法請求損害賠償。",
        )
    return ("其他修訂事項", f"## 其他修訂事項\n{text}")


def apply_clause_updates(base_text: str, clause_updates: str) -> str:
    if not clause_updates.strip():
        return base_text
    requests = _split_update_requests(clause_updates)
    clauses: list[str] = []
    for request in requests:
        _title, clause = _generate_formal_clause(request)
        clauses.append(clause)
    return base_text.rstrip() + "\n\n" + "\n\n".join(clauses) + "\n"


def _infer_block_title(change_type: str, before_text: str, after_text: str, counter: int) -> str:
    source = after_text or before_text
    for line in source.splitlines():
        stripped = line.strip().lstrip("#").strip()
        if stripped:
            return f"{change_type}{stripped}"
    return f"{change_type}條款 {counter}"


def _build_redline_blocks(original_lines: list[str], revised_lines: list[str]) -> tuple[RedlineBlock, ...]:
    matcher = SequenceMatcher(None, original_lines, revised_lines)
    blocks: list[RedlineBlock] = []
    counter = 1
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            continue
        before_text = "\n".join(original_lines[i1:i2]).strip()
        after_text = "\n".join(revised_lines[j1:j2]).strip()
        if tag == "insert":
            change_type = "新增"
        elif tag == "delete":
            change_type = "刪除"
        else:
            change_type = "修改"
        blocks.append(
            RedlineBlock(
                change_type=change_type,
                title=_infer_block_title(change_type, before_text, after_text, counter),
                before_text=before_text,
                after_text=after_text,
            )
        )
        counter += 1
    return tuple(blocks)


def summarize_redline(
    original_text: str,
    revised_text: str,
    *,
    original_name: str = "原始版本",
    revised_name: str = "修訂版本",
) -> RedlineSummary:
    original_lines = original_text.splitlines()
    revised_lines = revised_text.splitlines()
    changed_lines = 0
    added_lines = 0
    removed_lines = 0
    max_len = max(len(original_lines), len(revised_lines))
    for idx in range(max_len):
        left = original_lines[idx] if idx < len(original_lines) else ""
        right = revised_lines[idx] if idx < len(revised_lines) else ""
        if left == right:
            continue
        changed_lines += 1
        if left and not right:
            removed_lines += 1
        elif right and not left:
            added_lines += 1
        else:
            added_lines += 1
            removed_lines += 1
    html = HtmlDiff(wrapcolumn=90).make_table(
        original_lines,
        revised_lines,
        fromdesc=original_name,
        todesc=revised_name,
        context=True,
        numlines=2,
    )
    blocks = _build_redline_blocks(original_lines, revised_lines)
    return RedlineSummary(
        html=html,
        changed_lines=changed_lines,
        added_lines=added_lines,
        removed_lines=removed_lines,
        blocks=blocks,
    )


def extract_placeholders(text: str) -> list[str]:
    return sorted(set(re.findall(r"\{([a-zA-Z0-9_]+)\}", text)))
