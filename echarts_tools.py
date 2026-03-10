"""ECharts 圖表工具：依類型與資料產生 ECharts option（與 ECharts MCP 參數語意一致）。

支援類型：bar, line, pie, scatter。Streamlit 端用 streamlit-echarts 渲染。
若需雲端圖片 URL，可另接 Apache ECharts MCP（Node）並改為呼叫該 MCP。
"""
from __future__ import annotations

from typing import Any


def _to_float(x: Any) -> float:
    if isinstance(x, (int, float)):
        return float(x)
    try:
        return float(x)
    except (TypeError, ValueError):
        return 0.0


def create_chart_option(
    chart_type: str,
    data: Any,
    *,
    title: str | None = None,
    series_name: str | None = None,
    x_axis_name: str | None = None,
    y_axis_name: str | None = None,
    x_axis_data: list[str] | None = None,
) -> dict[str, Any]:
    """依 ECharts MCP 風格的參數產生 ECharts option，供 streamlit-echarts 使用。

    - chart_type: bar | line | pie | scatter
    - data: 依類型為 [1,2,3]（與 x 對應）、[[x,y],[x,y]]（scatter）、或 [{name, value}, ...]（pie）
    - title / series_name / x_axis_name / y_axis_name: 標題與軸名
    - x_axis_data: 類目軸標籤（bar/line 若無則用 ["1","2",...]）
    """
    chart_type = (chart_type or "bar").strip().lower()
    if chart_type not in ("bar", "line", "pie", "scatter"):
        chart_type = "bar"

    # 正規化 data
    if data is None:
        data = []
    if isinstance(data, str):
        try:
            import json
            data = json.loads(data)
        except Exception:
            data = []
    if not isinstance(data, list):
        data = [data]

    option: dict[str, Any] = {"title": {"text": title or "圖表", "left": "center"}}

    if chart_type == "pie":
        # data: [{"name": "A", "value": 10}, ...] 或 [[name, value], ...]
        series_data: list[dict[str, Any]] = []
        for i, item in enumerate(data):
            if isinstance(item, dict) and "name" in item and "value" in item:
                series_data.append(item)
            elif isinstance(item, (list, tuple)) and len(item) >= 2:
                series_data.append({"name": str(item[0]), "value": _to_float(item[1])})
            else:
                series_data.append({"name": str(i), "value": _to_float(item)})
        option["tooltip"] = {"trigger": "item"}
        option["series"] = [{"type": "pie", "radius": "60%", "data": series_data, "label": {"show": True}}]

    elif chart_type == "scatter":
        # data: [[x,y], [x,y], ...] 或 [{value: [x,y]}, ...]
        scatter_data = []
        for item in data:
            if isinstance(item, (list, tuple)) and len(item) >= 2:
                scatter_data.append(item)
            elif isinstance(item, dict) and "value" in item:
                scatter_data.append(item["value"])
            else:
                continue
        option["tooltip"] = {"trigger": "item"}
        option["xAxis"] = {"type": "value", "name": x_axis_name or ""}
        option["yAxis"] = {"type": "value", "name": y_axis_name or ""}
        option["series"] = [{"type": "scatter", "name": series_name or "資料", "data": scatter_data, "symbolSize": 10}]

    else:
        # bar / line
        if not x_axis_data and data and isinstance(data[0], (list, tuple)) and len(data[0]) >= 2:
            # data 為 [[label, val], ...]
            x_axis_data = [str(x[0]) for x in data]
            data = [x[1] if len(x) > 1 else 0 for x in data]
        if not x_axis_data:
            x_axis_data = [str(i + 1) for i in range(len(data))]
        vals = [_to_float(x) for x in data]
        option["tooltip"] = {"trigger": "axis"}
        option["xAxis"] = {"type": "category", "data": x_axis_data, "name": x_axis_name or ""}
        option["yAxis"] = {"type": "value", "name": y_axis_name or ""}
        option["series"] = [{"type": chart_type, "name": series_name or "數值", "data": vals}]

    return option
