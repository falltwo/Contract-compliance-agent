"""對公司有用的 Tools：計算／分析、日期／排程／小幫手。

供總管 Agent 路由呼叫，測試「模型選工具 → 工具回傳 → 模型整合回答」流程。
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any


def _to_float(x: Any) -> float | None:
    if x is None:
        return None
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(str(x).strip().replace(",", ""))
    except (TypeError, ValueError):
        return None


def financial_metrics(
    *,
    revenue_this_year: float | int | str | None = None,
    revenue_last_year: float | int | str | None = None,
    gross_margin_this_year: float | int | str | None = None,
    gross_margin_last_year: float | int | str | None = None,
    net_margin_this_year: float | int | str | None = None,
    net_margin_last_year: float | int | str | None = None,
    unit: str = "億",
) -> str:
    """財報指標計算：依今年／去年數據算成長率並給簡單評語。

    輸入至少要有「今年營收」與「去年營收」；毛利率、淨利率為選填。
    回傳：營收成長率、毛利率／淨利率變化（若有）、以及一句簡單評語。
    """
    lines: list[str] = []
    rev_this = _to_float(revenue_this_year)
    rev_last = _to_float(revenue_last_year)
    gm_this = _to_float(gross_margin_this_year)
    gm_last = _to_float(gross_margin_last_year)
    nm_this = _to_float(net_margin_this_year)
    nm_last = _to_float(net_margin_last_year)

    if rev_this is not None and rev_last is not None and rev_last != 0:
        growth = ((rev_this - rev_last) / rev_last) * 100
        lines.append(f"**營收成長率**：{growth:+.1f}%（今年 {rev_this}{unit} vs 去年 {rev_last}{unit}）")
        if growth > 10:
            lines.append("營收成長表現佳。")
        elif growth > 0:
            lines.append("營收微幅成長。")
        elif growth > -10:
            lines.append("營收略為下滑。")
        else:
            lines.append("營收明顯衰退，建議關注營運狀況。")
    elif rev_this is not None or rev_last is not None:
        lines.append("請同時提供「今年營收」與「去年營收」才能計算成長率。")
    else:
        lines.append("請提供 revenue_this_year 與 revenue_last_year（或問題中寫明今年／去年營收數字）。")

    if gm_this is not None and gm_last is not None:
        diff = gm_this - gm_last
        lines.append(f"**毛利率變化**：{gm_this:.1f}% vs 去年 {gm_last:.1f}%（{diff:+.1f}pp）")
    if nm_this is not None and nm_last is not None:
        diff = nm_this - nm_last
        lines.append(f"**淨利率變化**：{nm_this:.1f}% vs 去年 {nm_last:.1f}%（{diff:+.1f}pp）")

    return "\n\n".join(lines) if lines else "未提供足夠數字，請給今年／去年營收（或毛利率、淨利率）再計算。"


def parse_dates_from_text(text: str) -> str:
    """從文字中解析出所有日期，回傳條列。

    支援常見格式：YYYY-MM-DD、YYYY/MM/DD、YYYY年M月D日、M月D日、Q1 2025 等。
    """
    if not text or not text.strip():
        return "未提供文字，請在問題或 tool_args 的 text 中輸入要解析的內容。"

    found: list[str] = []
    t = text.strip()

    # YYYY-MM-DD, YYYY/MM/DD
    for m in re.finditer(r"(20\d{2})[-/](\d{1,2})[-/](\d{1,2})", t):
        y, mo, d = m.group(1), m.group(2).zfill(2), m.group(3).zfill(2)
        found.append(f"{y}-{mo}-{d}（西元年月日）")

    # YYYY年M月D日、YYYY年M月
    for m in re.finditer(r"(20\d{2})年(\d{1,2})月(\d{1,2})?日?", t):
        y, mo = m.group(1), m.group(2).zfill(2)
        d = m.group(3)
        if d:
            found.append(f"{y}年{mo}月{d}日")
        else:
            found.append(f"{y}年{mo}月")

    # M月D日（無年）
    for m in re.finditer(r"(\d{1,2})月(\d{1,2})日?", t):
        found.append(f"{m.group(1)}月{m.group(2)}日")

    # Q1 2025, 2025 Q1
    for m in re.finditer(r"(?:Q|第)?([1-4])\s*季度?\s*(20\d{2})?", t, re.IGNORECASE):
        q, y = m.group(1), m.group(2) or "（未指定年）"
        found.append(f"Q{q} {y}")

    for m in re.finditer(r"(20\d{2})\s*(?:年)?\s*Q([1-4])", t, re.IGNORECASE):
        found.append(f"{m.group(1)} Q{m.group(2)}")

    # 去重並保持順序
    seen: set[str] = set()
    unique = []
    for x in found:
        if x not in seen:
            seen.add(x)
            unique.append(x)

    if not unique:
        return "在提供的文字中未偵測到常見日期格式（可支援 YYYY-MM-DD、YYYY年M月、Q1 2025 等）。"
    return "解析到的日期：\n\n" + "\n".join(f"- {u}" for u in unique)


def generate_quarterly_plan(
    topic: str = "計畫",
    start_quarter: str = "2025Q1",
    num_quarters: int = 4,
) -> str:
    """產生未來數季的簡單計畫表（依主題給每季重點）。

    輸入：topic（主題，如「產品上市」「預算編列」）、start_quarter（起始季度，如 2025Q1）、num_quarters（預設 4 季）。
    回傳：每季一行的簡單計畫表，方便後續由模型整合進回答。
    """
    topic = (topic or "計畫").strip()
    start_quarter = (start_quarter or "2025Q1").strip().upper()
    num_quarters = max(1, min(int(num_quarters) if isinstance(num_quarters, int) else 4, 8))

    # 解析 2025Q1 或 Q1 2025
    m = re.match(r"(20\d{2})?\s*Q([1-4])", start_quarter, re.IGNORECASE)
    if m:
        y_str, q = m.group(1), int(m.group(2))
        year = int(y_str) if y_str else datetime.now().year
    else:
        year = datetime.now().year
        q = 1

    rows: list[str] = []
    for i in range(num_quarters):
        label = f"{year} Q{q}"
        if q == 1:
            focus = "規劃與啟動、目標確認"
        elif q == 2:
            focus = "執行與檢視、中期調整"
        elif q == 3:
            focus = "衝刺與收斂、里程碑檢核"
        else:
            focus = "收尾與結案、下年度預備"
        rows.append(f"| **{label}** | {topic}：{focus} |")
        q += 1
        if q > 4:
            q = 1
            year += 1

    return f"**「{topic}」未來 {num_quarters} 季簡單計畫表**\n\n| 季度 | 重點 |\n|------|------|\n" + "\n".join(rows)
