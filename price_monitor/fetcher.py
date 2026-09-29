"""Аккуратная загрузка страниц: повторы с экспоненциальной задержкой, пауза между
запросами к одному сайту, ротация User-Agent и соблюдение robots.txt."""
from __future__ import annotations

import asyncio
import logging
import random
import time
from urllib.parse import urlsplit
from urllib.robotparser import RobotFileParser

import httpx

log = logging.getLogger(__name__)

USER_AGENTS = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 14_6) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.6 Safari/605.1.15",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:130.0) Gecko/20100101 Firefox/130.0",
)
RETRY_STATUSES = {429, 500, 502, 503, 504}
BOT_NAME = "PriceMonitorBot"


class FetchError(RuntimeError):
    def __init__(self, url: str, reason: str) -> None:
        super().__init__(f"{url}: {reason}")
        self.url, self.reason = url, reason


class Fetcher:
    def __init__(self, *, timeout: float = 20.0, max_retries: int = 3, per_host_delay: float = 1.5,
                 respect_robots: bool = True, transport: httpx.AsyncBaseTransport | None = None,
                 sleep=asyncio.sleep) -> None:
        self.max_retries = max_retries
        self.per_host_delay = per_host_delay
        self.respect_robots = respect_robots
        self._sleep = sleep
        self._client = httpx.AsyncClient(timeout=timeout, follow_redirects=True, transport=transport,
                                         headers={"Accept-Language": "ru-RU,ru;q=0.9,en;q=0.7",
                                                  "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
        self._host_locks: dict[str, asyncio.Lock] = {}
        self._last_hit: dict[str, float] = {}
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()

    async def __aenter__(self) -> "Fetcher":
        return self

    async def __aexit__(self, *exc: object) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _allowed(self, url: str) -> bool:
        if not self.respect_robots:
            return True
        parts = urlsplit(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_lock:
            await self._load_robots(origin)
        parser = self._robots[origin]
        return parser is None or parser.can_fetch(BOT_NAME, url)

    async def _load_robots(self, origin: str) -> None:
        if origin not in self._robots:
            parser: RobotFileParser | None = RobotFileParser()
            try:
                resp = await self._client.get(origin + "/robots.txt", headers={"User-Agent": BOT_NAME})
                if resp.status_code >= 400:
                    parser = None  # нет robots.txt — ограничений нет
                else:
                    parser.parse(resp.text.splitlines())
            except httpx.HTTPError:
                parser = None
            self._robots[origin] = parser

    async def _polite_wait(self, host: str) -> None:
        lock = self._host_locks.setdefault(host, asyncio.Lock())
        async with lock:
            wait = self._last_hit.get(host, 0.0) + self.per_host_delay - time.monotonic()
            if wait > 0:
                await self._sleep(wait)
            self._last_hit[host] = time.monotonic()

    async def get(self, url: str) -> str:
        if not await self._allowed(url):
            raise FetchError(url, "запрещено правилами robots.txt сайта")
        host = urlsplit(url).netloc
        last_error = "неизвестная ошибка"
        for attempt in range(self.max_retries + 1):
            await self._polite_wait(host)
            try:
                resp = await self._client.get(url, headers={"User-Agent": random.choice(USER_AGENTS)})
            except httpx.TimeoutException:
                last_error = "таймаут"
            except httpx.HTTPError as exc:
                last_error = f"сетевая ошибка: {exc.__class__.__name__}"
            else:
                if resp.status_code < 400:
                    return resp.text
                if resp.status_code not in RETRY_STATUSES:
                    raise FetchError(url, f"HTTP {resp.status_code}")
                last_error = f"HTTP {resp.status_code}"
                retry_after = resp.headers.get("Retry-After")
                if retry_after and retry_after.isdigit() and attempt < self.max_retries:
                    await self._sleep(min(int(retry_after), 60))
                    continue
            if attempt < self.max_retries:
                delay = min(2 ** attempt, 30) + random.uniform(0, 0.5)
                log.warning("%s: %s, повтор через %.1f с (%d/%d)", url, last_error, delay, attempt + 1, self.max_retries)
                await self._sleep(delay)
        raise FetchError(url, f"не удалось после {self.max_retries + 1} попыток: {last_error}")
