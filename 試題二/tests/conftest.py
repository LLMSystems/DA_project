from __future__ import annotations

import sqlite3
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

# 讓 `import app...` 可運作（把 試題二 專案根目錄加進 sys.path）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

# 與試題一一致的最小欄位（測試只需查詢用到的欄位）。
_CREATE_SQL = """
CREATE TABLE doorplate_records (
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
    query_date_start TEXT NOT NULL,
    query_date_end TEXT NOT NULL,
    scraped_at TEXT NOT NULL
)
"""

_SAMPLE_ROWS = [
    # row_hash, city(臺), township, area_name, edit_date, change_date,
    # old_address, new_address, edit_type, q_start, q_end, scraped_at
    ("h1", "臺北市", "大安區", "錦安里", "114/09/05", "114/09/10",
     "舊址A", "新址A", "門牌初編", "114/09/01", "114/11/30", "2026-06-18T00:00:00"),
    ("h2", "臺北市", "大安區", "龍泉里", "114/10/01", "114/10/03",
     "舊址B", "新址B", "門牌初編", "114/09/01", "114/11/30", "2026-06-18T00:00:00"),
]


def _make_db(path: Path) -> None:
    con = sqlite3.connect(path)
    con.execute(_CREATE_SQL)
    con.executemany(
        "INSERT INTO doorplate_records "
        "(row_hash, city, township, area_name, edit_date, change_date, old_address, "
        "new_address, edit_type, query_date_start, query_date_end, scraped_at) "
        "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
        _SAMPLE_ROWS,
    )
    con.commit()
    con.close()


@pytest.fixture
def client(tmp_path, monkeypatch) -> TestClient:
    db = tmp_path / "doorplate.sqlite3"
    _make_db(db)
    monkeypatch.setenv("DB_PATH", str(db))
    monkeypatch.setenv("LOG_DIR", str(tmp_path / "logs"))
    from app.main import app

    return TestClient(app)
