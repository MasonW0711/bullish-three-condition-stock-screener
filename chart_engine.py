"""Chart rendering helpers for the stock screener."""

from __future__ import annotations

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from config import CHART_BASE_LINE_STYLES


def create_stock_chart(stock_df: pd.DataFrame, timeframe_label: str):
    """Create an interactive candlestick chart for a single stock."""
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
            name="K 線",
        ),
        row=1,
        col=1,
    )

    for column_name, style in CHART_BASE_LINE_STYLES.items():
        if column_name in chart_df.columns and chart_df[column_name].notna().any():
            fig.add_trace(
                go.Scatter(
                    x=chart_df["Date"],
                    y=chart_df[column_name],
                    mode="lines",
                    name=style["name"],
                    line={"color": style["color"], "width": 1.8},
                ),
                row=1,
                col=1,
            )

    signal_rows = chart_df[chart_df["final_long_signal"].fillna(False)]
    if not signal_rows.empty:
        fig.add_trace(
            go.Scatter(
                x=signal_rows["Date"],
                y=signal_rows["Close"],
                mode="markers",
                name="最終多頭訊號",
                marker={"color": "#f59e0b", "size": 10, "symbol": "triangle-up"},
                text=[
                    f"分數：{int(score)}"
                    for score in signal_rows["long_signal_score"].fillna(0).tolist()
                ],
                hovertemplate="%{x|%Y-%m-%d}<br>%{text}<br>收盤：%{y:.2f}<extra></extra>",
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

    fig.update_layout(
        title=f"{stock_code} 多頭三條件訊號圖 ({timeframe_label})",
        xaxis_rangeslider_visible=False,
        legend={"orientation": "h", "yanchor": "bottom", "y": 1.02, "x": 0},
        margin={"l": 20, "r": 20, "t": 70, "b": 20},
        height=720,
    )
    fig.update_yaxes(title_text="價格", row=1, col=1)
    fig.update_yaxes(title_text="成交量", row=2, col=1)

    return fig, None
