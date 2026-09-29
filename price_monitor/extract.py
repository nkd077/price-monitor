"""Извлечение цены, названия и наличия со страницы товара.

Порядок: микроразметка schema.org (JSON-LD) → microdata/meta-теги → CSS-селекторы из настроек.
Большинство интернет-магазинов отдают JSON-LD для поисковиков, поэтому парсер работает
на новых сайтах без настройки.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from typing import Any

from bs4 import BeautifulSoup

from .config import Selectors

log = logging.getLogger(__name__)

_NUM_RE = re.compile(r"\d[\d\s  .,']*")


@dataclass(frozen=True)
class Offer:
    price: float
    currency: str | None
    title: str | None
    in_stock: bool | None
    source: str  # jsonld | meta | css


class ExtractError(ValueError):
    """Цену на странице найти не удалось."""


def parse_price(text: str | float | int | None) -> float | None:
    """'1 299,00 ₽' → 1299.0; '12,345.50' → 12345.5; '1.299' → 1299.0 (точка как разделитель тысяч)."""
    if text is None:
        return None
    if isinstance(text, (int, float)):
        return float(text)
    m = _NUM_RE.search(text)
    if not m:
        return None
    raw = re.sub(r"[\s  ']", "", m.group()).rstrip(".,")
    if "," in raw and "." in raw:
        # последний из разделителей — десятичный
        dec = "," if raw.rfind(",") > raw.rfind(".") else "."
        raw = raw.replace("." if dec == "," else ",", "").replace(dec, ".")
    elif "," in raw:
        head, _, tail = raw.rpartition(",")
        raw = raw.replace(",", "") if len(tail) == 3 and head else raw.replace(",", ".")
    elif raw.count(".") >= 1:
        head, _, tail = raw.rpartition(".")
        if len(tail) == 3 and head:  # 1.299 — тысячи
            raw = raw.replace(".", "")
    try:
        return float(raw)
    except ValueError:
        return None


def _availability(value: Any) -> bool | None:
    if not value:
        return None
    v = str(value).lower()
    if "instock" in v or "in_stock" in v or "limitedavailability" in v or "preorder" in v:
        return True
    if "outofstock" in v or "soldout" in v or "discontinued" in v:
        return False
    return None


def _iter_jsonld(soup: BeautifulSoup):
    for tag in soup.find_all("script", attrs={"type": "application/ld+json"}):
        try:
            data = json.loads(tag.string or tag.get_text() or "")
        except json.JSONDecodeError:
            continue
        stack = [data]
        while stack:
            node = stack.pop()
            if isinstance(node, list):
                stack.extend(node)
            elif isinstance(node, dict):
                yield node
                for key in ("@graph", "mainEntity", "itemListElement"):
                    if key in node:
                        stack.append(node[key])


def _from_jsonld(soup: BeautifulSoup) -> Offer | None:
    for node in _iter_jsonld(soup):
        types = node.get("@type")
        types = types if isinstance(types, list) else [types]
        if "Product" not in types:
            continue
        offers = node.get("offers")
        offers = offers if isinstance(offers, list) else [offers]
        for off in offers:
            if not isinstance(off, dict):
                continue
            price = parse_price(off.get("price") or off.get("lowPrice"))
            if price is None and isinstance(off.get("priceSpecification"), dict):
                price = parse_price(off["priceSpecification"].get("price"))
            if price is not None:
                return Offer(price, off.get("priceCurrency"), node.get("name"), _availability(off.get("availability")), "jsonld")
    return None


def _from_meta(soup: BeautifulSoup) -> Offer | None:
    tag = (soup.find("meta", attrs={"property": "product:price:amount"})
           or soup.find("meta", attrs={"itemprop": "price"})
           or soup.find(attrs={"itemprop": "price"}))
    if tag is None:
        return None
    price = parse_price(tag.get("content") or tag.get_text())
    if price is None:
        return None
    cur = soup.find("meta", attrs={"property": "product:price:currency"}) or soup.find(attrs={"itemprop": "priceCurrency"})
    avail = soup.find(attrs={"itemprop": "availability"})
    title = soup.find("meta", attrs={"property": "og:title"})
    return Offer(price, cur.get("content") if cur else None, title.get("content") if title else None,
                 _availability(avail.get("href") or avail.get("content")) if avail else None, "meta")


def _from_css(soup: BeautifulSoup, sel: Selectors) -> Offer | None:
    node = soup.select_one(sel.price)
    if node is None:
        return None
    price = parse_price(node.get("content") or node.get_text(" ", strip=True))
    if price is None:
        return None
    title_node = soup.select_one(sel.title) if sel.title else None
    in_stock = (soup.select_one(sel.in_stock) is not None) if sel.in_stock else None
    return Offer(price, None, title_node.get_text(" ", strip=True) if title_node else None, in_stock, "css")


def extract_offer(html: str, selectors: Selectors | None = None) -> Offer:
    soup = BeautifulSoup(html, "lxml")
    offer = (_from_css(soup, selectors) if selectors else None) or _from_jsonld(soup) or _from_meta(soup)
    if offer is None:
        raise ExtractError("цена не найдена: нет микроразметки schema.org — задайте CSS-селекторы в products.json")
    if offer.title is None:
        h1 = soup.find("h1") or soup.find("title")
        if h1:
            offer = Offer(offer.price, offer.currency, h1.get_text(" ", strip=True), offer.in_stock, offer.source)
    return offer
