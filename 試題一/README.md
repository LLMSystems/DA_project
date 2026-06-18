# 試題一

本資料夾為 `interview_assignment.pdf` 的試題一實作。

目前內容包含：

- `doorplate_scraper/`
  - Selenium 爬蟲
  - `aiosqlite` 寫入
  - CSV 匯出
- `tests/`
  - `pytest` 單元測試
- `規格文件.md`
  - 已確認的網站規格與技術選型

## 前置需求

- **Python 3.10+**
- **Google Chrome 瀏覽器**（必裝）：爬蟲以 Selenium driving Chrome。
  - 不需手動安裝 ChromeDriver；Selenium 4 內建的 Selenium Manager 會自動下載與你 Chrome 版本相符的 driver。
  - 但 **Chrome 本體必須已安裝**，否則啟動會失敗。

## 安裝

建議先建立並啟用虛擬環境（名稱自訂），再安裝依賴：

```powershell
pip install -r .\試題一\requirements.txt
```

## 執行測試

```powershell
cd .\試題一
python -m pytest
```

## 執行爬蟲

建議先用有視窗模式，方便人工輸入驗證碼：

```powershell
cd .\試題一
python .\main.py --areas 大安區
```

常用參數：

- `--city 臺北市`（預設 `臺北市`）
- `--start-date 114/09/01`（民國日期起）
- `--end-date 114/11/30`（民國日期迄）
- `--register-kind 1`（編訂類別代碼，`1` = 門牌初編）
- `--areas 大安區,信義區`（以逗號分隔；留空查全部行政區）
- `--db-path data/doorplate.sqlite3`
- `--csv-path data/doorplate_records.csv`
- `--log-path logs/crawler.log`
- `--headless`（headless 模式；驗證碼處理方式見下方說明）

## 驗證碼輸入方式

本爬蟲採**人工輸入驗證碼**，依模式自動切換取得驗證碼的方式：

- **有視窗模式（預設）**：直接看瀏覽器畫面上的圖形驗證碼，在終端機提示時輸入。
- **headless 模式（`--headless`）**：沒有可見視窗，程式會把驗證碼元素**截圖存成 PNG 並以系統預設看圖程式自動開啟**（同時把路徑寫進 Log），你看圖後在終端機輸入即可。
  - 圖檔位置：`logs/captcha/captcha_<行政區>_attempt<次數>.png`
  - 若自動開圖失敗（例如無 GUI 環境），終端機仍會印出圖檔路徑，可自行開啟。

驗證碼輸入錯誤時會自動重新產製並再次提示，最多重試 5 次。

> 進階：建構 `DoorplateScraper` 時可注入自訂 `captcha_provider`，即可改為自動辨識等其他方案。

## 輸出

- 資料庫：`data/doorplate.sqlite3`
- CSV：`data/doorplate_records.csv`
- Log：`logs/crawler.log`

## 已實作流程

1. 進入門牌查詢主頁
2. 切換到 `以鄉鎮市區、編釘類別查詢`
3. 自動選取 `臺北市`(可輸入其他城市)
4. 逐區查詢（每區依序：選區別、datepicker 選起訖日期、選編訂類別、人工輸入驗證碼、送出）
5. 取得結果後以**前端分頁逐頁擷取**
6. **每區爬完即時寫入 SQLite**，全部完成後再彙整匯出 CSV
7. 錯誤處理：
   - 驗證碼錯誤自動重試
   - 查無資料視為正常結果並記錄
   - **單一行政區失敗只記錄並跳過，不影響其他區**（已落地的資料不會遺失）
   - 非預期中斷時，仍會把已蒐集資料輸出 CSV 作為證據

## 資料落地說明

- 採**逐區即時寫入資料庫**（`INSERT OR IGNORE`，以 `row_hash` 去重，可重複執行不產生重複資料）。
- CSV 於整體流程結束時一次彙整輸出（`utf-8-sig` 編碼，方便 Excel 開啟）。

## 初步驗證

提供 `scripts/check_output.py` 比對單次執行的 CSV 與 SQLite 是否一致、有無重複：

```powershell
cd .\試題一
python scripts\check_output.py --csv data\verify\c1.csv --db data\verify\c1.sqlite3
```

輸出會列出 CSV／DB 筆數、各行政區筆數，以及一致性檢查（`duplicate rows in CSV`、CSV 與 DB 的 `row_hash` 差集），最後給出 `PASS` / `FAIL`。

### 抽查結果

跨 **4 個縣市、5 組不同（縣市 × 行政區 × 日期區間）** 全量擷取，逐組以 `check_output.py` 檢查，並合併檢查跨組重複：

| 組 | 縣市 | 行政區 | 日期區間（民國） | 筆數 | check_output |
|----|------|--------|------------------|------|--------------|
| ① | 臺北市 | 大安區 | 114/09/01–114/11/30 | 130 | PASS |
| ② | 臺北市 | 中正區 | 114/06/01–114/08/31 | 232 | PASS |
| ③ | 新北市 | 板橋區 | 113/01/01–113/03/31 | 280 | PASS |
| ④ | 臺中市 | 西屯區 | 113/07/01–113/09/30 | 64 | PASS |
| ⑤ | 高雄市 | 苓雅區 | 112/01/01–112/06/30 | 936 | PASS |

- 每組：CSV 與 DB 筆數一致、組內 `row_hash` 0 重複。
- 跨組合計 **1642 筆全唯一、0 重複**（不同縣市／行政區不會相撞）。
- `city` / `township` / `query_city` 欄位皆正確填入（無空值）。
- 另以大安區全量（855 筆）驗證過多頁翻頁完整性。

> 涵蓋面：互動地圖選縣市（4 縣市）、datepicker 跨不同年月、逐區資料解析、CSV／DB 落地一致性與去重。

## 城市切換

目前預設城市是 `臺北市`，但也可透過 `--city` 切換：

```powershell
cd .\試題一
python .\main.py --city 台中市
```

題目要求使用台北市時，可直接使用預設值，或明確指定：

```powershell
cd .\試題一
python .\main.py --city 臺北市 --areas 大安區
```
