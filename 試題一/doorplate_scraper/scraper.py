from __future__ import annotations

import base64
import logging
import math
import os
import subprocess
import sys
import time
from collections import defaultdict
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


@dataclass(slots=True)
class CaptchaCandidate:
    variant_name: str
    text: str
    confidence: float


class DoorplateScraper:
    _CAPTCHA_ALLOWED_CHARS = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ"
    _CAPTCHA_BEAM_SIZE = 10
    _CAPTCHA_BEAM_TOP_CHARS = 8

    def __init__(
        self,
        config: CrawlerConfig,
        *,
        logger: logging.Logger,
        captcha_provider: CaptchaProvider | None = None,
    ) -> None:
        self.config = config
        self.logger = logger
        # 人工輸入 provider：headless 截圖存檔開圖，有視窗則直接看畫面。
        self._manual_provider: CaptchaProvider = (
            self._prompt_captcha_with_image if config.headless else self._prompt_captcha
        )
        # 驗證碼模式：
        # - custom：外部注入 provider（例如自訂辨識），完全交給它，不做降級。
        # - auto  ：先用 ddddocr 自動辨識，連續失敗自動降級人工。
        # - manual：人工輸入。
        if captcha_provider is not None:
            self._captcha_mode = "custom"
            self.captcha_provider = captcha_provider
        elif config.captcha_mode == "auto":
            self._captcha_mode = "auto"
            self.captcha_provider = self._manual_provider  # 降級時使用
        else:
            self._captcha_mode = "manual"
            self.captcha_provider = self._manual_provider
        self._ocr = None  # ddddocr 實例，auto 模式首次使用時才載入
        self.driver: WebDriver | None = None
        self.wait: WebDriverWait | None = None
        self._last_captcha_signature: str | None = None
        self._captcha_charset_indices: dict[str, list[int]] | None = None

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
        # 容器內必要旗標：關閉 sandbox、避免 /dev/shm 過小導致 Chrome 崩潰。
        options.add_argument("--no-sandbox")
        options.add_argument("--disable-dev-shm-usage")
        options.add_argument("--window-size=1600,1400")
        options.add_argument("--lang=zh-TW")
        # 容器化時以環境變數指定 chromium / chromedriver 路徑（本機不設則維持原行為，
        # 由 Selenium Manager 自動處理）。
        chrome_binary = os.getenv("CHROME_BINARY")
        if chrome_binary:
            options.binary_location = chrome_binary
        chromedriver = os.getenv("CHROMEDRIVER")
        if chromedriver:
            from selenium.webdriver.chrome.service import Service

            return webdriver.Chrome(options=options, service=Service(executable_path=chromedriver))
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

        # auto 模式：先給 OCR 一定次數嘗試，用盡仍失敗才降級人工。
        auto_budget = self.config.auto_captcha_attempts if self._captcha_mode == "auto" else 0
        total_attempts = auto_budget + max_captcha_attempts

        for attempt in range(1, total_attempts + 1):
            use_ocr = self._captcha_mode == "auto" and attempt <= auto_budget
            if self._captcha_mode == "auto" and attempt == auto_budget + 1:
                self.logger.warning(
                    "OCR 連續 %s 次未過，%s 降級為人工輸入驗證碼", auto_budget, area_name
                )

            # 每輪只需等驗證碼「已載入」即可取得當前 signature。
            # 「等待換新」已在 captcha_error 與 5 碼閘門分支各自處理；此處若再要求
            # 與前一張不同，剛換好的新驗證碼會被誤判為「沒變」而 timeout。
            captcha_signature = self._wait_for_captcha_ready(previous_signature=None)
            self._last_captcha_signature = captcha_signature
            captcha_input = driver.find_element(By.ID, "captchaInput_captchaKey")

            provider = self._ocr_captcha if use_ocr else self.captcha_provider
            captcha_text = provider(driver, area_name, attempt)

            # 5 碼閘門（僅對 OCR）：輸出非預期長度視為不可信，
            # 直接點「產製新驗證碼」換一張重抽，不送出、不浪費伺服器請求。
            if use_ocr and len(captcha_text) != self.config.captcha_length:
                self.logger.info(
                    "OCR 結果 %r 非 %s 碼，%s 換新驗證碼重試",
                    captcha_text,
                    self.config.captcha_length,
                    area_name,
                )
                self._last_captcha_signature = self._refresh_captcha(
                    previous_signature=captcha_signature
                )
                continue

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

    def _refresh_captcha(self, *, previous_signature: str | None) -> str:
        """點「產製新驗證碼」換一張，回傳新的 signature（確認確實換新）。"""
        driver = self._required_driver()
        driver.execute_script(
            """
            const els = document.querySelectorAll('button, a, input[type="button"]');
            for (const el of els) {
              const text = (el.innerText || el.value || '').trim();
              if (text.includes('產製新驗證碼')) { el.click(); return; }
            }
            const img = document.querySelector('#captchaImage_captchaKey');
            if (img) { img.click(); }
            """
        )
        return self._wait_for_captcha_ready(previous_signature=previous_signature)

    def _grab_captcha_png(self) -> bytes:
        """以原始解析度取得驗證碼圖的 PNG bytes（canvas 抽圖，避免版面截圖被裁切）。"""
        driver = self._required_driver()
        data_b64 = driver.execute_script(
            """
            const img = document.querySelector('#captchaImage_captchaKey');
            if (!img) { return null; }
            const src = img.getAttribute('src') || '';
            if (src.startsWith('data:')) { return src.split(',')[1]; }
            const canvas = document.createElement('canvas');
            canvas.width = img.naturalWidth;
            canvas.height = img.naturalHeight;
            canvas.getContext('2d').drawImage(img, 0, 0);
            return canvas.toDataURL('image/png').split(',')[1];
            """
        )
        if not data_b64:
            raise CrawlerError("無法取得驗證碼圖片內容")
        return base64.b64decode(data_b64)

    @staticmethod
    def _otsu_png(png_bytes: bytes) -> bytes:
        """灰階 + Otsu 二值化（實測對本站驗證碼辨識率提升最顯著的前處理）。"""
        import cv2  # 延後載入：僅 auto 模式需要 opencv
        import numpy as np

        gray = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_GRAYSCALE)
        _, binary = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        ok, buf = cv2.imencode(".png", binary)
        if not ok:
            raise CrawlerError("Otsu 前處理後 PNG 編碼失敗")
        return buf.tobytes()

    @staticmethod
    def _encode_png_array(image) -> bytes:
        import cv2

        ok, buf = cv2.imencode(".png", image)
        if not ok:
            raise CrawlerError("Captcha variant PNG encoding failed")
        return buf.tobytes()

    @staticmethod
    def _captcha_variant_pngs(png_bytes: bytes, requested_count: int) -> list[tuple[str, bytes]]:
        import cv2
        import numpy as np

        source = cv2.imdecode(np.frombuffer(png_bytes, np.uint8), cv2.IMREAD_COLOR)
        if source is None:
            raise CrawlerError("Captcha PNG decode failed")

        gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
        rect2 = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
        cross2 = cv2.getStructuringElement(cv2.MORPH_CROSS, (2, 2))
        rect3 = cv2.getStructuringElement(cv2.MORPH_RECT, (3, 3))

        def threshold(resized):
            return cv2.threshold(resized, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)[1]

        def scaled_otsu(scale: float, interpolation: int):
            resized = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=interpolation)
            return threshold(resized)

        def erode(scale: float, kernel):
            return cv2.erode(scaled_otsu(scale, cv2.INTER_CUBIC), kernel, iterations=1)

        def open_morph(scale: float, kernel):
            return cv2.morphologyEx(scaled_otsu(scale, cv2.INTER_CUBIC), cv2.MORPH_OPEN, kernel)

        def close_morph(scale: float, kernel):
            return cv2.morphologyEx(scaled_otsu(scale, cv2.INTER_CUBIC), cv2.MORPH_CLOSE, kernel)

        def padded(scale: float, pad: int):
            image = scaled_otsu(scale, cv2.INTER_CUBIC)
            return cv2.copyMakeBorder(image, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)

        variants = [
            ("otsu", lambda: scaled_otsu(1, cv2.INTER_CUBIC)),
            ("s4_nearest_otsu", lambda: scaled_otsu(4, cv2.INTER_NEAREST)),
            ("s4_area_otsu", lambda: scaled_otsu(4, cv2.INTER_AREA)),
            ("s3_nearest_otsu", lambda: scaled_otsu(3, cv2.INTER_NEAREST)),
            ("s3_area_otsu", lambda: scaled_otsu(3, cv2.INTER_AREA)),
            ("s25_erode_rect2", lambda: erode(2.5, rect2)),
            ("s2_erode_rect2", lambda: erode(2, rect2)),
            ("s2_erode_cross2", lambda: erode(2, cross2)),
            ("s2_open_rect2", lambda: open_morph(2, rect2)),
            ("s25_nearest_otsu", lambda: scaled_otsu(2.5, cv2.INTER_NEAREST)),
            ("s2_cubic_otsu", lambda: scaled_otsu(2, cv2.INTER_CUBIC)),
            ("s3_erode_cross2", lambda: erode(3, cross2)),
            ("s3_pad2", lambda: padded(3, 2)),
            ("s125_nearest_otsu", lambda: scaled_otsu(1.25, cv2.INTER_NEAREST)),
            ("s25_close_rect2", lambda: close_morph(2.5, rect2)),
            ("s2_pad2", lambda: padded(2, 2)),
            ("s25_erode_rect3", lambda: erode(2.5, rect3)),
            ("s25_open_rect2", lambda: open_morph(2.5, rect2)),
        ]
        selected = variants[: min(max(1, requested_count), len(variants))]
        return [(name, DoorplateScraper._encode_png_array(fn())) for name, fn in selected]

    @staticmethod
    def _logadd(left: float, right: float) -> float:
        if left == -math.inf:
            return right
        if right == -math.inf:
            return left
        if left < right:
            left, right = right, left
        return left + math.log1p(math.exp(right - left))

    def _captcha_allowed_indices(self, charset: list[str]) -> dict[str, list[int]]:
        if self._captcha_charset_indices is None:
            indices: dict[str, list[int]] = {char: [] for char in self._CAPTCHA_ALLOWED_CHARS}
            for index, char in enumerate(charset):
                upper = char.upper() if char else char
                if upper in indices:
                    indices[upper].append(index)
            self._captcha_charset_indices = indices
        return self._captcha_charset_indices

    def _ctc_beam_decode(self, result: dict, target_length: int) -> tuple[str, float]:
        """Decode ddddocr probability output as fixed-length A-Z/0-9 CTC."""
        probabilities = result.get("probabilities") or []
        charset = result.get("charset") or []
        if not probabilities or not charset:
            return (result.get("text") or "").strip().upper(), float(result.get("confidence") or 0.0)

        char_indices = self._captcha_allowed_indices(charset)
        beams: dict[tuple[str, ...], tuple[float, float]] = {(): (0.0, -math.inf)}

        for step in probabilities:
            row = step[0]
            blank_logp = math.log(max(float(row[0]), 1e-30))
            char_logps: list[tuple[str, float]] = []
            for char, indices in char_indices.items():
                prob = sum(float(row[index]) for index in indices)
                char_logps.append((char, math.log(max(prob, 1e-30))))
            char_logps.sort(key=lambda item: item[1], reverse=True)
            char_logps = char_logps[: self._CAPTCHA_BEAM_TOP_CHARS]

            next_beams: dict[tuple[str, ...], tuple[float, float]] = defaultdict(
                lambda: (-math.inf, -math.inf)
            )
            for prefix, (prob_blank, prob_nonblank) in beams.items():
                next_blank, next_nonblank = next_beams[prefix]
                next_blank = self._logadd(next_blank, prob_blank + blank_logp)
                next_blank = self._logadd(next_blank, prob_nonblank + blank_logp)
                next_beams[prefix] = (next_blank, next_nonblank)

                for char, char_logp in char_logps:
                    if len(prefix) >= target_length and (not prefix or prefix[-1] != char):
                        continue

                    if prefix and prefix[-1] == char:
                        same_blank, same_nonblank = next_beams[prefix]
                        same_nonblank = self._logadd(same_nonblank, prob_nonblank + char_logp)
                        next_beams[prefix] = (same_blank, same_nonblank)

                        if len(prefix) < target_length:
                            extended = prefix + (char,)
                            ext_blank, ext_nonblank = next_beams[extended]
                            ext_nonblank = self._logadd(ext_nonblank, prob_blank + char_logp)
                            next_beams[extended] = (ext_blank, ext_nonblank)
                    else:
                        extended = prefix + (char,)
                        ext_blank, ext_nonblank = next_beams[extended]
                        ext_nonblank = self._logadd(ext_nonblank, prob_blank + char_logp)
                        ext_nonblank = self._logadd(ext_nonblank, prob_nonblank + char_logp)
                        next_beams[extended] = (ext_blank, ext_nonblank)

            ranked = sorted(
                next_beams.items(),
                key=lambda item: self._logadd(item[1][0], item[1][1]),
                reverse=True,
            )
            beams = dict(ranked[: self._CAPTCHA_BEAM_SIZE])

        prefix, (prob_blank, prob_nonblank) = max(
            beams.items(),
            key=lambda item: (
                len(item[0]) == target_length,
                self._logadd(item[1][0], item[1][1]),
            ),
        )
        log_probability = self._logadd(prob_blank, prob_nonblank)
        # Normalize log probability so it can be summed across variants as a
        # confidence-like agreement score.
        confidence = math.exp(log_probability / max(1, len(probabilities)))
        return "".join(prefix), confidence

    def _select_captcha_by_agreement(
        self, candidates: list[CaptchaCandidate]
    ) -> CaptchaCandidate:
        grouped: dict[str, list[CaptchaCandidate]] = defaultdict(list)
        for candidate in candidates:
            grouped[candidate.text].append(candidate)

        text, members = max(
            grouped.items(),
            key=lambda item: (
                len(item[0]) == self.config.captcha_length,
                sum(candidate.confidence for candidate in item[1]),
                max(candidate.confidence for candidate in item[1]),
            ),
        )
        return max(members, key=lambda candidate: candidate.confidence)

    def _ocr_captcha(self, driver: WebDriver, area_name: str, attempt: int) -> str:
        """auto 模式 provider：取圖 → Otsu 前處理 → ddddocr 辨識，回傳大寫結果。"""
        _ = driver
        if self._ocr is None:
            try:
                import ddddocr  # 延後載入：僅 auto 模式需要
            except ImportError as exc:
                raise CrawlerError(
                    "auto 模式需安裝 ddddocr 與 opencv-python：pip install ddddocr opencv-python"
                ) from exc
            self._ocr = ddddocr.DdddOcr(show_ad=False)
        png = self._grab_captcha_png()
        variant_count = max(1, self.config.captcha_variant_count)
        decoder = self.config.captcha_decoder
        if decoder not in {"native", "beam"}:
            raise CrawlerError(f"Unsupported captcha decoder: {decoder}")

        if variant_count == 1 and decoder == "native":
            processed = self._otsu_png(png)
            text = (self._ocr.classification(processed) or "").strip().upper()
            self.logger.info("OCR[%s] 第 %s 次辨識結果：%r", area_name, attempt, text)
            return text

        candidates: list[CaptchaCandidate] = []
        variants = (
            [("otsu", self._otsu_png(png))]
            if variant_count == 1
            else self._captcha_variant_pngs(png, variant_count)
        )
        for variant_name, processed in variants:
            result = self._ocr.classification(processed, probability=True)
            if decoder == "beam":
                text, confidence = self._ctc_beam_decode(result, self.config.captcha_length)
            else:
                text = (result.get("text") or "").strip().upper()
                confidence = float(result.get("confidence") or 0.0)
            candidates.append(CaptchaCandidate(variant_name, text, confidence))

        selected = (
            self._select_captcha_by_agreement(candidates)
            if decoder == "beam"
            else max(
                candidates,
                key=lambda item: (len(item.text) == self.config.captcha_length, item.confidence),
            )
        )
        self.logger.info(
            "OCR[%s] attempt=%s variants=%s decoder=%s selected=%s conf=%.4f len_ok=%s result=%r",
            area_name,
            attempt,
            len(variants),
            decoder,
            selected.variant_name,
            selected.confidence,
            len(selected.text) == self.config.captcha_length,
            selected.text,
        )
        return selected.text

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
