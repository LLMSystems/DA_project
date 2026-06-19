from __future__ import annotations

import os
from pathlib import Path

# 預設指向試題一的 SQLite；以環境變數 DB_PATH 覆蓋（容器化時掛共用 volume）。
# app/config.py -> app -> 試題二 -> DA_project -> 試題一/data/doorplate.sqlite3
_DEFAULT_DB = (
    Path(__file__).resolve().parent.parent.parent / "試題一" / "data" / "doorplate.sqlite3"
)


def get_db_path() -> Path:
    """API 讀取的 SQLite 路徑；唯讀查詢試題一爬蟲落地的資料。"""
    return Path(os.getenv("DB_PATH", str(_DEFAULT_DB)))


def city_variants(city: str) -> list[str]:
    """回傳縣市名的等價寫法（台↔臺），以涵蓋 DB 不同儲存形式。

    題目 Input 為「台北市」，但試題一 DB 可能存「臺北市」；查詢時兩種都比對，
    避免因「台/臺」差異而永遠查無資料。
    """
    name = city.strip()
    variants = {name, name.replace("台", "臺"), name.replace("臺", "台")}
    return [v for v in variants if v]
