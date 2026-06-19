"""結構化日誌：查詢紀錄與一般 app log 同時寫入檔案（JSON lines）與 stdout。

- 檔案輸出純 JSON 每行一筆，方便試題三的 Log 收集器解析與查詢歷史。
- stdout 輸出帶時間前綴，方便容器化時由 log driver 收集。
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime
from pathlib import Path


def get_log_dir() -> Path:
    """日誌目錄；以環境變數 LOG_DIR 覆蓋（容器化時掛共用 volume 給試題三收集）。"""
    return Path(os.getenv("LOG_DIR", str(Path(__file__).resolve().parent.parent / "logs")))


def _make_logger(name: str, filename: str) -> logging.Logger:
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    log_dir = get_log_dir()
    log_dir.mkdir(parents=True, exist_ok=True)

    file_handler = logging.FileHandler(log_dir / filename, encoding="utf-8")
    file_handler.setFormatter(logging.Formatter("%(message)s"))  # 檔案存純 JSON 行

    stream_handler = logging.StreamHandler(sys.stdout)
    stream_handler.setFormatter(logging.Formatter("%(asctime)s | %(name)s | %(message)s"))

    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    logger.propagate = False
    return logger


def get_app_logger() -> logging.Logger:
    return _make_logger("api.app", "app.log")


def get_query_logger() -> logging.Logger:
    return _make_logger("api.query", "query.log")


def get_notify_logger() -> logging.Logger:
    return _make_logger("api.notify", "notify.log")


def log_json(logger: logging.Logger, *, level: str = "INFO", **fields) -> None:
    payload = {"ts": datetime.now().astimezone().isoformat(), "level": level, **fields}
    logger.info(json.dumps(payload, ensure_ascii=False))


def log_query(logger: logging.Logger, **fields) -> None:
    """記錄一筆 API 查詢（時間、輸入、結果筆數、耗時…）。"""
    log_json(logger, level="INFO", kind="query", **fields)
