from pathlib import Path

import pytest

from doorplate_scraper.models import DoorplateRecord, RocDate
from doorplate_scraper.storage import fetch_records, init_db, insert_records


@pytest.mark.asyncio
async def test_insert_records_deduplicates_by_hash(tmp_path: Path) -> None:
    db_path = tmp_path / "test.sqlite3"
    record = DoorplateRecord.from_datatable_row(
        {
            "v1": "臺北市大安區",
            "v2": "民國114年9月1日",
            "v3": "民國114年9月1日",
            "v4": "",
            "v5": "大安路一段1號",
            "v6": "1",
        },
        city="臺北市",
        township="大安區",
        query_start=RocDate.parse("114/09/01"),
        query_end=RocDate.parse("114/11/30"),
        source_url="https://example.com",
        source_page=1,
        source_row_index=1,
    )

    await init_db(db_path)
    first_insert = await insert_records(db_path, [record])
    second_insert = await insert_records(db_path, [record])
    rows = await fetch_records(db_path)

    assert first_insert == 1
    assert second_insert == 0
    assert len(rows) == 1
    assert rows[0]["township"] == "大安區"
