from __future__ import annotations

from pathlib import Path
from typing import Iterable

import pandas as pd

from .models import DoorplateRecord
from .utils import ensure_parent


CSV_COLUMNS = [
    "city",
    "township",
    "area_name",
    "edit_date",
    "change_date",
    "old_address",
    "new_address",
    "edit_type",
    "query_city",
    "query_township",
    "query_date_start",
    "query_date_end",
    "source_url",
    "source_page",
    "source_row_index",
    "scraped_at",
    "row_hash",
]


def records_to_dataframe(records: Iterable[DoorplateRecord]) -> pd.DataFrame:
    rows = [record.as_dict() for record in records]
    return pd.DataFrame(rows, columns=CSV_COLUMNS)


def export_records_to_csv(records: Iterable[DoorplateRecord], csv_path: Path) -> Path:
    ensure_parent(csv_path)
    frame = records_to_dataframe(records)
    frame.to_csv(csv_path, index=False, encoding="utf-8-sig")
    return csv_path
