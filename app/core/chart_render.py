"""图表服务端渲染:ECharts 配置 → PNG 字节(matplotlib)。

前端组件(Plotly/ECharts)依赖浏览器加载 JS,内网/受限网络会静默失败;
服务端出 PNG 保证图表任何环境都能显示。自动选择可用的中文字体。
"""
from __future__ import annotations

import io

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt          # noqa: E402
from matplotlib import font_manager      # noqa: E402

_CJK_CANDIDATES = ["PingFang SC", "Heiti SC", "Hei", "Arial Unicode MS",
                   "Microsoft YaHei", "Noto Sans CJK SC", "WenQuanYi Zen Hei",
                   "SimHei", "Songti SC"]


def _setup_font() -> None:
    available = {f.name for f in font_manager.fontManager.ttflist}
    for name in _CJK_CANDIDATES:
        if name in available:
            plt.rcParams["font.sans-serif"] = [name, "DejaVu Sans"]
            break
    plt.rcParams["axes.unicode_minus"] = False


_setup_font()

_COLORS = ["#2563eb", "#f59e0b", "#10b981", "#ef4444", "#8b5cf6",
           "#06b6d4", "#f97316", "#84cc16", "#ec4899", "#64748b",
           "#14b8a6", "#a855f7"]


def render_png(option: dict) -> bytes | None:
    """将 suggest_chart 产出的 ECharts 配置渲染为 PNG。失败返回 None。"""
    try:
        s0 = option["series"][0]
        title = option.get("title", {}).get("text", "")
        fig, ax = plt.subplots(figsize=(9, 4.2), dpi=110)
        try:
            if s0["type"] == "pie":
                labels = [d["name"] for d in s0["data"]]
                values = [d["value"] or 0 for d in s0["data"]]
                ax.pie(values, labels=labels, autopct="%1.1f%%",
                       colors=_COLORS[:len(values)], startangle=90,
                       wedgeprops={"width": 0.45}, pctdistance=0.78,
                       textprops={"fontsize": 9})
                ax.set_aspect("equal")
            else:
                x = option["xAxis"]["data"]
                idx = range(len(x))
                for i, s in enumerate(option["series"][:3]):
                    color = _COLORS[i % len(_COLORS)]
                    if s["type"] == "line":
                        ax.plot(idx, s["data"], marker="o", markersize=3.5,
                                linewidth=1.8, color=color, label=s.get("name", ""))
                    else:
                        n = len(option["series"][:3])
                        width = 0.8 / n
                        ax.bar([j + i * width - 0.4 + width / 2 for j in idx],
                               s["data"], width=width, color=color,
                               label=s.get("name", ""))
                ax.set_xticks(list(idx))
                rotate = 45 if len(x) > 10 else (30 if len(x) > 6 else 0)
                ax.set_xticklabels([str(v) for v in x], rotation=rotate,
                                   ha="right" if rotate else "center", fontsize=8.5)
                ax.grid(axis="y", alpha=0.25, linewidth=0.6)
                for spine in ("top", "right"):
                    ax.spines[spine].set_visible(False)
                ax.margins(x=0.02)
                ax.ticklabel_format(axis="y", style="plain")
                if len(option["series"]) > 1 or option.get("legend", {}).get("show"):
                    ax.legend(fontsize=9, frameon=False)
            if title:
                ax.set_title(title, fontsize=12, pad=12)
            fig.tight_layout()
            buf = io.BytesIO()
            fig.savefig(buf, format="png", bbox_inches="tight")
            return buf.getvalue()
        finally:
            plt.close(fig)
    except Exception:
        return None
