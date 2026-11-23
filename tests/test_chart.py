"""图表建议规则测试(设计文档 §4.6)。"""
from app.core.chart import suggest_chart


def test_category_measure_to_bar():
    rows = [{"地区": "华东", "销售额": 100.0}, {"地区": "华北", "销售额": 80.0}]
    opt = suggest_chart("各地区销售额", rows)
    assert opt and opt["series"][0]["type"] == "bar"


def test_time_measure_to_line():
    rows = [{"月份": "2026-01", "销售额": 10.0}, {"月份": "2026-02", "销售额": 12.0}]
    opt = suggest_chart("每月销售额趋势", rows)
    assert opt and opt["series"][0]["type"] == "line"
    assert opt["xAxis"]["data"] == ["2026-01", "2026-02"]


def test_proportion_to_pie():
    rows = [{"地区": "华东", "销售额": 60.0}, {"地区": "华北", "销售额": 40.0}]
    opt = suggest_chart("各地区销售额占比", rows)
    assert opt and opt["series"][0]["type"] == "pie"


def test_single_row_no_chart():
    assert suggest_chart("总销售额", [{"销售额": 100.0}]) is None


def test_no_numeric_no_chart():
    rows = [{"客户": "远大集团"}, {"客户": "宏图科技"}]
    assert suggest_chart("客户名单", rows) is None


def test_long_time_series_still_charts():
    # 回归:32 个月的趋势数据曾被 30 点上限卡掉
    rows = [{"月份": f"{2024 + i // 12}-{i % 12 + 1:02d}", "销售额": 100.0 + i}
            for i in range(32)]
    opt = suggest_chart("各月份销售额趋势", rows)
    assert opt and opt["series"][0]["type"] == "line"
    assert len(opt["xAxis"]["data"]) == 32


def test_user_requested_pie_wins():
    rows = [{"地区": r, "销售额": v} for r, v in
            [("华东", 60.0), ("华北", 40.0), ("华南", 30.0)]]
    opt = suggest_chart("华东大区的销售额(柱状图和饼图)", rows)
    # 同时提到两种时饼图优先级最高(更明确的占比意图)
    assert opt and opt["series"][0]["type"] == "pie"


def test_user_requested_bar_over_time_auto():
    rows = [{"月份": "2026-01", "销售额": 10.0}, {"月份": "2026-02", "销售额": 12.0}]
    opt = suggest_chart("每月销售额柱状图", rows)
    assert opt and opt["series"][0]["type"] == "bar"


def test_pie_top_n_merge():
    rows = [{"客户": f"客户{i}", "金额": float(100 - i)} for i in range(20)]
    opt = suggest_chart("各客户金额饼图", rows)
    assert opt and opt["series"][0]["type"] == "pie"
    data = opt["series"][0]["data"]
    assert len(data) == 12
    assert data[-1]["name"] == "其他"


def test_render_png():
    from app.core.chart_render import render_png
    rows = [{"地区": "华东", "销售额": 60.0}, {"地区": "华北", "销售额": 40.0}]
    opt = suggest_chart("各地区销售额", rows)
    png = render_png(opt)
    assert png and png[:4] == b"\x89PNG"
    line_opt = suggest_chart("趋势", [{"月份": "2026-01", "销售额": 1.0},
                                      {"月份": "2026-02", "销售额": 2.0}])
    assert render_png(line_opt)[:4] == b"\x89PNG"
    pie_opt = suggest_chart("占比", [{"地区": "华东", "销售额": 6.0},
                                     {"地区": "华北", "销售额": 4.0}])
    assert render_png(pie_opt)[:4] == b"\x89PNG"
