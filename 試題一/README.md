# 試題一

本資料夾為試題一實作。

目前內容包含：

- `doorplate_scraper/`
  - Selenium 爬蟲
  - `aiosqlite` 寫入
  - CSV 匯出
  - 驗證碼自動辨識（ddddocr + OpenCV，`--captcha auto`）
- `tests/`
  - `pytest` 單元測試
- `scripts/`
  - 輸出檢查與驗證碼蒐集／標註／辨識評測工具
- `試題一規格文件.md`
  - 已確認的網站規格與技術選型

## 前置需求

- **Python 3.10+**
- **Google Chrome 瀏覽器**（必裝）：爬蟲以 Selenium driving Chrome。
  - 不需手動安裝 ChromeDriver；Selenium 4 內建的 Selenium Manager 會自動下載與你 Chrome 版本相符的 driver。
  - 但 **Chrome 本體必須已安裝**，否則啟動會失敗。

## 安裝

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
- `--captcha manual|auto`（驗證碼處理；預設 `manual`。`auto` 見下方「驗證碼自動辨識」）
- `--headless`（headless 模式；驗證碼處理方式見下方說明）
- `--min-delay 1.5` / `--max-delay 4.0`（每區查詢送出前的隨機等待秒數範圍，反爬節流；設 `0 0` 關閉，見下方「反爬節流與指紋」）
- `--user-agent "..."`（自訂 User-Agent；留空則沿用瀏覽器 UA 並自動移除 Headless 標記）
- `--no-stealth`（關閉反自動化指紋遮蔽）

## 驗證碼輸入方式

預設採**人工輸入驗證碼**，依模式自動切換取得驗證碼的方式：

- **有視窗模式（預設）**：直接看瀏覽器畫面上的圖形驗證碼，在終端機提示時輸入。
- **headless 模式（`--headless`）**：沒有可見視窗，程式會把驗證碼元素**截圖存成 PNG 並以系統預設看圖程式自動開啟**（同時把路徑寫進 Log），你看圖後在終端機輸入即可。
  - 圖檔位置：`logs/captcha/captcha_<行政區>_attempt<次數>.png`
  - 若自動開圖失敗（例如無 GUI 環境），終端機仍會印出圖檔路徑，可自行開啟。

驗證碼輸入錯誤時會自動重新產製並再次提示，最多重試 5 次。

> 進階：建構 `DoorplateScraper` 時可注入自訂 `captcha_provider`，即可改為其他辨識方案。

## 驗證碼自動辨識（`--captcha auto`）

加上 `--captcha auto` 可改用 **ddddocr 自動辨識**驗證碼，無需人工：

```powershell
cd .\試題一
python .\main.py --captcha auto --areas 大安區
```

