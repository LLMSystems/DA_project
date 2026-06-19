from __future__ import annotations

import re
from pathlib import Path

import aiosqlite

# 同時相容 DB 內「民國114年10月16日」與輸入「114/09/01」「114-09-01」等寫法。
_ROC_PATTERNS = (
    re.compile(r"民國\s*(\d+)\s*年\s*(\d+)\s*月\s*(\d+)\s*日"),
    re.compile(r"(\d+)\s*[/\-.]\s*(\d+)\s*[/\-.]\s*(\d+)"),
)


def roc_to_int(value: str | None) -> int | None:
    """把民國日期字串轉成可比較的整數 YYYMMDD（皆為民國年，可直接比大小）。無法解析回 None。"""
    if not value:
        return None
    for pattern in _ROC_PATTERNS:
        m = pattern.search(value)
        if m:
            year, month, day = (int(g) for g in m.groups())
            return year * 10000 + month * 100 + day
    return None

# 回傳給 API 使用者的欄位（隱藏 row_hash、source_* 等內部欄位）。
RETURN_COLUMNS = [
    "city",
    "township",
    "area_name",
    "edit_date",
    "change_date",
    "old_address",
    "new_address",
    "edit_type",
    "query_date_start",
    "query_date_end",
    "scraped_at",
]


async def db_status(db_path: Path) -> dict:
    """檢查 DB 是否可用，回傳 {available, reason, records}。供 /health 與查詢前檢查使用。"""
    if not db_path.exists():
        return {"available": False, "reason": f"DB 不存在：{db_path}", "records": 0}
    try:
        async with aiosqlite.connect(db_path) as db:
            cursor = await db.execute("SELECT COUNT(*) FROM doorplate_records")
            row = await cursor.fetchone()
        return {"available": True, "reason": "", "records": int(row[0]) if row else 0}
    except Exception as exc:  # noqa: BLE001 - 任何連線/結構問題都視為不可用並回報原因
        return {"available": False, "reason": str(exc), "records": 0}


async def query_records(
    db_path: Path,
    cities: list[str],
    township: str,
    *,
    edit_type: str | None = None,
    edit_date_start: str | None = None,
    edit_date_end: str | None = None,
) -> list[dict]:
    """依縣市（多個等價寫法）與鄉鎮市區查詢門牌資料；其餘為選用過濾。本服務僅做 SELECT（唯讀）。

    - edit_type 直接在 SQL 精確比對。
    - 編訂日期區間因 DB 內存「民國114年10月16日」格式，於 Python 端解析後過濾，較字串比較穩定。
    """
    columns = ", ".join(RETURN_COLUMNS)
    placeholders = ", ".join("?" for _ in cities)
    sql = (
        f"SELECT {columns} FROM doorplate_records "
        f"WHERE city IN ({placeholders}) AND township = ?"
    )
    params: list[str] = [*cities, township]
    if edit_type:
        sql += " AND edit_type = ?"
        params.append(edit_type)
    sql += " ORDER BY area_name, change_date"

    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(sql, params)
        rows = [dict(row) for row in await cursor.fetchall()]

    start = roc_to_int(edit_date_start)
    end = roc_to_int(edit_date_end)
    if start is None and end is None:
        return rows

    # 有日期過濾時：無法解析 edit_date 的列予以排除（無法確認是否落在區間內）。
    filtered = []
    for row in rows:
        value = roc_to_int(row.get("edit_date"))
        if value is None:
            continue
        if start is not None and value < start:
            continue
        if end is not None and value > end:
            continue
        filtered.append(row)
    return filtered
