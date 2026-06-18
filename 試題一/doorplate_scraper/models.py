from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from hashlib import sha256
import re
from typing import Any

REGISTER_KIND_MAPPING = {
    "0": "資料維護",
    "1": "門牌初編",
    "2": "門牌改編",
    "3": "門牌增編",
    "4": "門牌合併",
    "5": "門牌廢止",
    "6": "行政區域調整",
    "7": "門牌整編",
    "F": "行政區域調整錯誤更正",
    "G": "門牌整編錯誤更正",
    "8": "戶政事務合併",
}


@dataclass(slots=True, frozen=True)
class RocDate:
    year: int
    month: int
    day: int

    @classmethod
    def parse(cls, value: str) -> "RocDate":
        matched = re.fullmatch(r"\s*(\d{2,3})[-/](\d{1,2})[-/](\d{1,2})\s*", value)
        if not matched:
            raise ValueError(f"Unsupported ROC date format: {value!r}")
        year, month, day = (int(group) for group in matched.groups())
        return cls(year=year, month=month, day=day)

    def to_input_value(self) -> str:
        return f"{self.year:03d}-{self.month:02d}-{self.day:02d}"

    def to_query_label(self) -> str:
        return f"民國{self.year}年{self.month}月{self.day}日"

    def to_gregorian_year(self) -> int:
        return self.year + 1911

    def month_zero_based(self) -> int:
        return self.month - 1

    def as_iso(self) -> str:
        gregorian_year = self.to_gregorian_year()
        return f"{gregorian_year:04d}-{self.month:02d}-{self.day:02d}"


@dataclass(slots=True)
class DoorplateRecord:
    city: str
    township: str
    area_name: str
    edit_date: str
    change_date: str
    old_address: str
    new_address: str
    edit_type: str
    query_city: str
    query_township: str
    query_date_start: str
    query_date_end: str
    source_url: str
    source_page: int
    source_row_index: int
    scraped_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    row_hash: str = ""

    def __post_init__(self) -> None:
        if not self.row_hash:
            payload = "|".join(
                [
                    self.city,
                    self.township,
                    self.area_name,
                    self.edit_date,
                    self.change_date,
                    self.old_address,
                    self.new_address,
                    self.edit_type,
                    self.query_date_start,
                    self.query_date_end,
                ]
            )
            self.row_hash = sha256(payload.encode("utf-8")).hexdigest()

    @classmethod
    def from_datatable_row(
        cls,
        row: dict[str, Any],
        *,
        city: str,
        township: str,
        query_start: RocDate,
        query_end: RocDate,
        source_url: str,
        source_page: int,
        source_row_index: int,
    ) -> "DoorplateRecord":
        edit_type_code = str(row.get("v6", "")).strip()
        edit_type = REGISTER_KIND_MAPPING.get(edit_type_code, edit_type_code)
        area_name = str(row.get("v1", "")).strip()
        return cls(
            city=city,
            township=township,
            area_name=area_name,
            edit_date=str(row.get("v2", "")).strip(),
            change_date=str(row.get("v3", "")).strip(),
            old_address=str(row.get("v4", "")).strip(),
            new_address=str(row.get("v5", "")).strip(),
            edit_type=edit_type,
            query_city=city,
            query_township=township,
            query_date_start=query_start.to_input_value(),
            query_date_end=query_end.to_input_value(),
            source_url=source_url,
            source_page=source_page,
            source_row_index=source_row_index,
        )

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)
