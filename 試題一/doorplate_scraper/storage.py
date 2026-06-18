from __future__ import annotations

import aiosqlite
from pathlib import Path
from typing import Iterable

from .models import DoorplateRecord
from .utils import ensure_parent


CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS doorplate_records (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    row_hash TEXT NOT NULL UNIQUE,
    city TEXT NOT NULL,
    township TEXT NOT NULL,
    area_name TEXT NOT NULL,
    edit_date TEXT NOT NULL,
    change_date TEXT NOT NULL,
    old_address TEXT NOT NULL,
    new_address TEXT NOT NULL,
    edit_type TEXT NOT NULL,
    query_city TEXT NOT NULL,
    query_township TEXT NOT NULL,
    query_date_start TEXT NOT NULL,
    query_date_end TEXT NOT NULL,
    source_url TEXT NOT NULL,
    source_page INTEGER NOT NULL,
    source_row_index INTEGER NOT NULL,
    scraped_at TEXT NOT NULL
);
"""


async def init_db(db_path: Path) -> None:
    ensure_parent(db_path)
    async with aiosqlite.connect(db_path) as db:
        await db.execute(CREATE_TABLE_SQL)
        await db.commit()


async def insert_records(db_path: Path, records: Iterable[DoorplateRecord]) -> int:
    rows = [record.as_dict() for record in records]
    if not rows:
        return 0

    async with aiosqlite.connect(db_path) as db:
        await db.execute(CREATE_TABLE_SQL)
        before_changes = db.total_changes
        await db.executemany(
            """
            INSERT OR IGNORE INTO doorplate_records (
                row_hash,
                city,
                township,
                area_name,
                edit_date,
                change_date,
                old_address,
                new_address,
                edit_type,
                query_city,
                query_township,
                query_date_start,
                query_date_end,
                source_url,
                source_page,
                source_row_index,
                scraped_at
            ) VALUES (
                :row_hash,
                :city,
                :township,
                :area_name,
                :edit_date,
                :change_date,
                :old_address,
                :new_address,
                :edit_type,
                :query_city,
                :query_township,
                :query_date_start,
                :query_date_end,
                :source_url,
                :source_page,
                :source_row_index,
                :scraped_at
            )
            """,
            rows,
        )
        await db.commit()
        return int(db.total_changes - before_changes)


async def fetch_records(db_path: Path) -> list[dict[str, str]]:
    async with aiosqlite.connect(db_path) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM doorplate_records ORDER BY township, source_page, source_row_index"
        )
        rows = await cursor.fetchall()
        return [dict(row) for row in rows]
