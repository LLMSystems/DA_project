from __future__ import annotations

from pydantic import BaseModel, Field


class QueryRequest(BaseModel):
    """查詢輸入，對應題目的 Input JSON。

    city、township 為題目必填條件；其餘為選用過濾條件（沿用試題一的爬蟲參數，
    對應 DB 已存欄位），未提供則不過濾。
    """

    city: str = Field(..., min_length=1, examples=["台北市"], description="縣市，例如 台北市")
    township: str = Field(..., min_length=1, examples=["大安區"], description="鄉鎮市區，例如 大安區")
    edit_type: str | None = Field(
        default=None, examples=["門牌初編"], description="選用：編訂類別，精確比對"
    )
    edit_date_start: str | None = Field(
        default=None, examples=["114/09/01"], description="選用：編訂日期起（民國，含當日）"
    )
    edit_date_end: str | None = Field(
        default=None, examples=["114/11/30"], description="選用：編訂日期迄（民國，含當日）"
    )


class DoorplateRecord(BaseModel):
    """單筆門牌異動資料（對齊試題一 DB 欄位，隱藏內部欄位）。"""

    city: str
    township: str
    area_name: str
    edit_date: str
    change_date: str
    old_address: str
    new_address: str
    edit_type: str
    query_date_start: str
    query_date_end: str
    scraped_at: str


class QueryResponse(BaseModel):
    city: str
    township: str
    count: int
    records: list[DoorplateRecord]
