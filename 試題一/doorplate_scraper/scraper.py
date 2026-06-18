from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from dataclasses import dataclass
from math import ceil
from pathlib import Path
from typing import Callable

from selenium import webdriver
from selenium.common.exceptions import StaleElementReferenceException, TimeoutException
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.by import By
from selenium.webdriver.remote.webdriver import WebDriver
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.support.ui import Select, WebDriverWait

from .config import CrawlerConfig, MAIN_URL, RESULT_PAGE_SIZE
from .models import DoorplateRecord, RocDate


CaptchaProvider = Callable[[WebDriver, str, int], str]


class CrawlerError(RuntimeError):
    pass


@dataclass(slots=True)
class QueryState:
    kind: str
    message: str = ""
    pages: int = 0
    page_index: int = 0
    total_records: int = 0


class DoorplateScraper:
    def __init__(
        self,
        config: CrawlerConfig,
        *,
        logger: logging.Logger,
        captcha_provider: CaptchaProvider | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        # 驗證碼來源優先序：
        # 1) 外部注入的 provider（例如自動辨識）。
        # 2) headless 模式：截圖存檔並開啟驗證碼圖，再由人工輸入。
        # 3) 有視窗模式：直接看瀏覽器畫面、人工輸入。
        if captcha_provider is not None:
            self.captcha_provider = captcha_provider
        elif config.headless:
            self.captcha_provider = self._prompt_captcha_with_image
        else:
            self.captcha_provider = self._prompt_captcha
        self.driver: WebDriver | None = None
        self.wait: WebDriverWait | None = None
        self._last_captcha_signature: str | None = None

    def __enter__(self) -> "DoorplateScraper":
        self.driver = self._build_driver()
        self.wait = WebDriverWait(self.driver, self.config.timeout_seconds)
        return self

    def __exit__(self, exc_type, exc, exc_tb) -> None:
        if self.driver is not None:
            self.driver.quit()
            self.driver = None
            self.wait = None

    def _build_driver(self) -> WebDriver:
        options = Options()
        if self.config.headless:
            options.add_argument("--headless=new")
        options.add_argument("--disable-gpu")
        options.add_argument("--window-size=1600,1400")
        options.add_argument("--lang=zh-TW")
        return webdriver.Chrome(options=options)

    def _required_driver(self) -> WebDriver:
        if self.driver is None:
            raise RuntimeError("Driver is not initialized. Use the scraper as a context manager.")
        return self.driver

    def _required_wait(self) -> WebDriverWait:
        if self.wait is None:
            raise RuntimeError("WebDriverWait is not initialized.")
        return self.wait

    def open_query_page(self) -> None:
        driver = self._required_driver()
        wait = self._required_wait()
        driver.get(MAIN_URL)
        self.logger.info("Opened main page: %s", MAIN_URL)
        driver.execute_script(
            """
            const form = document.querySelector('form#command');
            const input = document.createElement('input');
            input.type = 'hidden';
            input.name = 'searchType';
            input.value = 'village';
            form.appendChild(input);
            form.submit();
            """
        )
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form#mapForm")))
        driver.execute_script(
            """
            document.querySelector('#cityCode').value = arguments[0];
            document.querySelector('#mapForm').submit();
            """,
            self.config.city_code,
        )
        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "form#mainForm")))
        self.logger.info("Entered query page for city %s", self.config.city_name)

    def list_available_areas(self) -> list[tuple[str, str]]:
        driver = self._required_driver()
        select = Select(driver.find_element(By.ID, "areaCode"))
        areas: list[tuple[str, str]] = []
        for option in select.options:
            code = option.get_attribute("value")
            label = option.text.strip()
            if code and code != "0" and label:
                areas.append((code, label))
        return areas

    def query_area(
        self,
        *,
        area_code: str,
        area_name: str,
        start_date: RocDate,
        end_date: RocDate,
        register_kind: str,
        max_captcha_attempts: int = 5,
    ) -> list[DoorplateRecord]:
        driver = self._required_driver()
        self._fill_query_form(
            area_code=area_code,
            start_date=start_date,
            end_date=end_date,
            register_kind=register_kind,
        )

        for attempt in range(1, max_captcha_attempts + 1):
            previous_signature = None if attempt == 1 else self._last_captcha_signature
            captcha_signature = self._wait_for_captcha_ready(previous_signature=previous_signature)
            self._last_captcha_signature = captcha_signature
            captcha_input = driver.find_element(By.ID, "captchaInput_captchaKey")
            captcha_text = self.captcha_provider(driver, area_name, attempt)
            if self._current_captcha_signature() != captcha_signature:
                self.logger.warning(
                    "Captcha changed before submit for %s. Prompting again.",
                    area_name,
                )
                continue
            captcha_input.clear()
            captcha_input.send_keys(captcha_text.strip())
            driver.execute_script(
                """
                arguments[0].dispatchEvent(new Event('input', { bubbles: true }));
                arguments[0].dispatchEvent(new Event('change', { bubbles: true }));
                """,
                captcha_input,
            )
            time.sleep(0.2)

            driver.find_element(By.ID, "main-query").click()
            state = self._wait_for_query_outcome()
            if state.kind == "captcha_error":
                self.logger.warning("Captcha validation failed for %s, retrying", area_name)
                self._dismiss_modal()
                self._last_captcha_signature = self._wait_for_captcha_ready(
                    previous_signature=captcha_signature
                )
                continue
            if state.kind == "no_data":
                self.logger.info("No data for %s in %s to %s", area_name, start_date, end_date)
                self._dismiss_modal()
                return []
            if state.kind == "success":
                self.logger.info("Query succeeded for %s with %s records", area_name, state.total_records)
                return self._collect_paginated_records(
                    city=self.config.city_name,
                    township=area_name,
                    start_date=start_date,
                    end_date=end_date,
                    expected_total_records=state.total_records,
                    expected_pages=state.pages,
                )
            if state.kind == "query_error":
                self._dismiss_modal()
                raise CrawlerError(state.message or f"Query failed for {area_name}")

        raise CrawlerError(f"Captcha failed too many times for {area_name}")

    def _fill_query_form(
        self,
        *,
        area_code: str,
        start_date: RocDate,
        end_date: RocDate,
        register_kind: str,
    ) -> None:
        driver = self._required_driver()
        Select(driver.find_element(By.ID, "areaCode")).select_by_value(area_code)
        Select(driver.find_element(By.ID, "registerKind")).select_by_value(register_kind)

        include_no_date = driver.find_element(By.ID, "noDate")
        if include_no_date.is_selected():
            include_no_date.click()

        self._pick_date("sDate", start_date)
        self._pick_date("eDate", end_date)

    def _pick_date(self, element_id: str, value: RocDate) -> None:
        driver = self._required_driver()
        wait = self._required_wait()
        driver.find_element(By.ID, element_id).click()
        wait.until(EC.visibility_of_element_located((By.ID, "ui-datepicker-div")))

        # jQuery UI datepicker rebuilds its DOM after selecting a year or month,
        # so each interaction re-locates the select to avoid stale references.
        self._select_datepicker_value(
            "select.ui-datepicker-year",
            str(value.to_gregorian_year()),
        )
        self._select_datepicker_value(
            "select.ui-datepicker-month",
            str(value.month_zero_based()),
        )

        day_xpath = (
            "//div[@id='ui-datepicker-div' and not(contains(@style, 'display: none'))]"
            "//table[contains(@class, 'ui-datepicker-calendar')]"
            f"//a[normalize-space()='{value.day}']"
        )
        wait.until(EC.element_to_be_clickable((By.XPATH, day_xpath))).click()
        wait.until(
            lambda drv: drv.find_element(By.ID, element_id).get_attribute("value") == value.to_input_value()
        )

    def _select_datepicker_value(self, css_selector: str, value: str) -> None:
        wait = self._required_wait()
        for _ in range(3):
            try:
                element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
                Select(element).select_by_value(value)
                return
            except StaleElementReferenceException:
                time.sleep(0.2)
        element = wait.until(EC.element_to_be_clickable((By.CSS_SELECTOR, css_selector)))
        Select(element).select_by_value(value)

    def _wait_for_query_outcome(self) -> QueryState:
        wait = self._required_wait()

        def probe(driver: WebDriver) -> QueryState | None:
            state = self._probe_state(driver)
            if state.kind in {"captcha_error", "no_data", "query_error", "success"}:
                return state
            return None

        try:
            return wait.until(probe)
        except TimeoutException as exc:
            raise CrawlerError("Timed out while waiting for query results") from exc

    def _probe_state(self, driver: WebDriver) -> QueryState:
        payload = driver.execute_script(
            """
            const popup = document.querySelector('.swal2-popup');
            if (popup && popup.offsetParent !== null) {
              return {
                kind: 'modal',
                message: popup.innerText || '',
              };
            }

            const hasTable = window.jQuery
              && jQuery.fn.dataTable
              && jQuery.fn.dataTable.isDataTable('#view-datatable');

            if (hasTable) {
              const table = jQuery('#view-datatable').DataTable();
              const info = table.page.info();
              return {
                kind: 'table',
                totalRecords: info.recordsDisplay ?? info.recordsTotal ?? 0,
                pages: info.pages ?? 0,
                pageIndex: info.page ?? 0,
              };
            }

            return { kind: 'pending' };
            """
        )

        kind = payload.get("kind", "pending")
        if kind == "modal":
            message = str(payload.get("message", "")).strip()
            if "圖形驗證碼驗證失敗" in message:
                return QueryState(kind="captcha_error", message=message)
            if "查無資料" in message:
                return QueryState(kind="no_data", message=message)
            return QueryState(kind="query_error", message=message)
        if kind == "table" and int(payload.get("totalRecords", 0)) > 0:
            return QueryState(
                kind="success",
                message="ok",
                pages=int(payload.get("pages", 0)),
                page_index=int(payload.get("pageIndex", 0)),
                total_records=int(payload.get("totalRecords", 0)),
            )
        return QueryState(kind="pending")

    def _dismiss_modal(self) -> None:
        driver = self._required_driver()
        try:
            button = driver.find_element(
                By.CSS_SELECTOR,
                ".swal2-confirm, .confirm, button.swal2-confirm",
            )
            button.click()
        except Exception:
            driver.execute_script(
                """
                const btn = document.querySelector('.swal2-confirm, .confirm, button.swal2-confirm');
                if (btn) { btn.click(); }
                """
            )
        time.sleep(0.5)

    def _wait_for_captcha_ready(self, previous_signature: str | None) -> str:
        wait = self._required_wait()

        def ready(driver: WebDriver) -> str | None:
            signature = self._current_captcha_signature()
            if not signature:
                return None
            if previous_signature and signature == previous_signature:
                return None
            loaded = driver.execute_script(
                """
                const img = document.querySelector('#captchaImage_captchaKey');
                return !!img && !!img.complete && img.naturalWidth > 0;
                """
            )
            return signature if loaded else None

        return wait.until(ready)

    def _current_captcha_signature(self) -> str:
        driver = self._required_driver()
        payload = driver.execute_script(
            """
            const img = document.querySelector('#captchaImage_captchaKey');
            const key = document.querySelector('#captchaKey_captchaKey');
            if (!img || !key) {
              return null;
            }
            return `${key.value}|${img.getAttribute('src') || ''}`;
            """
        )
        return str(payload or "")

    def _collect_paginated_records(
        self,
        *,
        city: str,
        township: str,
        start_date: RocDate,
        end_date: RocDate,
        expected_total_records: int,
        expected_pages: int,
    ) -> list[DoorplateRecord]:
        driver = self._required_driver()
        wait = self._required_wait()
        self._wait_for_result_table_ready()
        records: list[DoorplateRecord] = []
        page_number = 1
        # 每頁筆數固定為網站預設值（不調整，見 config.RESULT_PAGE_SIZE 註解），
        # 僅以前端「下一頁」翻頁擷取。
        expected_page_count = expected_pages or ceil(expected_total_records / RESULT_PAGE_SIZE)

        while True:
            wait.until(lambda drv: len(self._datatable_rows()) > 0)
            page_rows = self._datatable_rows()
            self.logger.info(
                "Collecting page %s: rows=%s",
                page_number,
                len(page_rows),
            )
            for row_index, row in enumerate(page_rows, start=1):
                records.append(
                    DoorplateRecord.from_datatable_row(
                        row,
                        city=city,
                        township=township,
                        query_start=start_date,
                        query_end=end_date,
                        source_url=driver.current_url,
                        source_page=page_number,
                        source_row_index=row_index,
                    )
                )

            info = self._stable_datatable_page_info(
                expected_total_records=expected_total_records,
                fallback_page=page_number - 1,
                fallback_pages=expected_page_count,
                fallback_length=RESULT_PAGE_SIZE,
            )
            self.logger.info(
                "DataTable page info: page=%s pages=%s length=%s total=%s",
                info["page"] + 1,
                info["pages"],
                info["length"],
                info["total"],
            )
            if len(records) >= expected_total_records:
                break

            if page_number >= expected_page_count:
                break

            first_row_signature = self._row_signature(page_rows[0]) if page_rows else ""
            driver.execute_script(
                """
                const table = jQuery('#view-datatable').DataTable();
                table.page('next').draw('page');
                """
            )
            # 以「首列內容改變」作為翻頁完成、資料已重繪的訊號；
            # DataTable 的頁碼指標會在 rows().data() 實際更新前就跳號，不能用來判斷。
            wait.until(
                lambda drv: self._has_page_advanced(
                    previous_first_row_signature=first_row_signature,
                )
            )
            page_number += 1

        if len(records) != expected_total_records:
            # 筆數與網站宣告總數不符，通常代表翻頁未抓齊，需留意 Log。
            self.logger.warning(
                "Collected %s records but site reported %s for %s",
                len(records),
                expected_total_records,
                township,
            )
        if len(records) > expected_total_records:
            records = records[:expected_total_records]
        return records

    def _wait_for_result_table_ready(self) -> None:
        wait = self._required_wait()

        def ready(driver: WebDriver) -> bool:
            rows = driver.find_elements(By.CSS_SELECTOR, "#view-datatable tbody tr")
            if not rows:
                return False
            return not self._is_processing()

        wait.until(ready)

    def _stable_datatable_page_info(
        self,
        *,
        expected_total_records: int,
        fallback_page: int,
        fallback_pages: int,
        fallback_length: int,
    ) -> dict[str, int]:
        for _ in range(5):
            info = self._datatable_page_info()
            if info["total"] > 0 and info["pages"] > 0:
                return info
            time.sleep(0.2)
        return {
            "page": fallback_page,
            "pages": fallback_pages,
            "length": fallback_length,
            "total": expected_total_records,
        }

    def _has_page_advanced(
        self,
        *,
        previous_first_row_signature: str,
    ) -> bool:
        if self._is_processing():
            return False

        rows = self._datatable_rows()
        if not rows:
            return False

        current_first_row_signature = self._row_signature(rows[0])
        return current_first_row_signature != previous_first_row_signature

    def _is_processing(self) -> bool:
        driver = self._required_driver()
        return bool(
            driver.execute_script(
                """
                const processing = document.querySelector('#view-datatable_processing');
                if (!processing) {
                  return false;
                }
                const style = window.getComputedStyle(processing);
                return style.display !== 'none' && style.visibility !== 'hidden';
                """
            )
        )

    @staticmethod
    def _row_signature(row: dict[str, str]) -> str:
        return "|".join(str(row.get(key, "")) for key in ("v1", "v2", "v3", "v4", "v5", "v6"))

    def _datatable_rows(self) -> list[dict[str, str]]:
        driver = self._required_driver()
        return driver.execute_script(
            """
            const table = jQuery('#view-datatable').DataTable();
            return table.rows({ page: 'current' }).data().toArray();
            """
        )

    def _datatable_page_info(self) -> dict[str, int]:
        driver = self._required_driver()
        return driver.execute_script(
            """
            const table = jQuery('#view-datatable').DataTable();
            const info = table.page.info();
            return {
              page: info.page ?? 0,
              pages: info.pages ?? 0,
              length: info.length ?? 0,
              total: info.recordsDisplay ?? info.recordsTotal ?? 0,
            };
            """
        )

    @staticmethod
    def _prompt_captcha(driver: WebDriver, area_name: str, attempt: int) -> str:
        _ = driver
        return input(
            f"[{area_name}] 第 {attempt} 次請輸入畫面上的圖形驗證碼："
        ).strip()

    def _prompt_captcha_with_image(self, driver: WebDriver, area_name: str, attempt: int) -> str:
        # headless 下沒有可見瀏覽器，改將驗證碼元素截圖存檔並以系統預設程式開啟，
        # 由使用者看圖後手動輸入。
        path = self._save_captcha_image(driver, area_name, attempt)
        if path is not None:
            self._open_file(path)
            self.logger.info("Captcha image saved to %s", path)
            prompt = f"[{area_name}] 第 {attempt} 次，請查看已開啟的驗證碼圖檔 {path} 後輸入："
        else:
            prompt = f"[{area_name}] 第 {attempt} 次請輸入圖形驗證碼（圖檔擷取失敗）："
        return input(prompt).strip()

    def _save_captcha_image(self, driver: WebDriver, area_name: str, attempt: int) -> Path | None:
        try:
            element = driver.find_element(By.ID, "captchaImage_captchaKey")
            captcha_dir = self.config.log_path.parent / "captcha"
            captcha_dir.mkdir(parents=True, exist_ok=True)
            path = captcha_dir / f"captcha_{area_name}_attempt{attempt}.png"
            element.screenshot(str(path))
            return path
        except Exception:
            self.logger.exception("Failed to capture captcha image for %s", area_name)
            return None

    @staticmethod
    def _open_file(path: Path) -> None:
        # 開檔失敗不致命：使用者仍可手動開啟印出的路徑。
        try:
            if sys.platform.startswith("win"):
                os.startfile(str(path))  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(path)])
            else:
                subprocess.Popen(["xdg-open", str(path)])
        except Exception:
            pass
