# 試題四：系統架構圖

本文件以多個視角呈現整套系統，涵蓋試題一（爬蟲）、試題二（查詢 API）、
試題三（Log 收集與異常通報）與加分題（自動化排程）。
圖以 [Mermaid](https://mermaid.js.org/) 撰寫，GitLab／GitHub 可直接渲染。

- [1. 系統總覽](#1-系統總覽)
- [2. 容器部署架構（docker compose）](#2-容器部署架構docker-compose)
- [3. 端到端資料流](#3-端到端資料流)
- [4. 爬蟲內部流程（試題一）](#4-爬蟲內部流程試題一)
- [5. 異常通報流程（試題三）](#5-異常通報流程試題三)
- [6. 自動化排程（加分題）](#6-自動化排程加分題)
- [7. 資料模型](#7-資料模型)
- [8. 技術棧與元件對照](#8-技術棧與元件對照)
- [9. 設計取捨](#9-設計取捨)

---

## 1. 系統總覽

四個子系統圍繞「一份共用 SQLite 資料 + 一份共用 Log」協作：爬蟲產資料、API 供查詢、
監控平台收集 Log 並在異常時通報，排程器則定期觸發爬蟲形成維運閉環。

```mermaid
flowchart LR
    subgraph SRC["資料來源"]
        RIS["內政部戶政司<br/>門牌查詢網站<br/>ris.gov.tw"]
    end

    subgraph T1["試題一 · 爬蟲"]
        CR["crawler<br/>Selenium + ddddocr"]
    end

    subgraph T2["試題二 · 查詢 API"]
        API["api<br/>FastAPI"]
    end

    subgraph DATA["共用資料層（named volumes）"]
        DB[("SQLite<br/>doorplate.sqlite3")]
        LOGS["共用 Log 區<br/>crawler.log / api/*.log"]
    end

    subgraph T3["試題三 · 監控與通報平台"]
        PROM["Promtail<br/>收集"]
        LOKI[("Loki<br/>儲存／查詢")]
        GRAF["Grafana<br/>檢視 + 告警"]
        SINK["notifier-sink<br/>通報落地"]
    end

    subgraph BONUS["加分題 · 排程"]
        SCHED["Ofelia<br/>scheduler"]
    end

    USER(["使用者 / 維運人員"])

    RIS -->|"HTTPS 抓取"| CR
    CR -->|"寫入（INSERT OR IGNORE）"| DB
    CR -->|"寫 crawler.log"| LOGS
    DB -->|"唯讀查詢"| API
    API -->|"寫 query log（JSON）"| LOGS
    USER -->|"POST /query"| API

    LOGS --> PROM
    SCHED -.->|"stdout（docker.sock）"| PROM
    PROM --> LOKI --> GRAF
    GRAF -->|"webhook（超門檻）"| SINK
    USER -->|"看 Dashboard"| GRAF
    USER -->|"查通報紀錄"| SINK

    SCHED -.->|"定時起新容器"| CR
```

---

## 2. 容器部署架構（docker compose）

全部服務以單一 `docker compose`（專案名 `doorplate`）編排。容器間僅透過
**named volumes（共用資料）** 與 **Compose 內網（HTTP）** 耦合，無直接程式相依。

```mermaid
flowchart TB
    subgraph HOST["Docker Host（建議 Linux）"]
        direction TB

        subgraph NET["compose 內網 doorplate_default"]
            direction TB
            crawler["crawler<br/><i>doorplate-crawler:latest</i><br/>一次性工作"]
            api["api :8000<br/><i>FastAPI / uvicorn</i>"]
            promtail["promtail :9080"]
            loki["loki :3100"]
            grafana["grafana :3000"]
            sink["notifier-sink :9000"]
            scheduler["scheduler<br/><i>mcuadros/ofelia</i>"]
        end

        subgraph VOLS["named volumes"]
            vdata[("doorplate-data<br/>/data")]
            vlogs[("doorplate-logs<br/>/logs")]
            vloki[("loki-data")]
            vgraf[("grafana-data")]
            vsink[("sink-data")]
        end

        sock{{"/var/run/docker.sock"}}
    end

    PORTS(["Host ports<br/>3000 / 8000 / 9000 / 3100"])

    crawler --- vdata
    crawler --- vlogs
    api --- vdata
    api --- vlogs
    promtail --- vlogs
    loki --- vloki
    grafana --- vgraf
    sink --- vsink

    promtail -->|push| loki
    grafana -->|query| loki
    grafana -->|webhook| sink

    scheduler -.->|spawn 容器| sock
    promtail -.->|讀 scheduler stdout| sock

    api --> PORTS
    grafana --> PORTS
    sink --> PORTS
    loki --> PORTS

    classDef oneshot stroke-dasharray:4 3;
    class crawler oneshot;
```

**耦合方式**

| 關係 | 機制 | 說明 |
|------|------|------|
| crawler → api | 共用 `doorplate-data` volume 上的 SQLite | API 唯讀，不寫 DB |
| crawler / api → promtail | 共用 `doorplate-logs` volume | Promtail tail 檔案 |
| scheduler → crawler | 掛 `docker.sock`，依映像起新容器 | 無需宿主機 cron |
| promtail → scheduler | 掛 `docker.sock`，Docker 服務發現收 stdout | 排程事件可觀測 |
| grafana → sink | Compose 內網 HTTP webhook | 平台偵測異常 → 通報 |

---

## 3. 端到端資料流

從「網站原始資料」到「使用者查詢結果」與「異常通報」的完整時序。

```mermaid
sequenceDiagram
    autonumber
    participant RIS as 戶政司網站
    participant CR as crawler（試題一）
    participant DB as SQLite（共用）
    participant LOG as 共用 Log
    participant PT as Promtail
    participant LK as Loki
    participant GF as Grafana
    participant SK as notifier-sink
    participant U as 使用者
    participant API as API（試題二）

    Note over CR: 逐行政區抓取
    CR->>RIS: 開查詢頁、填條件、抓驗證碼
    CR->>CR: ddddocr 辨識（5 碼閘門，失敗換一張重試）
    CR->>RIS: 送出查詢、翻頁擷取
    CR->>DB: INSERT OR IGNORE（row_hash 去重）
    CR->>LOG: 寫 crawler.log（每區結果 / ERROR）

    par 監控管線（持續）
        LOG->>PT: tail 檔案
        PT->>LK: push（label: job/level/...）
        LK->>GF: 查詢 / 告警評估（LogQL）
    end

    U->>API: POST /query {city, township, ...}
    API->>DB: 唯讀查詢（台↔臺正規化）
    DB-->>API: 命中資料列
    API->>LOG: 寫 query log（JSON）
    alt 查無資料
        API->>LOG: event=empty_result
        GF->>GF: 告警規則命中（count_over_time > 0）
        GF->>SK: webhook 通報
        SK->>SK: 落地 notifications.sqlite3
        U->>SK: 查通報紀錄
    else 有資料
        API-->>U: 200 + records
    end
```

---

## 4. 爬蟲內部流程（試題一）

爬蟲的關鍵在於**驗證碼自動辨識**與**單區失敗隔離**。

```mermaid
flowchart TD
    START(["啟動 main.py<br/>--captcha auto --headless"]) --> OPEN["開查詢頁<br/>列出行政區"]
    OPEN --> LOOP{"還有<br/>未處理行政區？"}
    LOOP -->|否| EXPORT["輸出 CSV + 收尾 Log<br/>Crawl complete"]
    LOOP -->|是| RESET["重開乾淨查詢頁<br/>（避免 swal2 殘留）"]
    RESET --> FILL["填日期/類別<br/>等驗證碼載入"]
    FILL --> OCR["抓 canvas → Otsu 前處理<br/>ddddocr 辨識"]
    OCR --> GATE{"輸出 5 碼？"}
    GATE -->|否，重抽| REFRESH["點『產製新驗證碼』"]
    REFRESH --> ATT{"OCR 次數<br/>< 6？"}
    ATT -->|是| OCR
    ATT -->|否| MANUAL["降級人工輸入<br/>（headless 下記 ERROR）"]
    GATE -->|是| SUBMIT["送出查詢"]
    SUBMIT --> VERIFY{"驗證碼<br/>通過？"}
    VERIFY -->|否| REFRESH
    VERIFY -->|是| PAGE["翻頁擷取所有列"]
    PAGE --> PERSIST["INSERT OR IGNORE 落庫<br/>（即時，避免後續失敗丟資料）"]
    PERSIST --> LOOP

    SUBMIT -.->|例外| FAIL["記 ERROR、標記失敗區<br/>跳下一區（隔離）"]
    FAIL --> LOOP
    MANUAL --> SUBMIT

    classDef err fill:#fde,stroke:#c66;
    class FAIL,MANUAL err;
```

> 驗證碼重試次數取 6 的依據：單次成功率實測，預設 Otsu 約 0.72、最佳模式
> （`--captcha-variants 18 --captcha-decoder beam`）約 0.85。累積成功率 `1-(1-p)^n`：
> 以最佳模式 p=0.85，n=6 已達 ~99.999%（即便 Otsu 也達 ~99.95%），再往上邊際效益極小，
> 故用盡 6 次即降級人工。

---

## 5. 異常通報流程（試題三）

採「**平台偵測為主**」：不在應用程式內各自接通知，而是把所有狀態寫成 Log，
由 Grafana 統一以 LogQL 計數、超門檻才通報。新增偵測規則不需改動應用程式。

```mermaid
flowchart LR
    subgraph SIGNALS["異常訊號（寫入 Log）"]
        E1["爬蟲 ERROR<br/>單區失敗 / 網站變更"]
        E2["API empty_result<br/>查無資料"]
        E3["API db_unavailable<br/>DB 未就緒"]
    end

    LK[("Loki")]
    subgraph GF["Grafana 告警"]
        R1["規則：crawler-error<br/>count({job=crawler} |= '| ERROR |') > 0"]
        R2["規則：api-empty-result<br/>count({job=api} |= 'empty_result') > 0"]
        CP["contact point<br/>webhook"]
    end
    SINK["notifier-sink<br/>POST /webhook → SQLite"]
    VIEW(["維運人員<br/>:9000 查通報"])

    E1 --> LK
    E2 --> LK
    E3 --> LK
    LK --> R1 --> CP
    LK --> R2 --> CP
    CP -->|"HTTP（內網）"| SINK
    SINK --> VIEW
```

---

## 6. 自動化排程（加分題）

Ofelia 以 Docker 原生方式排程，無需宿主機 cron。排程結果同樣寫入共用 volume，
因此**排程跑的資料一樣進監控、失敗一樣告警**，與手動執行共用同一條維運鏈路。

```mermaid
flowchart TD
    CFG["ofelia/config.ini<br/>schedule = 0 0 2 * * *（每日 02:00）"] --> OF["scheduler（Ofelia daemon）"]
    OF -->|"到點，透過 docker.sock"| SPAWN["起新容器<br/>doorplate-crawler:latest<br/>--captcha auto --headless"]
    SPAWN --> RUN["全量抓取台北市各區"]
    RUN --> WDB[("寫共用 DB<br/>INSERT OR IGNORE 冪等")]
    RUN --> WLOG["寫共用 crawler.log"]
    SPAWN -->|"delete = true"| GONE["跑完即刪除容器"]
    OF -.->|"stdout：Started/Finished"| PT["Promtail（job=scheduler）"]
    PT --> DASH["Dashboard：排程器事件面板"]
    WLOG --> PT2["Promtail（job=crawler）"] --> DASH2["Dashboard：爬蟲 Log + 告警"]
```

> 若環境不允許掛 `docker.sock`，可改用 Host 排程：
> `0 2 * * * docker compose run --rm crawler ...`（cron / 工作排程器）。

---

## 7. 資料模型

爬蟲落地的核心表 `doorplate_records`（試題一寫、試題二讀）與通報表 `notifications`
（試題三 notifier-sink 寫）。

```mermaid
erDiagram
    doorplate_records {
        int     id PK
        text    row_hash UK "去重雜湊（INSERT OR IGNORE）"
        text    city
        text    township
        text    area_name
        text    edit_date "民國日期"
        text    change_date
        text    old_address
        text    new_address
        text    edit_type "編訂類別"
        text    query_city "查詢輸入（追溯用）"
        text    query_township
        text    query_date_start
        text    query_date_end
        text    source_url
        int     source_page
        int     source_row_index
        text    scraped_at
    }

    notifications {
        int     id PK
        text    received_at
        text    rule "告警規則名"
        text    status "firing / resolved"
        text    payload "Grafana webhook 原文"
    }
```

> 兩表分屬不同 volume／不同服務，無外鍵關係；`notifications` 由 Grafana 告警事件驅動，
> 與 `doorplate_records` 是「監控 vs 業務」兩條獨立資料流。

---

## 8. 技術棧與元件對照

| 子系統 | 元件 | 技術 | 對外埠 | 職責 |
|--------|------|------|--------|------|
| 試題一 | crawler | Python 3.12 · Selenium · Chromium · ddddocr · OpenCV | — | 抓門牌異動、驗證碼自動辨識、落 DB/CSV |
| 試題二 | api | FastAPI · uvicorn · aiosqlite | 8000 | 唯讀查詢 API（`POST /query`、`/health`、`/docs`） |
| 試題三 | promtail | Grafana Promtail | (9080) | 收集共用 Log + scheduler stdout |
| 試題三 | loki | Grafana Loki | 3100 | Log 儲存／查詢（保留 7 天） |
| 試題三 | grafana | Grafana | 3000 | Dashboard + LogQL 告警 |
| 試題三 | notifier-sink | FastAPI · SQLite | 9000 | 收 webhook、落地可查詢通報 |
| 加分題 | scheduler | mcuadros/ofelia | — | Docker 原生定時起 crawler |

**共用資源**：`doorplate-data`（SQLite）、`doorplate-logs`（Log）、`docker.sock`（排程 + 服務發現）。

---

## 9. 設計取捨

| 決策 | 選擇 | 理由 |
|------|------|------|
| API 存取爬蟲資料 | 共用 volume + 唯讀 SQLite | 零額外服務、職責清楚；API 不寫入避免與爬蟲競用 |
| 驗證碼 | ddddocr + Otsu + 5 碼閘門 + 自動重試/降級 | 兼顧自動化與穩定，失敗有人工後路 |
| 去重 | `row_hash` + `INSERT OR IGNORE` | 排程重跑冪等，不產生重複資料 |
| 異常偵測 | 平台偵測（Grafana LogQL）為主 | 應用只管寫 Log，新增規則免改程式、解耦 |
| 通報落地 | webhook → notifier-sink（SQLite） | 通報可查詢、可重放，便於 demo 與稽核 |
| 排程 | Ofelia（容器內） | 免宿主機 cron，結果同進監控形成閉環 |
| 多行 Log | Promtail multiline 合併 traceback | 避免 traceback 各行被打散排序而看似錯亂 |
