"""Shared helpers for localizing and safely exporting result frames."""

from __future__ import annotations

import numpy as np
import pandas as pd

# OWASP CSV-injection prefixes: a cell starting with these can be interpreted
# as a formula by Excel / Google Sheets when the export file is opened.
_FORMULA_PREFIXES = ("=", "+", "-", "@", "\t", "\r")


def booleans_to_chinese(df: pd.DataFrame) -> pd.DataFrame:
    """將真正的布林欄位（突破、回測守住、法人條件等）轉為中文「是／否」。

    僅針對值全部為布林型別的欄位，避免誤判只含 0/1 的整數欄位。
    """
    frame = df.copy()
    for col in frame.columns:
        non_null = frame[col].dropna()
        if non_null.empty:
            continue
        # 注意不能用 type(value).__name__ 比對 "bool_"：NumPy 2 已把布林純量
        # 類別改名為 numpy.bool，字串比對會讓整個轉換靜默失效。
        is_boolean_column = pd.api.types.is_bool_dtype(non_null) or all(
            isinstance(value, (bool, np.bool_)) for value in non_null.unique()
        )
        if is_boolean_column:
            frame[col] = frame[col].map({True: "是", False: "否"})
    return frame


def sanitize_for_spreadsheet(df: pd.DataFrame) -> pd.DataFrame:
    """Neutralize formula-injection payloads in string cells before export.

    User-supplied values (e.g. stock codes from a pasted list or uploaded
    file) flow into the exported CSV/Excel; a value like ``=cmd|...`` would
    otherwise execute when the recipient opens the file. Dangerous leading
    characters are escaped with a ``'`` prefix, which spreadsheets display
    as plain text. Numeric columns are untouched.
    """
    frame = df.copy()
    for col in frame.columns:
        if frame[col].dtype == object:
            frame[col] = frame[col].map(
                lambda value: f"'{value}"
                if isinstance(value, str) and value.startswith(_FORMULA_PREFIXES)
                else value
            )
    return frame
