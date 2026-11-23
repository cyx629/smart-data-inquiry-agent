"""图表建议(设计文档 §4.6):规则优先,输出 ECharts 配置 JSON。

- 用户显式要求的图表类型优先(饼图/柱状图/折线图/趋势);
- 自动规则:时间+度量 → 折线;占比语义 → 饼图;1 维 + 1 度量 → 柱状;
- 点数上限:折线 120(长时间序列常见)、柱状 40、饼图 12(超出自动取 Top11+其他)。
"""
from __future__ import annotations

import re

_TIME_PATTERNS = [
    re.compile(r"^\d{4}$"),
    re.compile(r"^\d{4}[-/年]\d{1,2}月?$"),
    re.compile(r"^\d{4}[-/]\d{1,2}[-/]\d{1,2}"),
    re.compile(r"^\d{4}Q[1-4]$", re.IGNORECASE),
]

_MAX_LINE, _MAX_BAR, _MAX_PIE = 120, 40, 12


def _is_time_value(v) -> bool:
    return isinstance(v, str) and any(p.match(v.strip()) for p in _TIME_PATTERNS)


def _classify_columns(rows: list[dict]) -> tuple[list[str], list[str], list[str]]:
    """返回 (时间列, 类别列, 数值列)。以非空值占多数的类型为准。"""
    time_cols, cat_cols, num_cols = [], [], []
    for col in rows[0].keys():
        values = [r.get(col) for r in rows if r.get(col) is not None]
        if not values:
            cat_cols.append(col)
            continue
        n = len(values)
        if sum(isinstance(v, (int, float)) and not isinstance(v, bool) for v in values) > n / 2:
            num_cols.append(col)
        elif sum(_is_time_value(v) for v in values) > n / 2:
            time_cols.append(col)
        else:
            cat_cols.append(col)
    return time_cols, cat_cols, num_cols


def requested_chart_type(question: str) -> str | None:
    """用户显式要求的图表类型(说了就听用户的)。"""
    if any(k in question for k in ("饼图", "饼状", "占比图")):
        return "pie"
    if any(k in question for k in ("折线", "趋势图", "走势")):
        return "line"
    if any(k in question for k in ("柱状", "柱形", "条形")):
        return "bar"
    return None


def _line_option(rows, axis, num_cols):
    data = sorted(rows, key=lambda r: str(r.get(axis, "")))
    return {
        "title": {"text": f"{num_cols[0]}趋势"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category", "data": [str(r.get(axis, "")) for r in data]},
        "yAxis": {"type": "value"},
        "series": [{"name": m, "type": "line", "smooth": True,
                    "data": [r.get(m) for r in data]} for m in num_cols[:3]],
        "legend": {"show": len(num_cols) > 1},
    }


def _bar_option(rows, dim, num_cols):
    return {
        "title": {"text": f"各{dim}的{num_cols[0]}"},
        "tooltip": {"trigger": "axis"},
        "xAxis": {"type": "category",
                  "data": [str(r.get(dim, "")) for r in rows],
                  "axisLabel": {"interval": 0, "rotate": 30 if len(rows) > 8 else 0}},
        "yAxis": {"type": "value"},
        "series": [{"name": m, "type": "bar", "data": [r.get(m) for r in rows]}
                   for m in num_cols[:3]],
        "legend": {"show": len(num_cols) > 1},
    }


def _pie_option(rows, dim, measure):
    # 超过上限:取 Top N-1 + "其他"合并,保证饼图可读
    data = sorted(rows, key=lambda r: (r.get(measure) or 0), reverse=True)
    if len(data) > _MAX_PIE:
        head, tail = data[:_MAX_PIE - 1], data[_MAX_PIE - 1:]
        other = sum(r.get(measure) or 0 for r in tail)
        data = head + [{dim: "其他", measure: round(other, 2)}]
    return {
        "title": {"text": f"{dim}{measure}占比"},
        "tooltip": {"trigger": "item", "formatter": "{b}: {c} ({d}%)"},
        "series": [{
            "type": "pie", "radius": ["35%", "65%"],
            "data": [{"name": str(r.get(dim, "")), "value": r.get(measure)} for r in data],
        }],
    }


def suggest_chart(question: str, rows: list[dict]) -> dict | None:
    if not rows or len(rows) < 2:
        return None
    time_cols, cat_cols, num_cols = _classify_columns(rows)
    if not num_cols:
        return None
    measure = num_cols[0]
    dim = time_cols[0] if time_cols else (cat_cols[0] if cat_cols else None)
    if dim is None:
        return None

    want = requested_chart_type(question)

    if want == "pie":
        return _pie_option(rows, dim, measure)
    if want == "line":
        if len(rows) <= _MAX_LINE:
            return _line_option(rows, dim, num_cols)
        return None
    if want == "bar":
        if len(rows) <= _MAX_BAR:
            return _bar_option(rows, dim, num_cols)
        return None

    # 自动规则
    if time_cols and len(rows) <= _MAX_LINE:
        return _line_option(rows, time_cols[0], num_cols)
    if not cat_cols:
        return None
    if any(k in question for k in ("占比", "比例", "分布", "构成")):
        return _pie_option(rows, cat_cols[0], measure)
    if len(rows) <= _MAX_BAR:
        return _bar_option(rows, cat_cols[0], num_cols)
    return None
