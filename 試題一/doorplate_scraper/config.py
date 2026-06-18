from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

from .models import RocDate

MAIN_URL = "https://www.ris.gov.tw/info-doorplate/app/doorplate/main"
MAP_URL = "https://www.ris.gov.tw/info-doorplate/app/doorplate/map"
QUERY_URL = "https://www.ris.gov.tw/info-doorplate/app/doorplate/query"
AJAX_QUERY_URL = "https://www.ris.gov.tw/info-doorplate/app/doorplate/inquiry/village"

TAIPEI_CITY_CODE = "63000000"
CITY_NAME_TO_CODE = {
    "臺北市": "63000000",
    "台北市": "63000000",
    "新北市": "65000000",
    "桃園市": "68000000",
    "臺中市": "66000000",
    "台中市": "66000000",
    "臺南市": "67000000",
    "台南市": "67000000",
    "高雄市": "64000000",
    "基隆市": "10017000",
    "新竹市": "10018000",
    "嘉義市": "10020000",
    "新竹縣": "10004000",
    "苗栗縣": "10005000",
    "彰化縣": "10007000",
    "南投縣": "10008000",
    "雲林縣": "10009000",
    "嘉義縣": "10010000",
    "屏東縣": "10013000",
    "宜蘭縣": "10002000",
    "花蓮縣": "10015000",
    "臺東縣": "10014000",
    "台東縣": "10014000",
    "澎湖縣": "10016000",
    "金門縣": "09020000",
    "連江縣": "09007000",
}
REGISTER_KIND_INITIAL = "1"
# 結果表格固定每頁 50 筆（網站預設值），僅用於估算頁數。
# 不另外調整每頁筆數：此站「改每頁筆數」會重新向伺服器查詢並重送已被
# 消耗的單次驗證碼，造成「圖形驗證碼驗證失敗」；改以前端翻頁擷取最穩定。
RESULT_PAGE_SIZE = 50


@dataclass(slots=True, frozen=True)
class QuerySpec:
    area_code: str
    area_name: str
    start_date: RocDate
    end_date: RocDate
    register_kind: str = REGISTER_KIND_INITIAL


@dataclass(slots=True)
class CrawlerConfig:
    city_name: str = "臺北市"
    city_code: str = TAIPEI_CITY_CODE
    start_date: RocDate = field(default_factory=lambda: RocDate.parse("114/09/01"))
    end_date: RocDate = field(default_factory=lambda: RocDate.parse("114/11/30"))
    register_kind: str = REGISTER_KIND_INITIAL
    headless: bool = False
    timeout_seconds: int = 20
    db_path: Path = field(default_factory=lambda: Path("data") / "doorplate.sqlite3")
    csv_path: Path = field(default_factory=lambda: Path("data") / "doorplate_records.csv")
    log_path: Path = field(default_factory=lambda: Path("logs") / "crawler.log")
    areas: list[str] = field(default_factory=list)

    def with_area_names(self, areas: Iterable[str]) -> None:
        self.areas = [area.strip() for area in areas if area.strip()]

    def set_city(self, city_name: str) -> None:
        normalized_name = city_name.strip()
        try:
            city_code = CITY_NAME_TO_CODE[normalized_name]
        except KeyError as exc:
            supported = ", ".join(sorted(set(CITY_NAME_TO_CODE)))
            raise ValueError(f"Unsupported city name: {city_name}. Supported cities: {supported}") from exc
        self.city_name = normalized_name
        self.city_code = city_code
