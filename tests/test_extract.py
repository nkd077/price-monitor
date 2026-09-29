import unittest
from pathlib import Path

from price_monitor.config import Selectors
from price_monitor.extract import ExtractError, extract_offer, parse_price

FIX = Path(__file__).parent / "fixtures"


class ParsePriceTest(unittest.TestCase):
    def test_formats(self) -> None:
        cases = {
            "1 299 ₽": 1299.0, "1 299,00 ₽": 1299.0, "1 870,50 ₽": 1870.5, "12,345.50": 12345.5,
            "12.345,50": 12345.5, "1.299": 1299.0, "4990.00": 4990.0, "от 990 руб.": 990.0, "$19.99": 19.99,
            "2,5": 2.5, "1,299": 1299.0, 4990: 4990.0, "цена по запросу": None, None: None,
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_price(raw), expected)


class ExtractTest(unittest.TestCase):
    def test_jsonld_graph(self) -> None:
        o = extract_offer((FIX / "jsonld_shop.html").read_text(encoding="utf-8"))
        self.assertEqual((o.price, o.currency, o.in_stock, o.source), (4990.0, "RUB", True, "jsonld"))
        self.assertEqual(o.title, "Кроссовки беговые Runner 3")

    def test_microdata_out_of_stock(self) -> None:
        o = extract_offer((FIX / "microdata_shop.html").read_text(encoding="utf-8"))
        self.assertEqual((o.price, o.currency, o.in_stock, o.source), (1290.0, "RUB", False, "meta"))
        self.assertEqual(o.title, "Термос 1 л стальной")

    def test_css_selectors(self) -> None:
        sel = Selectors(price=".price__current", title=".product-title", in_stock=".buy-button")
        o = extract_offer((FIX / "plain_shop.html").read_text(encoding="utf-8"), sel)
        self.assertEqual((o.price, o.title, o.in_stock, o.source), (1870.5, "Лампа настольная LED", True, "css"))

    def test_no_price_is_clear_error(self) -> None:
        with self.assertRaises(ExtractError):
            extract_offer((FIX / "plain_shop.html").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