- **相依**：需 `pip install ddddocr opencv-python`（已列入 `requirements.txt`；`manual` 模式可不裝）。
  - Windows 上 ddddocr 依賴的 `onnxruntime` 需要**較新的 Microsoft Visual C++ Redistributable（x64）**，否則 `import onnxruntime` 會出現「DLL 初始化失敗」。請安裝[最新版 VC++ Redistributable](https://aka.ms/vs/17/release/vc_redist.x64.exe)。
- **前處理**：取驗證碼圖後做**灰階 → Otsu 二值化**再丟給 ddddocr（實測對本站雜訊底圖提升最顯著）。
- **5 碼閘門**：本站驗證碼固定 5 碼，辨識結果若非 5 碼視為不可信，**直接換一張重抽、不送出**（不浪費伺服器請求）。
- **自動降級**：`auto` 先讓 OCR 嘗試數次（預設 6 次），仍失敗才**自動降級為人工輸入**（沿用上節的視窗／截圖方式），確保最終仍可完成。
- **CPU 即可**：單張辨識約 10ms 級，不需要 GPU。

### 辨識率（人工標註樣本評測）

為避免只對單一樣本集過度調整，另蒐集三批各 100 張 holdout 驗證碼重新標註驗證。

| 方案 | 原 100 張 exact | holdout #1 exact | holdout #2 exact | holdout #3 exact | 合併 300 exact | 合併 char | 合併 len5 |
|------|---------------:|-----------------:|-----------------:|-----------------:|---------------:|----------:|----------:|
| 原圖直接辨識 | 59% | 62% | 58% | 66% | 62.0% | 81.8% | 70.7% |
| 灰階 + Otsu（原本 auto 預設） | 71% | 69% | 72% | 76% | 72.3% | 88.2% | 81.7% |
| `--captcha-variants 6` | 78% | 76% | 75% | 78% | 76.3% | 90.9% | 88.0% |
| `--captcha-variants 18` | 80% | 76% | **81%** | 78% | **78.3%** | **92.9%** | **92.3%** |
| `--captcha-variants 18 --captcha-decoder beam` | **85%** | **82%** | **88%** | **85%** | **85.0%** | **96.3%** | **99.7%** |

目前保留 `--captcha-variants 1 --captcha-decoder native` 作為預設，確保 `--captcha auto` 的原本速度與行為不變；需要兼顧速度與辨識率時建議使用 `--captcha-variants 6`，在三批 holdout 合併後由 Otsu 的 72.3% 提升到 76.3%，且耗時仍約 73ms/張。若更重視準確率，可使用 `--captcha-variants 18 --captcha-decoder beam`，三批 holdout 合併後達 85.0%，評測約 305ms/張。

搭配「5 碼閘門 + 重試（每次換新驗證碼）」：以合併 holdout 的單次成功率估算，Otsu 約 72.3%、variants=6 約 76.3%、variants=18 native 約 78.3%、variants=18 beam 約 85.0%。累積成功率 = 1 − (1 − p)ⁿ；`auto_captcha_attempts=6` 時，Otsu 約 99.95%、variants=6 約 99.98%、variants=18 beam 約 99.999%，用盡仍失敗才降級人工。

> 評測與資料蒐集腳本見 [scripts/](scripts/)：`collect_captchas.py`（蒐集樣本）、`label_captchas.py`（產生標註頁）、`eval_captcha.py`（ddddocr 預測報告）、`eval_cv.py`（比較各種 CV 前處理）、`eval_variant_selector.py`（比較 variants selector 策略）、`eval_beam_ablation.py`（beam variants 剪枝實驗）。這些腳本與其產物僅供評測，不影響主流程。

## 反爬節流與指紋

為降低**頻繁爬取被擋**的風險，預設啟用兩組機制（demo 想最快可關閉）：

**節流與退避（請求節奏）**

- **每區隨機等待**：每個行政區查詢送出前隨機等待 `--min-delay ~ --max-delay` 秒（預設 1.5–4s），打散規律的請求節奏。設 `--min-delay 0 --max-delay 0` 關閉。
- **驗證碼錯誤退避**：站方判定驗證碼錯誤時，採指數退避加抖動（`min(base·2^(n-1), 8s)`）再重試，避免疑似限流時還連續猛打。

> 註：結果分頁是**前端翻頁**（DataTable，不再打伺服器），故伺服器壓力來自「區數 × 驗證碼次數」而非資料筆數；提高 OCR 單次辨識率（`--captcha-variants` / `--captcha-decoder beam`）可減少驗證碼重抽，對站方更友善。

**指紋遮蔽（`--no-stealth` 關閉）**

- **User-Agent**：移除 headless 的 `HeadlessChrome` 標記（改回一般 `Chrome`）；可用 `--user-agent` 完全自訂。
- **`navigator.webdriver`**：以 CDP 注入遮成 `undefined`。
- **自動化特徵**：關閉「Chrome 正受自動化控制」橫幅（`--disable-blink-features=AutomationControlled` 等）。
- **視窗尺寸**：每次啟動加入抖動，避免固定解析度成為指紋。

> 整套 Docker demo 中，一次性 `crawler` 預設關閉節流以維持 demo 速度；定期全量爬的排程器（Ofelia）刻意保留節流，貼近真實「頻繁爬」情境。詳見 [試題三 docker-compose.yml](../試題三/docker-compose.yml) 與 [ofelia/config.ini](../試題三/ofelia/config.ini)。

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
- CSV 於整體流程結束時一次彙整輸出（。

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

## 進階 OCR variants

`--captcha auto` 預設仍使用原本的單一 Otsu 流程。若要用較多 CPU 時間換取較高辨識率，可加上 `--captcha-variants`：

```powershell
python .\main.py --captcha auto --captcha-variants 6 --areas 大安區
python .\main.py --captcha auto --captcha-variants 18 --areas 大安區
python .\main.py --captcha auto --captcha-variants 18 --captcha-decoder beam --areas 大安區
```

- `--captcha-variants 1`: 原本行為；只跑一張 Otsu 前處理圖，速度最快。
- `--captcha-variants 6`: 建議的平衡模式；合併 holdout exact 76.3%，速度與準確率較平衡。
- `--captcha-variants 18`: 完整 native 搜尋；合併 holdout exact 78.3%。
- `--captcha-decoder beam`: 最高準確率模式；限制 CTC 只輸出 5 碼 A-Z/0-9，合併 holdout exact 85.0%，但比 native 慢。
