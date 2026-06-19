"""異常通知 hook。

試題二先落地最小可用版本：把通知寫入 notify.log（JSON 行）並輸出到 stdout。
試題三可替換或擴充為實際通知管道（webhook / Slack / email…）——只要保持
`notify(event, message, **context)` 介面即可無痛接上。
"""
from __future__ import annotations

from .logging_setup import get_notify_logger, log_json


class Notifier:
    def __init__(self) -> None:
        self._logger = get_notify_logger()

    def notify(self, event: str, message: str, **context) -> None:
        """發出一則異常通知。event 為事件類型，message 為人類可讀訊息。"""
        log_json(self._logger, level="WARNING", event=event, message=message, **context)
