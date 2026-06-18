import pytest

from doorplate_scraper.config import CrawlerConfig


def test_set_city_supports_aliases() -> None:
    config = CrawlerConfig()
    config.set_city("台北市")

    assert config.city_name == "台北市"
    assert config.city_code == "63000000"


def test_set_city_raises_for_unknown_city() -> None:
    config = CrawlerConfig()

    with pytest.raises(ValueError):
        config.set_city("火星市")
