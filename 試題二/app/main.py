"""試題二：門牌資料查詢 API（FastAPI）。

輸入縣市、鄉鎮市區，回傳試題一爬蟲落地於 SQLite 的門牌異動資料。
- POST /query ：題目要求的查詢端點。
- GET  /health：健檢（容器 healthcheck / 系統自動健檢用）。
- GET  /docs  ：FastAPI 自動產生的互動式 API 文件（可當作示範執行畫面）。

維運鉤子（銜接試題三）：
- 每次查詢寫入結構化 query log。
- 查無資料（空結果）時發出異常通知。
"""
from __future__ import annotations

import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import RedirectResponse

from .config import city_variants, get_db_path
from .db import db_status, query_records
from .logging_setup import get_app_logger, get_query_logger, log_json, log_query
from .models import QueryRequest, QueryResponse
from .notifier import Notifier

logger = get_app_logger()
query_logger = get_query_logger()
notifier = Notifier()


@asynccontextmanager
async def lifespan(app: FastAPI):
    db_path = get_db_path()
    status = await db_status(db_path)
    if status["available"]:
        log_json(logger, message=f"DB 就緒：{db_path}（{status['records']} 筆）")
    else:
        log_json(
            logger,
            level="WARNING",
            message=f"DB 尚未就緒：{status['reason']}（請先執行試題一爬蟲）",
        )
    yield


app = FastAPI(
    title="試題二：門牌資料查詢 API",
    version="1.0.0",
    description="查詢試題一爬取的門牌異動資料；輸入縣市、鄉鎮市區產出結果。",
    lifespan=lifespan,
)


@app.get("/", include_in_schema=False)
async def root():
    return RedirectResponse(url="/docs")


@app.get("/health")
async def health():
    db_path = get_db_path()
    status = await db_status(db_path)
    return {
        "status": "ok" if status["available"] else "degraded",
        "db_path": str(db_path),
        "db_available": status["available"],
        "records": status["records"],
        "detail": status["reason"],
    }


@app.post("/query", response_model=QueryResponse)
async def query(req: QueryRequest):
    db_path = get_db_path()
    status = await db_status(db_path)
    if not status["available"]:
        notifier.notify(
            "db_unavailable", status["reason"], city=req.city, township=req.township
        )
        raise HTTPException(
            status_code=503,
            detail=f"資料庫尚未就緒：{status['reason']}（請先執行試題一爬蟲）",
        )

    started = time.perf_counter()
    cities = city_variants(req.city)
    township = req.township.strip()
    rows = await query_records(
        db_path,
        cities,
        township,
        edit_type=req.edit_type,
        edit_date_start=req.edit_date_start,
        edit_date_end=req.edit_date_end,
    )
    elapsed_ms = round((time.perf_counter() - started) * 1000, 1)

    log_query(
        query_logger,
        city=req.city,
        township=township,
        normalized_cities=cities,
        edit_type=req.edit_type,
        edit_date_start=req.edit_date_start,
        edit_date_end=req.edit_date_end,
        count=len(rows),
        elapsed_ms=elapsed_ms,
    )

    if not rows:
        # 試題三鉤子：查無資料 → 異常通知。
        notifier.notify(
            "empty_result",
            f"查無資料：{req.city}/{township}",
            city=req.city,
            township=township,
        )

    return QueryResponse(city=req.city, township=township, count=len(rows), records=rows)
