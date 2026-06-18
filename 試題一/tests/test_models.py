from doorplate_scraper.models import DoorplateRecord, RocDate


def test_roc_date_parsing_and_formatting() -> None:
    value = RocDate.parse("114/09/01")

    assert value.year == 114
    assert value.month == 9
    assert value.day == 1
    assert value.to_input_value() == "114-09-01"
    assert value.to_gregorian_year() == 2025
    assert value.month_zero_based() == 8
    assert value.as_iso() == "2025-09-01"


def test_record_from_datatable_row_maps_edit_type() -> None:
    record = DoorplateRecord.from_datatable_row(
        {
            "v1": "臺北市大安區",
            "v2": "民國114年9月1日",
            "v3": "民國114年9月1日",
            "v4": "",
            "v5": "大安路一段1號",
            "v6": "1",
        },
        city="臺北市",
        township="大安區",
        query_start=RocDate.parse("114/09/01"),
        query_end=RocDate.parse("114/11/30"),
        source_url="https://example.com",
        source_page=1,
        source_row_index=1,
    )

    assert record.city == "臺北市"
    assert record.township == "大安區"
    assert record.edit_type == "門牌初編"
    assert record.row_hash
