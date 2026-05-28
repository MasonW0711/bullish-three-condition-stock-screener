"""Chart rendering helpers — candlestick with attack signal markers."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

# Marker styles for each of the four attack signals.
_SIGNAL_MARKERS = {
    "red_attack_success": {
        "label": "大紅攻成功",
        "color": "#dc2626",       # red
        "symbol": "triangle-up",
        "y_col": "High",
        "y_offset_sign": +1,
    },
    "red_attack_failed": {
        "label": "大紅攻失敗",
        "color": "#f97316",       # orange
        "symbol": "x",
        "y_col": "Low",
        "y_offset_sign": -1,
    },
    "black_attack_success": {
        "label": "大黑攻成功",
        "color": "#1d4ed8",       # dark blue
        "symbol": "triangle-down",
        "y_col": "Low",
        "y_offset_sign": -1,
    },
    "black_attack_failed": {
        "label": "大黑攻失敗",
        "color": "#16a34a",       # green
        "symbol": "triangle-up",
        "y_col": "High",
        "y_offset_sign": +1,
    },
}


def create_stock_chart(stock_df: pd.DataFrame, timeframe_label: str):
    """Create an interactive candlestick chart for a single stock.

    Markers indicate each of the four attack signal types.
    No base lines are drawn.
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

    # Volume bar colours: green when Close >= Open, red otherwise.
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

    # --- Candlestick ---
    fig.add_trace(
        go.Candlestick(
            x=chart_df["Date"],
            open=chart_df["Open"],
            high=chart_df["High"],
            low=chart_df["Low"],
            close=chart_df["Close"],
            name="K 線",
        ),
        row=1,
        col=1,
    )

    # --- Attack signal markers ---
    price_range = chart_df["High"].max() - chart_df["Low"].min()
    offset_pct = 0.015  # marker offset as fraction of price range
    y_offset = price_range * offset_pct if price_range > 0 else 0

    for col_name, cfg in _SIGNAL_MARKERS.items():
        if col_name not in chart_df.columns:
            continue
        signal_rows = chart_df[chart_df[col_name].fillna(False)]
        if signal_rows.empty:
            continue

        y_base = signal_rows[cfg["y_col"]]
        y_values = y_base + cfg["y_offset_sign"] * y_offset

        fig.add_trace(
            go.Scatter(
                x=signal_rows["Date"],
                y=y_values,
                mode="markers",
                name=cfg["label"],
                marker={
                    "color": cfg["color"],
                    "size": 10,
                    "symbol": cfg["symbol"],
                },
                hovertemplate=(
                    "%{x|%Y-%m-%d}<br>"
                    + cfg["label"]
                    + "<br>收盤：%{customdata:.2f}<extra></extra>"
                ),
                customdata=signal_rows["Close"].values,
            ),
            row=1,
            col=1,
        )

    # --- Volume bars ---
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

    fig.update_layout(
        title=f"{stock_code} 大紅攻 / 大黑攻 訊號圖（{timeframe_label}）",
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 20, "r": 20, "t": 70, "b": 20},
        height=720,
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig, None
