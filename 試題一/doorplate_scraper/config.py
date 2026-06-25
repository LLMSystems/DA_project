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
    # 驗證碼模式：manual=人工輸入；auto=ddddocr 自動辨識（失敗自動降級人工）。
    captcha_mode: str = "manual"
    # 本站驗證碼固定 5 碼，自動辨識輸出非此長度時視為不可信、換一張重抽。
    captcha_length: int = 5
    # auto 模式先讓 OCR 嘗試的次數，用盡仍失敗才降級人工。
    # 每次都換一張新驗證碼、彼此獨立，單次成功率實測約 0.71，
    # 累積成功率 = 1 - (1 - 0.71)^n：n=3→97.6%、n=5→99.8%、n=6→99.9%。
    # 取 6 在「成功率」與「失敗時等待時間」間取平衡：到 6 次已 ~99.9%，
    # 再往上邊際效益 <0.1%，不如直接降級人工。
    auto_captcha_attempts: int = 6
    # 1 keeps the original single Otsu OCR path. Values >1 enable the advanced
    # multi-variant selector, capped by the built-in variant list.
    captcha_variant_count: int = 1
    # native keeps ddddocr's built-in greedy decoder. beam enables a slower
    # restricted CTC decoder tuned for 5-character A-Z/0-9 captchas.
    captcha_decoder: str = "native"
    db_path: Path = field(default_factory=lambda: Path("data") / "doorplate.sqlite3")
    csv_path: Path = field(default_factory=lambda: Path("data") / "doorplate_records.csv")
    log_path: Path = field(default_factory=lambda: Path("logs") / "crawler.log")
    areas: list[str] = field(default_factory=list)
    # === 反爬：請求節流與退避===
    # 每個行政區查詢送出前的隨機等待秒數範圍，模擬人為節奏、打散規律請求指紋。
    # 設為 0/0 可關閉（例如測試或想最快跑完時）。
    request_delay_min: float = 1.5
    request_delay_max: float = 4.0
    # 驗證碼被站方判定錯誤後的指數退避：min(base * 2**(n-1), backoff_max)，再加抖動。
    # 避免被暫時限流時還猛打，加速被封。base<=0 可關閉。
    retry_backoff_base: float = 1.0
    retry_backoff_max: float = 8.0
    # === 反爬：指紋遮蔽===
    # 移除 headless 的 UA 標記、遮蔽 navigator.webdriver 與自動化橫幅。
    stealth: bool = True
    # 自訂 User-Agent；留空則沿用實際瀏覽器 UA 並自動移除 Headless 標記。
    user_agent: str = ""

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
