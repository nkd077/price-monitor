"""Один проход мониторинга: загрузка → извлечение → история → уведомления → отчёт."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol

from .alerts import Alert, evaluate
from .config import Product, Settings
from .extract import ExtractError, extract_offer
from .export import ReportRow
from .fetcher import FetchError, Fetcher
from .storage import Observation, Storage

log = logging.getLogger(__name__)


class Notifier(Protocol):
    async def send(self, text: str) -> bool: ...


@dataclass
class RunResult:
    rows: list[ReportRow] = field(default_factory=list)
    alerts: list[Alert] = field(default_factory=list)
    errors: int = 0


async def check_product(product: Product, fetcher: Fetcher, storage: Storage, settings: Settings,
                        now: datetime) -> tuple[ReportRow, list[Alert]]:
    old = await storage.last(product.url)
    try:
        html = await fetcher.get(product.url)
        offer = extract_offer(html, product.selectors)
    except (FetchError, ExtractError) as exc:
        log.error("%s: %s", product.name, exc)
        reason = exc.reason if isinstance(exc, FetchError) else "цена не найдена"
        return ReportRow(product.name, None, old.price if old else None, None, product.target_price,
                         None, old.seen_at if old else None, product.url, error=reason), []

    new = Observation(product.url, product.name, offer.title, offer.price, offer.currency, offer.in_stock, now)
    alerts = evaluate(product, old, new, settings.drop_alert_percent)
    await storage.add(new)
    hist = await storage.history(product.url, days=30, now=now)
    return ReportRow(product.name, new.price, old.price if old else None, min(o.price for o in hist),
                     product.target_price, new.in_stock, now, product.url), alerts


async def run_once(settings: Settings, fetcher: Fetcher, storage: Storage, notifier: Notifier | None,
                   now: datetime | None = None) -> RunResult:
    now = now or datetime.now(timezone.utc)
    sem = asyncio.Semaphore(max(1, settings.concurrency))

    async def guarded(p: Product):
        async with sem:
            return await check_product(p, fetcher, storage, settings, now)

    result = RunResult()
    for row, alerts in await asyncio.gather(*(guarded(p) for p in settings.products)):
        result.rows.append(row)
        result.alerts.extend(alerts)
        result.errors += row.error is not None
    if notifier:
        for alert in result.alerts:
            await notifier.send(alert.render())
    log.info("Проверено товаров: %d, уведомлений: %d, ошибок: %d", len(result.rows), len(result.alerts), result.errors)
    return result
