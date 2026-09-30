"""Telegram 通知（選用）。"""
import logging

import requests

log = logging.getLogger(__name__)


class Notifier:
    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token
        self.chat_id = chat_id

    def send(self, text: str) -> None:
        log.info(text)
        if not (self.token and self.chat_id):
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.token}/sendMessage",
                json={"chat_id": self.chat_id, "text": text},
                timeout=10,
            )
        except requests.RequestException as exc:
            log.warning("Telegram 通知失敗：%s", exc)
