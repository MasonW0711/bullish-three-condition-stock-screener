"""Chart rendering helpers for breakout-and-retest-hold signals."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

_LINE_STYLES = [
    ("red_line", "#dc2626", "紅線"),
    ("black_line", "#111827", "黑線"),
]

_MARKER_STYLES = [
    ("break_red_line_daily", "突破紅線", "#dc2626", "triangle-up", "High", 1),
    ("break_black_line_daily", "突破黑線", "#111827", "triangle-up", "High", 1),
    ("break_down_red_line", "跌破紅線", "#dc2626", "triangle-down", "Low", -1),
    ("break_down_black_line", "跌破黑線", "#111827", "triangle-down", "Low", -1),
]


def create_stock_chart(stock_df: pd.DataFrame, timeframe_label: str, direction: str | None = None):
    """Create an interactive candlestick chart for one stock.

    ``direction`` (做多 / 做空 / None) only adjusts the title; all relevant
    breakout/breakdown and retest markers are drawn regardless so the chart
    stays informative on either tab.
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
    volume_colors = [
        "#16a34a" if close >= open_price else "#dc2626"
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
                line={"color": line_color, "width": 1.5, "dash": "dash"},
                name=line_label,
                connectgaps=False,
            ),
            row=1,
            col=1,
        )

    price_range = chart_df["High"].max() - chart_df["Low"].min()
    y_offset = price_range * 0.015 if price_range > 0 else 0

    for col_name, label, color, symbol, y_col, y_sign in _MARKER_STYLES:
        if col_name not in chart_df.columns:
            continue
        rows = chart_df[chart_df[col_name].fillna(False)]
        if rows.empty:
            continue
        fig.add_trace(
            go.Scatter(
                x=rows["Date"],
                y=rows[y_col] + y_sign * y_offset,
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
    fig.update_layout(
        title=f"{stock_code} 突破／跌破與回測（{timeframe_label}{direction_suffix}）",
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 20, "r": 20, "t": 70, "b": 20},
        height=720,
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig, None
