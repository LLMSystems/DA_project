from __future__ import annotations


def test_query_returns_records(client):
    resp = client.post("/query", json={"city": "台北市", "township": "大安區"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["count"] == 2
    assert body["city"] == "台北市"
    assert body["township"] == "大安區"
    assert {r["area_name"] for r in body["records"]} == {"錦安里", "龍泉里"}


def test_city_normalization_tai_vs_tai(client):
    # DB 存「臺北市」，以「台北市」查詢仍應命中（台↔臺 正規化）。
    resp = client.post("/query", json={"city": "台北市", "township": "大安區"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 2


def test_empty_result_is_200_with_zero(client):
    # 查無資料屬正常情境（會觸發通知），回 200 + count 0，不是 404。
    resp = client.post("/query", json={"city": "台北市", "township": "信義區"})
    assert resp.status_code == 200
    assert resp.json()["count"] == 0
    assert resp.json()["records"] == []


def test_validation_rejects_blank(client):
    resp = client.post("/query", json={"city": "", "township": "大安區"})
    assert resp.status_code == 422


def test_optional_edit_type_filter(client):
    # 兩筆皆為「門牌初編」。
    ok = client.post("/query", json={"city": "台北市", "township": "大安區", "edit_type": "門牌初編"})
    assert ok.json()["count"] == 2
    # 不存在的類別 → 0 筆。
    none = client.post("/query", json={"city": "台北市", "township": "大安區", "edit_type": "門牌增編"})
    assert none.json()["count"] == 0


def test_optional_edit_date_range(client):
    # 樣本 edit_date：錦安里 114/09/05、龍泉里 114/10/01。
    only_oct = client.post(
        "/query",
        json={"city": "台北市", "township": "大安區", "edit_date_start": "114/10/01"},
    )
    assert only_oct.json()["count"] == 1
    assert only_oct.json()["records"][0]["area_name"] == "龍泉里"

    only_sep = client.post(
        "/query",
        json={"city": "台北市", "township": "大安區", "edit_date_end": "114/09/30"},
    )
    assert only_sep.json()["count"] == 1
    assert only_sep.json()["records"][0]["area_name"] == "錦安里"


def test_health_ok(client):
    resp = client.get("/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["db_available"] is True
    assert body["records"] == 2
