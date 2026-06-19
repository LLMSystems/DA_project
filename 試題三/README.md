# 試題三：Log 收集器與異常通報

以 **docker compose** 串接爬蟲（試題一）、查詢 API（試題二）、Log 收集與異常通報，
形成可檢視即時/歷史 log、並在異常時自動通報的維運平台。

## 架構與資料流

```
試題一 crawler ─ 寫 ─▶ DB(volume) ◀─ 讀 ─ 試題二 api
      │                                   │
      └─ crawler.log ──┐      ┌── api/*.log（JSON）
                       ▼      ▼
                     共用 logs volume
                          │
                     Promtail（收集）
                          │
                        Loki（儲存／查詢，保留 7 天）
                          │
                     Grafana（檢視 + 告警規則）
                          │ 平台偵測：爬蟲 ERROR／API empty_result
                          ▼ webhook
                   notifier-sink（落地通報紀錄，可查詢）
```

- **Log 收集**：Promtail tail 共用 logs volume → Loki。
- **檢視即時/歷史**：Grafana Dashboard（Loki 保留 7 天，可查歷史）。
- **異常通報（平台偵測為主）**：Grafana 以 LogQL 計數異常事件，超門檻 → webhook → notifier-sink 落地。

## 前置需求

- Docker 與 Docker Compose（**建議在 Linux 環境執行**）。

## 啟動

```bash
cd 試題三
docker compose up --build
```

各服務埠位：

| 服務 | 網址 | 用途 |
|------|------|------|
| Grafana | <http://localhost:3000> | 看 log 與告警（匿名登入，已開 Admin） |
| API (試題二) | <http://localhost:8000/docs> | 查詢 API |
| notifier-sink | <http://localhost:9000> | 通報紀錄查詢 |
| Loki | <http://localhost:3100> | log 儲存（一般不直接開） |

> `crawler` 為一次性工作，預設僅抓 `大安區,中正區` 以加速 demo；要抓**台北市全部行政區**，
> 移除 compose 中 crawler 的 `--areas` 參數再 `docker compose up crawler` 即可。

## 怎麼看（對應繳交項目）

- **即時/歷史 Log 查詢**：Grafana → Dashboards → 「門牌系統維運監控」，含爬蟲與 API 兩個 log 面板與異常計數。也可在 Explore 用 LogQL（如 `{job="crawler"}`、`{job="api"} |= "empty_result"`）查歷史。
- **通報紀錄**：開 <http://localhost:9000>（HTML 表格）或 <http://localhost:9000/notifications>（JSON）。

### 示範畫面

維運 Dashboard（即時 Log + 異常計數）：

![Grafana Dashboard](../docs/03_grafana_dashboard.png)

歷史 Log 查詢（Explore + LogQL）：

![Grafana Explore](../docs/04_grafana_explore.png)

告警觸發（平台偵測）與通報落地（notifier-sink）：

![Alert rules](../docs/05_alert_firing.png)
![通報紀錄](../docs/06_sink_notifications.png)

> 更完整的交付物對照見 [docs/README.md](../docs/README.md)。

## 驗證兩種異常通報

1. **API 查無資料（試題二）**：對不存在的區查詢，會記 `empty_result`，1 分鐘內 Grafana 告警 → sink 出現一筆通報。

   ```bash
   curl -X POST http://localhost:8000/query \
     -H "Content-Type: application/json" \
     -d '{"city":"台北市","township":"不存在區"}'
   ```

2. **爬蟲異常（試題一）**：爬蟲過程若發生 ERROR（單區失敗、網站變更等）會寫入 `crawler.log`，
   Grafana 偵測到 `| ERROR |` 即告警 → sink 通報。

> 告警評估間隔為 1 分鐘，觸發後約 1～2 分鐘內可在 sink 看到通報。

## 異常處理設計（對應試題一說明）

- 爬蟲：單一行政區失敗只記錄並跳過、驗證碼錯誤自動重試（詳見試題一 README）；任何 ERROR 都會被本平台偵測通報。
- API：查無資料、DB 不可用皆寫入結構化 log 並由平台通報。
- 網站變更：解析失敗會丟出例外並記 ERROR，透過告警即時得知。

## [加分題] 自動化排程

`crawler` 容器為一次性工作。要定期執行有幾種方式：

- **Host 排程**：以 cron（Linux）或工作排程器呼叫 `docker compose run --rm crawler`。
  例：每日 02:00 執行 `0 2 * * * cd /path/試題三 && docker compose run --rm crawler`。
- **常駐排程容器**：另起一個帶 cron 的容器定時觸發 crawler（可後續加入 compose）。

## 設定檔一覽

| 路徑 | 說明 |
|------|------|
| `docker-compose.yml` | 服務編排與容器關聯 |
| `loki/loki-config.yml` | Loki 單體 + 檔案系統儲存、保留 7 天 |
| `promtail/promtail-config.yml` | 收集爬蟲（文字）與 API（JSON）兩種 log |
| `grafana/provisioning/datasources/` | Loki 資料源 |
| `grafana/provisioning/dashboards/` + `grafana/dashboards/` | 維運 Dashboard |
| `grafana/provisioning/alerting/` | 告警規則、通知路由、webhook 通知管道 |
| `notifier_sink/` | 通報接收與查詢服務 |
