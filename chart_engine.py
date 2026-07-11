"""Chart rendering helpers for breakout-and-retest-hold signals."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Taiwan market convention: red = up (漲), green = down (跌). The chart forces a
# light background (see update_layout) so the near-black 黑線 stays visible
# regardless of the viewer's Streamlit theme.
_UP_COLOR = "#dc2626"
_DOWN_COLOR = "#16a34a"
_RED_LINE_COLOR = "#dc2626"
_BLACK_LINE_COLOR = "#111827"

_LINE_STYLES = [
    ("red_line", _RED_LINE_COLOR, "紅線"),
    ("black_line", _BLACK_LINE_COLOR, "黑線"),
]

# (column, label, color, symbol, y_col, y_sign, stagger). ``stagger`` multiplies
# the vertical offset so that when both lines break on the same bar the two
# markers no longer sit on top of each other (the black one used to hide behind
# the red one).
_MARKER_STYLES = [
    ("break_red_line_daily", "突破紅線", _RED_LINE_COLOR, "triangle-up", "High", 1, 1),
    ("break_black_line_daily", "突破黑線", _BLACK_LINE_COLOR, "triangle-up", "High", 1, 2),
    ("break_down_red_line", "跌破紅線", _RED_LINE_COLOR, "triangle-down", "Low", -1, 1),
    ("break_down_black_line", "跌破黑線", _BLACK_LINE_COLOR, "triangle-down", "Low", -1, 2),
]


def create_stock_chart(
    stock_df: pd.DataFrame,
    timeframe_label: str,
    direction: str | None = None,
    stock_name: str | None = None,
):
    """Create an interactive candlestick chart for one stock.

    ``direction`` (做多 / 做空 / None) only adjusts the title; all relevant
    breakout/breakdown and retest markers are drawn regardless so the chart
    stays informative on either tab. ``stock_name`` is shown in the title next
    to the code; when omitted it is read from a ``StockName`` column if present.
    """
    if stock_df is None or stock_df.empty:
        return None, "目前沒有可供顯示的資料。"

    required_columns = {"Date", "StockCode", "Open", "High", "Low", "Close", "Volume"}
    missing_columns = required_columns.difference(stock_df.columns)
    if missing_columns:
        return None, f"圖表資料缺少必要欄位：{sorted(missing_columns)}"

    chart_df = stock_df.sort_values("Date").copy()
    if chart_df[["Open", "High", "Low", "Close"]].dropna(how="any").shape[0] < 2:
        return None, "歷史資料不足，無法為所選股票繪製可靠圖表。"

    stock_code = str(chart_df["StockCode"].iloc[-1])
    if stock_name is None and "StockName" in chart_df.columns:
        name_value = chart_df["StockName"].iloc[-1]
        if pd.notna(name_value):
            stock_name = str(name_value)
    volume_colors = [
        _UP_COLOR if close >= open_price else _DOWN_COLOR
        for open_price, close in zip(chart_df["Open"], chart_df["Close"])
    ]

    fig = make_subplots(
        rows=2,
        cols=1,
        shared_xaxes=True,
        vertical_spacing=0.06,
        row_heights=[0.72, 0.28],
    )

    fig.add_trace(
        go.Candlestick(
            x=chart_df["Date"],
            open=chart_df["Open"],
            high=chart_df["High"],
            low=chart_df["Low"],
            close=chart_df["Close"],
            name="K線",
            increasing={"line": {"color": _UP_COLOR}, "fillcolor": _UP_COLOR},
            decreasing={"line": {"color": _DOWN_COLOR}, "fillcolor": _DOWN_COLOR},
        ),
        row=1,
        col=1,
    )

    for line_col, line_color, line_label in _LINE_STYLES:
        if line_col not in chart_df.columns or chart_df[line_col].isna().all():
            continue
        fig.add_trace(
            go.Scatter(
                x=chart_df["Date"],
                y=chart_df[line_col],
                mode="lines",
                line={"color": line_color, "width": 1.5, "dash": "dash", "shape": "hv"},
                name=line_label,
                connectgaps=False,
            ),
            row=1,
            col=1,
        )

    price_range = chart_df["High"].max() - chart_df["Low"].min()
    y_offset = price_range * 0.015 if price_range > 0 else 0

    for col_name, label, color, symbol, y_col, y_sign, stagger in _MARKER_STYLES:
        if col_name not in chart_df.columns:
            continue
        rows = chart_df[chart_df[col_name].fillna(False)]
        if rows.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=rows["Date"],
                y=rows[y_col] + y_sign * y_offset * stagger,
                mode="markers",
                name=label,
                marker={"color": color, "size": 11, "symbol": symbol},
                hovertemplate="%{x|%Y-%m-%d}<br>" + label + "<br>收盤：%{customdata:.2f}<extra></extra>",
                customdata=rows["Close"].values,
            ),
            row=1,
            col=1,
        )

    if "retest_hold_daily" in chart_df.columns:
        retest_rows = chart_df[chart_df["retest_hold_daily"].fillna(False)]
        for line_type, label, color in [
            ("Red Line", "紅線回測守住", "#f97316"),
            ("Black Line", "黑線回測守住", "#2563eb"),
        ]:
            rows = retest_rows[retest_rows["active_breakout_line_type"] == line_type]
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=rows["Date"],
                    y=rows["Low"] - y_offset,
                    mode="markers",
                    name=label,
                    marker={"color": color, "size": 11, "symbol": "circle"},
                    hovertemplate="%{x|%Y-%m-%d}<br>" + label + "<br>收盤：%{customdata:.2f}<extra></extra>",
                    customdata=rows["Close"].values,
                ),
                row=1,
                col=1,
            )

    if "retest_reject_daily" in chart_df.columns:
        reject_rows = chart_df[chart_df["retest_reject_daily"].fillna(False)]
        for line_type, label, color in [
            ("Red Line", "紅線回測壓回", "#b91c1c"),
            ("Black Line", "黑線回測壓回", "#1e3a8a"),
        ]:
            rows = reject_rows[reject_rows["active_breakdown_line_type"] == line_type]
            if rows.empty:
                continue
            fig.add_trace(
                go.Scatter(
                    x=rows["Date"],
                    y=rows["High"] + y_offset,
                    mode="markers",
                    name=label,
                    marker={"color": color, "size": 11, "symbol": "x"},
                    hovertemplate="%{x|%Y-%m-%d}<br>" + label + "<br>收盤：%{customdata:.2f}<extra></extra>",
                    customdata=rows["Close"].values,
                ),
                row=1,
                col=1,
            )

    fig.add_trace(
        go.Bar(
            x=chart_df["Date"],
            y=chart_df["Volume"],
            name="成交量",
            marker={"color": volume_colors},
        ),
        row=2,
        col=1,
    )

    direction_suffix = f"｜{direction}" if direction else ""
    name_prefix = f"{stock_code} {stock_name}" if stock_name and stock_name != stock_code else stock_code
    fig.update_layout(
        title=f"{name_prefix} 突破／跌破與回測（{timeframe_label}{direction_suffix}）",
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 20, "r": 20, "t": 70, "b": 20},
        height=720,
        # Force a light background so the near-black 黑線 and its markers stay
        # visible regardless of the viewer's (possibly dark) Streamlit theme.
        template="plotly_white",
        plot_bgcolor="white",
        paper_bgcolor="white",
        font={"color": "#111827"},
    )
    fig.update_xaxes(showgrid=True, gridcolor="#e5e7eb")
    fig.update_yaxes(title_text="價格", row=1, col=1, showgrid=True, gridcolor="#e5e7eb")
    fig.update_yaxes(title_text="成交量", row=2, col=1, showgrid=True, gridcolor="#e5e7eb")

    return fig, None
