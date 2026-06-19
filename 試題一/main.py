from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from doorplate_scraper.config import CrawlerConfig
from doorplate_scraper.exporters import export_records_to_csv
from doorplate_scraper.models import RocDate
from doorplate_scraper.scraper import DoorplateScraper
from doorplate_scraper.storage import init_db, insert_records
from doorplate_scraper.utils import configure_logger


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="試題一：村里街路門牌異動查詢爬蟲")
    parser.add_argument("--headless", action="store_true", help="以 headless 模式啟動瀏覽器")
    parser.add_argument(
        "--captcha",
        choices=["manual", "auto"],
        default="manual",
        help="驗證碼處理：manual=人工輸入（預設）；auto=ddddocr 自動辨識，失敗自動降級人工"
        "（需 pip install ddddocr opencv-python）",
    )
    parser.add_argument("--city", default="臺北市", help="查詢縣市名稱，例如 臺北市、台中市")
    parser.add_argument("--start-date", default="114/09/01", help="民國日期起，例如 114/09/01")
    parser.add_argument("--end-date", default="114/11/30", help="民國日期迄，例如 114/11/30")
    parser.add_argument("--register-kind", default="1", help="編訂類別代碼，1 代表門牌初編")
    parser.add_argument(
        "--areas",
        default="",
        help="指定行政區名稱，以逗號分隔，例如 大安區,信義區。留空則查詢台北市全部行政區。",
    )
    parser.add_argument(
        "--captcha-variants",
        type=int,
        default=1,
        help="auto OCR preprocessing variants; 1 keeps the original single Otsu path",
    )
    parser.add_argument("--db-path", default=str(Path("data") / "doorplate.sqlite3"))
    parser.add_argument("--csv-path", default=str(Path("data") / "doorplate_records.csv"))
    parser.add_argument("--log-path", default=str(Path("logs") / "crawler.log"))
    return parser


async def run() -> int:
    args = build_parser().parse_args()
    if args.captcha_variants < 1:
        raise ValueError("--captcha-variants must be >= 1")
    config = CrawlerConfig(
        headless=args.headless,
        start_date=RocDate.parse(args.start_date),
        end_date=RocDate.parse(args.end_date),
        register_kind=args.register_kind,
        captcha_mode=args.captcha,
        captcha_variant_count=args.captcha_variants,
        db_path=Path(args.db_path),
        csv_path=Path(args.csv_path),
        log_path=Path(args.log_path),
    )
    config.set_city(args.city)
    if args.areas:
        config.with_area_names(args.areas.split(","))

    logger = configure_logger(config.log_path)
    await init_db(config.db_path)

    all_records = []
    failed_areas: list[str] = []
    selected_areas: list[tuple[str, str]] = []
    total_inserted = 0
    try:
        with DoorplateScraper(config, logger=logger) as scraper:
            scraper.open_query_page()
            available_areas = scraper.list_available_areas()
            selected_areas = [
                area for area in available_areas if not config.areas or area[1] in config.areas
            ]

            if not selected_areas:
                logger.warning("No areas matched the input filters: %s", config.areas)
                return 1

            for area_code, area_name in selected_areas:
                logger.info("Start crawling %s", area_name)
                try:
                    # 每區都重新進到乾淨的查詢頁，避免上一區殘留的彈窗（swal2）或頁面狀態
                    # 擋住後續操作而連環失敗。
                    scraper.open_query_page()
                    records = scraper.query_area(
                        area_code=area_code,
                        area_name=area_name,
                        start_date=config.start_date,
                        end_date=config.end_date,
                        register_kind=config.register_kind,
                    )
                except Exception:  # noqa: BLE001 - 隔離單區失敗，避免拖垮其他行政區
                    logger.exception("Failed to crawl %s; skipping to next area", area_name)
                    failed_areas.append(area_name)
                    continue

                logger.info("Finished %s with %s records", area_name, len(records))
                all_records.extend(records)
                # 每區爬完即時落庫（INSERT OR IGNORE 冪等），
                # 確保後續區域失敗時，已完成的資料不會遺失。
                inserted = await insert_records(config.db_path, records)
                total_inserted += inserted
                logger.info(
                    "Persisted %s: records=%s inserted=%s", area_name, len(records), inserted
                )
    except Exception:
        # 非預期中斷時，仍把已蒐集到的資料輸出為 CSV 作為證據後再往外拋。
        logger.exception("Crawler aborted unexpectedly")
        if all_records:
            export_records_to_csv(all_records, config.csv_path)
        raise

    export_records_to_csv(all_records, config.csv_path)
    logger.info(
        "Crawl complete. areas=%s failed=%s total_records=%s inserted=%s csv=%s db=%s",
        len(selected_areas),
        len(failed_areas),
        len(all_records),
        total_inserted,
        config.csv_path,
        config.db_path,
    )
    if failed_areas:
        logger.warning("Areas that failed: %s", ", ".join(failed_areas))
        # 全部選定區域都失敗才視為整體失敗；部分成功仍回傳 0 並保留已落地資料。
        if len(failed_areas) == len(selected_areas):
            return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
