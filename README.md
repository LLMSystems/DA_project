# DA project


一套圍繞**內政部戶政司門牌查詢**的資料系統：自動化爬取門牌異動資料、
提供查詢 API、收集 Log 並在異常時自動通報，並以容器排程定期更新資料。

```
爬取（試題一） → 落地共用 SQLite → 查詢 API（試題二）
                      │
                  共用 Log → 收集／儲存／檢視 → 異常通報（試題三）
                                                    ▲
                                          定期觸發爬蟲（加分題）
```

> 完整多視角架構圖見 **[試題四：系統架構圖](試題四/README.md)**。

## 子系統一覽

| 題目 | 子系統 | 技術 | 說明 |
|------|--------|------|------|
| [試題一](試題一/) | 門牌爬蟲 | Selenium · Chromium · ddddocr · OpenCV | 抓門牌異動、**驗證碼自動辨識**，落地 SQLite/CSV |
| [試題二](試題二/) | 查詢 API | FastAPI · uvicorn · aiosqlite | 唯讀查詢 API（`POST /query`、`/health`、`/docs`） |
| [試題三](試題三/) | Log 收集與異常通報 | Docker Compose · Loki · Promtail · Grafana | 即時/歷史 Log 檢視 + 平台偵測 → webhook 通報 |
| [試題四](試題四/) | 系統架構圖 | Mermaid | 總覽／部署／資料流／資料模型／設計取捨 |
| [額外](試題三/README.md#加分題-自動化排程) | 自動化排程 | Ofelia（Docker 原生） | 定時起新容器跑全量抓取，結果同進監控 |

## 快速開始

### 方式 A：一鍵起整套平台（建議，需 Docker，建議 Linux）

用 [試題三](試題三/) 的 `docker compose` 同時拉起爬蟲、API、Log 收集、監控與通報：

```bash
cd 試題三
docker compose up --build
```

| 服務 | 網址 | 用途 |
|------|------|------|
| 查詢 API | <http://localhost:8000/docs> | 互動式 API 文件，可直接試打 |
| Grafana | <http://localhost:3000> | 維運 Dashboard 與告警（匿名登入） |
| 通報紀錄 | <http://localhost:9000> | 異常通報落地查詢 |

### 方式 B：單獨在本機跑某一題

各題目錄內附獨立 README 與 `requirements.txt`，可單獨執行：

```powershell
# 試題一：爬取大安區（驗證碼自動辨識）
cd 試題一
pip install -r requirements.txt
python .\main.py --captcha auto --areas 大安區

# 試題二：啟動查詢 API（讀試題一產出的 DB）
cd ..\試題二
pip install -r requirements.txt
$env:DB_PATH = "..\試題一\data\doorplate.sqlite3"
uvicorn app.main:app --port 8000
```

## 交付物對照

示範畫面與檔案，彙整於 **[docs/README.md](docs/README.md)**。

| 請提供項目 | 位置 |
|------------|------|
| 完整程式碼 | [試題一](試題一/)、[試題二](試題二/)、[試題三](試題三/) |
| 示範執行結果 CSV | [試題一/data/verify/](試題一/data/verify/) |
| 示範 API 執行結果 | [docs/README.md](docs/README.md#試題二-api-執行結果) |
| LOG 與通報紀錄 | [docs/README.md](docs/README.md#試題三-log-與通報) |
| 系統架構圖 | [試題四](試題四/) |
| 排程設計 | [試題三 — 自動化排程](試題三/README.md#加分題-自動化排程) |

## 設計重點

- **共用資料層解耦**：爬蟲寫、API 唯讀同一份 SQLite；所有服務寫同一份 Log，靠 named volume 串接，無直接程式相依。
- **驗證碼自動辨識**：ddddocr + Otsu 前處理 + 5 碼閘門 + 失敗自動重試/降級人工；進階可開多變體與 beam CTC 解碼（3次重試可達 99% 成功率，詳見[OCR 優化報告](試題一/OCR_優化報告.md)）。
- **平台偵測為主的異常通報**：應用只負責寫結構化 Log，由 Grafana 以 LogQL 統一偵測。

## 環境需求

- Python 3.10+（單獨跑各題）。
- Docker 與 Docker Compose（方式 A 整套平台，**建議 Linux 環境**）。
- 試題一在 Windows 跑 `--captcha auto` 需 VC++ 執行庫（onnxruntime 相依），細節見[試題一 README](試題一/README.md)。
