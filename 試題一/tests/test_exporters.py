from doorplate_scraper.exporters import records_to_dataframe
from doorplate_scraper.models import DoorplateRecord, RocDate


def test_records_to_dataframe_keeps_expected_columns() -> None:
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

    frame = records_to_dataframe([record])

    assert frame.shape == (1, 17)
    assert frame.iloc[0]["township"] == "大安區"
    assert frame.iloc[0]["edit_type"] == "門牌初編"
