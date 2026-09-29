"""CLI: python -m price_monitor check | watch --every 30 | report --out report.xlsx"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .alerts import TelegramNotifier
from .config import BASE_DIR, ConfigError, Settings, load_settings
from .export import GoogleSheetsClient, HEADERS, write_excel
from .fetcher import Fetcher
from .monitor import RunResult, run_once
from .storage import Storage

log = logging.getLogger("price_monitor")


async def _cycle(settings: Settings, out: Path | None) -> RunResult:
    storage = Storage(settings.db_path)
    notifier = TelegramNotifier(settings.telegram_token, settings.telegram_chat_id) if settings.telegram_token else None
    try:
        async with Fetcher(timeout=settings.timeout, max_retries=settings.max_retries,
                           per_host_delay=settings.per_host_delay, respect_robots=settings.respect_robots) as fetcher:
            result = await run_once(settings, fetcher, storage, notifier)
        history = {p.url: [(o.seen_at, o.price) for o in await storage.history(p.url, days=30)] for p in settings.products}
        if out:
            write_excel(result.rows, history, out)
        if settings.google_sheet_id and settings.google_credentials:
            sheets = GoogleSheetsClient(settings.google_credentials)
            try:
                tz = ZoneInfo("Europe/Moscow")
                await sheets.write(settings.google_sheet_id, [HEADERS] + [r.as_list(tz) for r in result.rows])
            except Exception:
                log.exception("Не удалось обновить Google Таблицу")
            finally:
                await sheets.close()
        return result
    finally:
        storage.close()
        if notifier:
            await notifier.close()


async def _watch(settings: Settings, minutes: float, out: Path | None) -> None:
    log.info("Мониторинг запущен: %d товаров, проверка каждые %.0f мин", len(settings.products), minutes)
    while True:
        started = datetime.now(timezone.utc)
        try:
            await _cycle(settings, out)
        except Exception:
            log.exception("Сбой цикла мониторинга — продолжим в следующий раз")
        elapsed = (datetime.now(timezone.utc) - started).total_seconds()
        await asyncio.sleep(max(5.0, minutes * 60 - elapsed))


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    parser = argparse.ArgumentParser(prog="price_monitor", description="Мониторинг цен с уведомлениями в Telegram")
    parser.add_argument("--products", type=Path, help="файл со списком товаров (по умолчанию products.json)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p_check = sub.add_parser("check", help="проверить цены один раз")
    p_check.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "prices.xlsx", help="куда сохранить Excel-отчёт")
    p_watch = sub.add_parser("watch", help="проверять по расписанию")
    p_watch.add_argument("--every", type=float, default=60, help="интервал в минутах (по умолчанию 60)")
    p_watch.add_argument("--out", type=Path, default=BASE_DIR / "reports" / "prices.xlsx")
    args = parser.parse_args(argv)

    try:
        settings = load_settings(args.products)
    except ConfigError as exc:
        log.error("%s", exc)
        return 2
    try:
        if args.cmd == "check":
            result = asyncio.run(_cycle(settings, args.out))
            for row in result.rows:
                price = f"{row.price:,.0f}".replace(",", " ") if row.price is not None else "—"
                print(f"{row.name[:40]:40} {price:>10}  {row.error or ''}")
            return 1 if result.errors == len(result.rows) else 0
        asyncio.run(_watch(settings, args.every, args.out))
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
