"""通知接收端（notifier-sink）。

接收 Grafana alert 的 webhook，落地成可查詢的通報紀錄（SQLite），並提供查詢頁面。
這是「異常通報」的接收與留存端；通知形式不限，此處以最小可重現的 webhook + 自存方式實作，
審閱者 docker compose up 後即可在 / 看到歷史通報。
"""
from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse

DB_PATH = Path(os.getenv("SINK_DB", "/sink/notifications.sqlite3"))

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS notifications (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at TEXT NOT NULL,
    status TEXT,
    alertname TEXT,
    severity TEXT,
    summary TEXT,
    starts_at TEXT,
    raw TEXT NOT NULL
)
"""


def _init_db() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute(_CREATE_SQL)
        con.commit()


def _store_alert(alert: dict[str, Any]) -> None:
    labels = alert.get("labels", {}) or {}
    annotations = alert.get("annotations", {}) or {}
    row = (
        datetime.now(timezone.utc).astimezone().isoformat(),
        alert.get("status"),
        labels.get("alertname"),
        labels.get("severity"),
        annotations.get("summary") or annotations.get("description"),
        alert.get("startsAt"),
        json.dumps(alert, ensure_ascii=False),
    )
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.execute(
            "INSERT INTO notifications "
            "(received_at, status, alertname, severity, summary, starts_at, raw) "
            "VALUES (?,?,?,?,?,?,?)",
            row,
        )
        con.commit()


_init_db()  # 載入即建表，確保任何請求進來前資料表已存在。

app = FastAPI(title="試題三：通知接收端 notifier-sink", version="1.0.0")


@app.get("/health")
def health() -> dict[str, Any]:
    with closing(sqlite3.connect(DB_PATH)) as con:
        (count,) = con.execute("SELECT COUNT(*) FROM notifications").fetchone()
    return {"status": "ok", "notifications": int(count)}


@app.post("/webhook")
async def webhook(request: Request) -> dict[str, Any]:
    """接收 Grafana 通知。Grafana 會在 payload 的 alerts[] 帶一或多筆告警。"""
    payload = await request.json()
    alerts = payload.get("alerts")
    if not isinstance(alerts, list) or not alerts:
        # 非標準格式也留存，避免漏接。
        _store_alert({"status": payload.get("status"), "annotations": {"summary": payload.get("message")}, "raw_passthrough": payload})
        return {"stored": 1}
    for alert in alerts:
        _store_alert(alert)
    return {"stored": len(alerts)}


@app.get("/notifications")
def notifications(limit: int = 100) -> list[dict[str, Any]]:
    with closing(sqlite3.connect(DB_PATH)) as con:
        con.row_factory = sqlite3.Row
        rows = con.execute(
            "SELECT id, received_at, status, alertname, severity, summary, starts_at "
            "FROM notifications ORDER BY id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    items = notifications(limit=200)
    rows = "".join(
        f"<tr><td>{n['id']}</td><td>{n['received_at']}</td>"
        f"<td>{n['status'] or ''}</td><td>{n['alertname'] or ''}</td>"
        f"<td>{n['severity'] or ''}</td><td>{n['summary'] or ''}</td></tr>"
        for n in items
    )
    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8"/>
<title>通報紀錄</title>
<style>
 body{{font-family:system-ui,sans-serif;margin:24px}}
 table{{border-collapse:collapse;width:100%}}
 th,td{{border:1px solid #ddd;padding:6px 10px;font-size:14px;text-align:left}}
 th{{background:#f3f4f6}}
</style></head><body>
<h1>異常通報紀錄（{len(items)} 筆）</h1>
<p>來源：Grafana alert webhook。JSON 介面見 <a href="/notifications">/notifications</a>。</p>
<table><thead><tr><th>#</th><th>收到時間</th><th>狀態</th><th>告警名稱</th><th>嚴重度</th><th>摘要</th></tr></thead>
<tbody>{rows}</tbody></table>
</body></html>"""
