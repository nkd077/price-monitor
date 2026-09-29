"""Настройки: секреты и параметры из .env, список товаров из products.json."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent


class ConfigError(RuntimeError):
    """Понятная ошибка настройки вместо трассировки."""


@dataclass(frozen=True)
class Selectors:
    """CSS-селекторы для сайтов без микроразметки."""
    price: str
    title: str | None = None
    in_stock: str | None = None  # элемент есть на странице → товар в наличии


@dataclass(frozen=True)
class Product:
    name: str
    url: str
    target_price: float | None = None
    selectors: Selectors | None = None


@dataclass(frozen=True)
class Settings:
    telegram_token: str | None
    telegram_chat_id: str | None
    drop_alert_percent: float
    concurrency: int
    per_host_delay: float
    timeout: float
    max_retries: int
    respect_robots: bool
    db_path: Path
    google_sheet_id: str | None
    google_credentials: Path | None
    products: tuple[Product, ...] = field(default_factory=tuple)


def load_products(path: Path) -> tuple[Product, ...]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise ConfigError(f"Не найден {path}. Скопируйте products.example.json в products.json") from exc
    except json.JSONDecodeError as exc:
        raise ConfigError(f"{path}: ошибка JSON в строке {exc.lineno}: {exc.msg}") from exc
    products = []
    for i, item in enumerate(raw.get("products", [])):
        if not item.get("url", "").startswith(("http://", "https://")):
            raise ConfigError(f"products[{i}]: нужен url, начинающийся с http(s)://")
        sel = item.get("selectors")
        selectors = Selectors(price=sel["price"], title=sel.get("title"), in_stock=sel.get("in_stock")) if sel else None
        target = item.get("target_price")
        products.append(Product(name=item.get("name") or item["url"], url=item["url"],
                                target_price=float(target) if target is not None else None, selectors=selectors))
    if not products:
        raise ConfigError(f"{path}: список products пуст")
    return tuple(products)


def _float(name: str, default: float) -> float:
    try:
        return float(os.getenv(name, default))
    except ValueError as exc:
        raise ConfigError(f"{name}: нужно число") from exc


def load_settings(products_path: Path | None = None) -> Settings:
    load_dotenv(BASE_DIR / ".env")
    token = os.getenv("TELEGRAM_BOT_TOKEN") or None
    chat = os.getenv("TELEGRAM_CHAT_ID") or None
    if bool(token) != bool(chat):
        raise ConfigError("Для уведомлений нужны оба параметра: TELEGRAM_BOT_TOKEN и TELEGRAM_CHAT_ID")
    creds = os.getenv("GOOGLE_CREDENTIALS_FILE")
    sheet = os.getenv("GOOGLE_SHEET_ID") or None
    if sheet and not creds:
        raise ConfigError("Для выгрузки в Google Таблицу укажите GOOGLE_CREDENTIALS_FILE (ключ сервисного аккаунта)")
    return Settings(
        telegram_token=token, telegram_chat_id=chat,
        drop_alert_percent=_float("DROP_ALERT_PERCENT", 5.0),
        concurrency=int(_float("CONCURRENCY", 5)),
        per_host_delay=_float("PER_HOST_DELAY", 1.5),
        timeout=_float("REQUEST_TIMEOUT", 20.0),
        max_retries=int(_float("MAX_RETRIES", 3)),
        respect_robots=os.getenv("RESPECT_ROBOTS", "true").lower() != "false",
        db_path=Path(os.getenv("DB_PATH", str(BASE_DIR / "data" / "prices.db"))),
        google_sheet_id=sheet,
        google_credentials=Path(creds) if creds else None,
        products=load_products(products_path or Path(os.getenv("PRODUCTS_FILE", str(BASE_DIR / "products.json")))),
    )
