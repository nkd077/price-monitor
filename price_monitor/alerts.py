"""Правила уведомлений и отправка в Telegram."""
from __future__ import annotations

import asyncio
import html
import logging
from dataclasses import dataclass

import httpx

from .config import Product
from .storage import Observation

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class Alert:
    kind: str  # drop | target | back_in_stock | out_of_stock
    product: Product
    old: Observation | None
    new: Observation

    def render(self) -> str:
        name = html.escape(self.product.name)
        currency = {"RUB": "₽", "RUR": "₽", None: "₽"}.get(self.new.currency, self.new.currency or "₽")
        cur = f"{money(self.new.price)} {currency}"
        link = f'<a href="{html.escape(self.product.url)}">Открыть товар</a>'
        if self.kind == "drop" and self.old:
            pct = (self.old.price - self.new.price) / self.old.price * 100
            return f"📉 <b>{name}</b>\nЦена снизилась на {pct:.0f}%: {money(self.old.price)} → <b>{cur}</b>\n{link}"
        if self.kind == "target":
            return f"🎯 <b>{name}</b>\nЦена достигла цели: <b>{cur}</b> (цель {money(self.product.target_price or 0)})\n{link}"
        if self.kind == "back_in_stock":
            return f"✅ <b>{name}</b> снова в наличии — {cur}\n{link}"
        return f"⚠️ <b>{name}</b> закончился в наличии\n{link}"


def money(value: float) -> str:
    """12345.0 → '12 345' (неразрывный пробел между разрядами)."""
    text = f"{value:,.2f}".rstrip("0").rstrip(".")
    return text.replace(",", "\u00a0")


def evaluate(product: Product, old: Observation | None, new: Observation, drop_percent: float) -> list[Alert]:
    """Какие уведомления вызывает новое наблюдение по сравнению с предыдущим."""
    alerts: list[Alert] = []
    if old is not None:
        if old.price > 0 and (old.price - new.price) / old.price * 100 >= drop_percent:
            alerts.append(Alert("drop", product, old, new))
        if old.in_stock is False and new.in_stock is True:
            alerts.append(Alert("back_in_stock", product, old, new))
        if old.in_stock is True and new.in_stock is False:
            alerts.append(Alert("out_of_stock", product, old, new))
    target = product.target_price
    if target is not None and new.price <= target and (old is None or old.price > target):
        alerts.append(Alert("target", product, old, new))
    return alerts


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._url = f"https://api.telegram.org/bot{token}/sendMessage"
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=15, transport=transport)

    async def send(self, text: str) -> bool:
        payload = {"chat_id": self._chat_id, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
        for attempt in range(3):
            try:
                resp = await self._client.post(self._url, json=payload)
                if resp.status_code == 200:
                    return True
                if resp.status_code == 429:
                    retry = resp.json().get("parameters", {}).get("retry_after", 2)
                    log.warning("Telegram просит подождать %s с", retry)
                    await asyncio.sleep(min(float(retry), 30))
                    continue
                log.error("Telegram ответил %s: %s", resp.status_code, resp.text[:200])
                return False
            except httpx.HTTPError as exc:
                log.warning("Ошибка отправки в Telegram (%s), попытка %d/3", exc.__class__.__name__, attempt + 1)
        return False

    async def close(self) -> None:
        await self._client.aclose()
