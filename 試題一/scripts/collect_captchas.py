"""蒐集驗證碼圖片的小工具：開啟查詢頁，連續截取多張驗證碼存檔，方便人工檢視。

用法：
    python scripts/collect_captchas.py --count 30 --out data/captcha_samples
    python scripts/collect_captchas.py --count 50 --out data/captcha_samples --headless

說明：
- 重用 doorplate_scraper 的導頁流程進到查詢頁。
- 每張都會確認驗證碼 signature（hidden key + 圖片 src）確實換新，避免重複截到同一張。
- 換新策略採分層：先試頁面上的刷新元素 / 點圖，失敗就整頁重導，確保一定拿到新圖。
"""
from __future__ import annotations

import argparse
import base64
import logging
import sys
import time
from pathlib import Path

# 允許直接以 `python scripts/collect_captchas.py` 執行（把專案根目錄加進 sys.path）。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from doorplate_scraper.config import CrawlerConfig  # noqa: E402
from doorplate_scraper.scraper import DoorplateScraper  # noqa: E402


def _build_logger() -> logging.Logger:
    logger = logging.getLogger("collect_captchas")
    if not logger.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def _grab_captcha_png(scraper: DoorplateScraper) -> bytes:
    """取得驗證碼圖的原始 PNG bytes。

    不用 element.screenshot（會被版面捲動 / 裁切影響而拍到空白或半張），
    改在瀏覽器端把圖以原始解析度畫進 canvas 輸出 base64；若 src 本身就是
    data URI 則直接取用。回傳 PNG bytes。
    """
    driver = scraper._required_driver()  # noqa: SLF001
    data_b64 = driver.execute_script(
        """
        const img = document.querySelector('#captchaImage_captchaKey');
        if (!img) { return null; }
        const src = img.getAttribute('src') || '';
        if (src.startsWith('data:')) {
          // 已是 data URI，直接回傳 base64 內容。
          return src.split(',')[1];
        }
        // 以原始解析度畫進 canvas，避免被顯示尺寸或裁切影響。
        const canvas = document.createElement('canvas');
        canvas.width = img.naturalWidth;
        canvas.height = img.naturalHeight;
        canvas.getContext('2d').drawImage(img, 0, 0);
        return canvas.toDataURL('image/png').split(',')[1];
        """
    )
    if not data_b64:
        raise RuntimeError("無法取得驗證碼圖片內容")
    return base64.b64decode(data_b64)


def _refresh_captcha(scraper: DoorplateScraper, previous_signature: str | None) -> str:
    """讓網站換一張新的驗證碼，並回傳新的 signature（確認確實換新）。"""
    driver = scraper._required_driver()  # noqa: SLF001 (姊妹腳本，沿用內部導頁能力)

    # 第一層：點頁面上的「產製新驗證碼」按鈕（以文字定位，最貼近使用者操作）；
    # 找不到再退而求其次點常見的刷新元素 / 點圖。
    clicked = driver.execute_script(
        """
        // 1) 依按鈕文字找「產製新驗證碼」。
        const els = document.querySelectorAll('button, a, input[type="button"]');
        for (const el of els) {
          const text = (el.innerText || el.value || '').trim();
          if (text.includes('產製新驗證碼')) { el.click(); return '產製新驗證碼'; }
        }
        // 2) 後備：常見刷新元素或直接點圖。
        const candidates = [
          'a[onclick*="aptcha"]',
          'img[onclick*="aptcha"]',
          '.captcha-refresh',
          '#refreshCaptcha',
          '#captchaImage_captchaKey',
        ];
        for (const sel of candidates) {
          const el = document.querySelector(sel);
          if (el) { el.click(); return sel; }
        }
        return null;
        """
    )

    if clicked:
        try:
            return scraper._wait_for_captcha_ready(previous_signature=previous_signature)  # noqa: SLF001
        except Exception:
            pass  # 點擊沒換成功，往下走整頁重導。

    # 第二層：整頁重導，最穩定（伺服器每次都會配一張新驗證碼）。
    scraper.open_query_page()
    return scraper._wait_for_captcha_ready(previous_signature=None)  # noqa: SLF001


def main() -> int:
    parser = argparse.ArgumentParser(description="蒐集驗證碼圖片以供檢視")
    parser.add_argument("--count", type=int, default=30, help="要蒐集的張數")
    parser.add_argument("--out", type=Path, default=Path("data/captcha_samples"), help="輸出資料夾")
    parser.add_argument("--city", default="臺北市", help="導頁用的縣市（不影響驗證碼）")
    parser.add_argument("--headless", action="store_true", help="無視窗模式")
    parser.add_argument("--delay", type=float, default=0.3, help="每張之間的間隔秒數")
    args = parser.parse_args()

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except AttributeError:
        pass

    logger = _build_logger()
    args.out.mkdir(parents=True, exist_ok=True)

    config = CrawlerConfig(headless=args.headless)
    config.set_city(args.city)  # 同步 city_name 與 city_code

    saved = 0
    seen_signatures: set[str] = set()
    with DoorplateScraper(config, logger=logger) as scraper:
        scraper.open_query_page()
        signature = scraper._wait_for_captcha_ready(previous_signature=None)  # noqa: SLF001

        while saved < args.count:
            if signature in seen_signatures:
                # 罕見：signature 沒換成功，強制再換一次。
                signature = _refresh_captcha(scraper, previous_signature=signature)
                continue
            seen_signatures.add(signature)

            path = args.out / f"captcha_{saved + 1:04d}.png"
            path.write_bytes(_grab_captcha_png(scraper))
            saved += 1
            logger.info("Saved %s (%d/%d)", path, saved, args.count)

            if saved < args.count:
                time.sleep(args.delay)
                signature = _refresh_captcha(scraper, previous_signature=signature)

    logger.info("完成：共存下 %d 張到 %s", saved, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
