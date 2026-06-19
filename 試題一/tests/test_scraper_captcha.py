import logging

from doorplate_scraper.config import CrawlerConfig
from doorplate_scraper.scraper import DoorplateScraper


class FakeOcr:
    def classification(self, image: bytes, probability: bool = False):
        assert probability is True
        if image == b"short":
            return {"text": "ABC", "confidence": 0.99}
        return {"text": "A1B2C", "confidence": 0.50}


def test_advanced_captcha_selector_prefers_expected_length(monkeypatch) -> None:
    config = CrawlerConfig(captcha_variant_count=2)
    scraper = DoorplateScraper(config, logger=logging.getLogger("test"))
    scraper._ocr = FakeOcr()

    monkeypatch.setattr(scraper, "_grab_captcha_png", lambda: b"raw")
    monkeypatch.setattr(
        DoorplateScraper,
        "_captcha_variant_pngs",
        staticmethod(lambda _png, _count: [("short", b"short"), ("ok", b"ok")]),
    )

    assert scraper._ocr_captcha(driver=None, area_name="area", attempt=1) == "A1B2C"
