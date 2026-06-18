"""驗證爬蟲輸出的小工具：比對 CSV 與 SQLite 是否一致、有無重複。

用法：
    python scripts/check_output.py --csv data/doorplate_records.csv --db data/doorplate.sqlite3

檢查項目：
1. CSV 筆數、依 row_hash 的唯一筆數、各行政區筆數。
2. DB 筆數、各行政區筆數。
3. 一致性：CSV 與 DB 的 row_hash 集合是否相符、是否有重複列。
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
from pathlib import Path

import pandas as pd


def _counts(series: pd.Series) -> str:
    return ", ".join(f"{name}={count}" for name, count in series.value_counts().items())


def main() -> int:
    parser = argparse.ArgumentParser(description="比對門牌爬蟲的 CSV 與 SQLite 輸出")
    parser.add_argument("--csv", required=True, type=Path)
    parser.add_argument("--db", required=True, type=Path)
    args = parser.parse_args()

    # Windows 主控台預設非 UTF-8，行政區中文會變亂碼，這裡統一切到 UTF-8 顯示。
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    if not args.csv.exists():
        print(f"[ERROR] CSV 不存在：{args.csv}")
        return 2
    if not args.db.exists():
        print(f"[ERROR] DB 不存在：{args.db}")
        return 2

    csv = pd.read_csv(args.csv)
    with sqlite3.connect(args.db) as con:
        db = pd.read_sql_query("SELECT * FROM doorplate_records", con)

    print(f"[CSV] {args.csv}")
    print(f"  rows           : {len(csv)}")
    print(f"  unique row_hash: {csv['row_hash'].nunique()}")
    print(f"  townships      : {_counts(csv['township'])}")

    print(f"[DB] {args.db}")
    print(f"  rows           : {len(db)}")
    print(f"  townships      : {_counts(db['township'])}")

    csv_hashes = set(csv["row_hash"])
    db_hashes = set(db["row_hash"])
    csv_dups = len(csv) - csv["row_hash"].nunique()

    print("[CONSISTENCY]")
    print(f"  duplicate rows in CSV : {csv_dups}")
    print(f"  in CSV but not in DB  : {len(csv_hashes - db_hashes)}")
    print(f"  in DB but not in CSV  : {len(db_hashes - csv_hashes)}")

    ok = (
        csv_dups == 0
        and csv_hashes == db_hashes
        and len(db) == len(db_hashes)
        and len(csv) == len(csv_hashes)
    )
    print(f"[RESULT] {'PASS' if ok else 'FAIL'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
