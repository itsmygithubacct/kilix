"""Currency and public-address answers: what is claimed, what is trusted,
what is cached, and the bounded child that does the only networking."""

import contextlib
import io
import json
import subprocess
import sys
import tempfile
import time
import unittest
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "config"))
sys.dont_write_bytecode = True

import kilix_lookup as lookup  # noqa: E402

RATES = {"USD": "1.1214", "JPY": "177.38", "GBP": "0.83545", "CHF": "0.9312", "CAD": "1.5501",
         "AUD": "1.7049", "SEK": "11.0435", "NOK": "11.6810", "DKK": "7.4605", "PLN": "4.2445",
         "CNY": "7.9821", "INR": "98.432", "KRW": "1621.55", "MXN": "20.4113"}


def ecb_xml(day=None, rates=RATES, extra=""):
    day = day or datetime.now(timezone.utc).date().isoformat()
    cubes = "".join(f"<Cube currency='{c}' rate='{r}'/>" for c, r in rates.items())
    return (f"<?xml version='1.0' encoding='UTF-8'?>{extra}"
            "<gesmes:Envelope xmlns:gesmes='http://www.gesmes.org/xml/2002-08-01' "
            "xmlns='http://www.ecb.int/vocabulary/2002-08-01/eurofxref'>"
            "<gesmes:subject>Reference rates</gesmes:subject>"
            f"<Cube><Cube time='{day}'>{cubes}</Cube></Cube></gesmes:Envelope>").encode()


CLAIMED = {
    "100 usd in eur": ("currency", "USD", "EUR", Decimal(100)),
    "convert 50 euros to dollars": ("currency", "EUR", "USD", Decimal(50)),
    "how much is 20 pounds in yen?": ("currency", "GBP", "JPY", Decimal(20)),
    "what is 100 usd in eur": ("currency", "USD", "EUR", Decimal(100)),
    "$5 in euros": ("currency", "USD", "EUR", Decimal(5)),
    "€20 to usd": ("currency", "EUR", "USD", Decimal(20)),
    "1,250.50 canadian dollars to us dollars": ("currency", "CAD", "USD", Decimal("1250.50")),
    "how many yen in a dollar": ("currency", "USD", "JPY", Decimal(1)),
    "how many euros is 100 dollars": ("currency", "USD", "EUR", Decimal(100)),
    "how much is a pound in euros": ("currency", "GBP", "EUR", Decimal(1)),
    "100 TRY in EUR": ("currency", "TRY", "EUR", Decimal(100)),
    "100 rub in usd": ("currency", "RUB", "USD", Decimal(100)),
    "100 kronor to eur": ("currency", "SEK", "EUR", Decimal(100)),
    "usd to eur": ("rate", "USD", "EUR", None),
    "USD/JPY": ("rate", "USD", "JPY", None),
    "exchange rate usd eur": ("rate", "USD", "EUR", None),
    "what's the exchange rate between the dollar and the euro?": ("rate", "USD", "EUR", None),
    "dollar to euro rate today": ("rate", "USD", "EUR", None),
}
ADDRESSES = {
    "what is my public ip": 4, "what's my external ip address?": 4, "show my public ip": 4,
    "what ip do websites see": 4, "what is my public ipv6 address": 6, "my wan ip": 4,
}
NOT_OURS = (
    "what is my ip", "what's my ip address", "show my ip", "what is my local ip", "5 miles in km",
    "convert 100 f to c", "5 pounds in kg", "convert this file to pdf", "100 in euros", "1 in a million",
    "what is the rate of fire", "close tab 2", "what is a dollar", "try to eur", "open the currency converter",
    "set the volume to 30", "what is the weather in Paris", "how do I change my ip address",
    "what is an ip address", "move 5 to the left", "pull up the exchange", "",
)


class ClaimTests(unittest.TestCase):
    def test_currency_and_rate_questions(self):
        for text, (kind, base, quote, amount) in CLAIMED.items():
            with self.subTest(text=text):
                self.assertEqual(lookup.ask_query(text), lookup.Query(kind, base, quote, amount))

    def test_public_address_questions(self):
        for text, version in ADDRESSES.items():
            with self.subTest(text=text):
                self.assertEqual(lookup.ask_query(text), lookup.Query("address", version=version))

    def test_everything_else_is_left_alone_without_networking(self):
        with patch.object(lookup.subprocess, "Popen", side_effect=AssertionError("networked")):
            for text in NOT_OURS:
                with self.subTest(text=text):
                    self.assertIsNone(lookup.ask_query(text))
                    self.assertEqual(lookup.main(["--ask", text]), lookup.NOT_LOOKUP)

    def test_ambiguous_names_and_bad_amounts_are_refused_with_a_reason(self):
        for text, reason in (("50 krone in usd", "Danish krone"), ("0 usd in eur", "above zero"),
                             ("100 krona to eur", "Icelandic")):
            with self.subTest(text=text), patch.object(lookup.subprocess, "Popen",
                                                       side_effect=AssertionError("networked")):
                with self.assertRaisesRegex(ValueError, reason):
                    lookup.ask_query(text)
                err = io.StringIO()
                with contextlib.redirect_stderr(err):
                    self.assertEqual(lookup.main(["--ask", text]), 1)
                self.assertIn(reason, err.getvalue())


class EcbFileTests(unittest.TestCase):
    today = datetime.now(timezone.utc).date()

    def test_the_daily_file_is_read(self):
        record = lookup.parse_ecb(ecb_xml(), self.today)
        self.assertEqual(record["date"], self.today.isoformat())
        self.assertEqual(record["rates"], RATES)

    def test_untrusted_files_are_refused(self):
        two_days = ecb_xml().replace(b"</Cube></Cube>", b"</Cube><Cube time='2026-01-01'/></Cube>")
        cases = {
            "declaration": ecb_xml(extra="<!DOCTYPE x [<!ENTITY a 'b'>]>"),
            "two days": two_days,
            "stale": ecb_xml(day=(self.today - timedelta(days=11)).isoformat()),
            "future": ecb_xml(day=(self.today + timedelta(days=3)).isoformat()),
            "no date": ecb_xml(day="yesterday"),
            "euro listed": ecb_xml(rates={**RATES, "EUR": "1"}),
            "negative": ecb_xml(rates={**RATES, "USD": "-1.1"}),
            "word": ecb_xml(rates={**RATES, "USD": "lots"}),
            "bad code": ecb_xml(rates={**RATES, "usd": "1.1"}),
            "too few": ecb_xml(rates=dict(list(RATES.items())[:5])),
            "not xml": b"<html>maintenance</html",
        }
        for name, data in cases.items():
            with self.subTest(name), self.assertRaises(ValueError):
                lookup.parse_ecb(data, self.today)


class AnswerTests(unittest.TestCase):
    def setUp(self):
        self.record = lookup.parse_ecb(ecb_xml(day="2026-11-10"), date(2026, 11, 10))

    def answer(self, text):
        return lookup.currency_answer(lookup.ask_query(text), self.record)

    def test_amounts_rates_and_the_ecb_is_named(self):
        text = self.answer("100 usd in eur")
        self.assertEqual(text.splitlines()[:2], ["100 USD = 89.17 EUR", "1 USD = 0.891742 EUR; 1 EUR = 1.1214 USD"])
        self.assertIn("ECB euro reference rates of Tue 10 Nov 2026. Source: ECB statistics.", text)
        self.assertIn("for information only", text)
        self.assertNotIn("Worked through the euro", text)

    def test_other_pairs_are_worked_through_the_euro_and_say_so(self):
        text = self.answer("how much is 20 pounds in yen")
        self.assertEqual(text.splitlines()[0], "20 GBP = 4,246 JPY")  # 20 * 177.38 / 0.83545
        self.assertIn("Worked through the euro: 1 EUR = 0.83545 GBP and 177.38 JPY.", text)
        self.assertEqual(self.answer("1,250.50 cad in usd").splitlines()[0], "1,250.50 CAD = 904.66 USD")
        self.assertTrue(self.answer("usd to eur").startswith("1 USD = 0.891742 EUR"))

    def test_currencies_without_an_ecb_rate_say_why(self):
        with self.assertRaisesRegex(ValueError, "suspended its rouble") as caught:
            self.answer("100 rub in usd")
        self.assertIn("USD", str(caught.exception))
        with self.assertRaisesRegex(ValueError, "publishes no reference rate for ARS"):
            self.answer("100 ars in usd")
        # In the vocabulary, but missing from this day's file.
        with self.assertRaisesRegex(ValueError, "publishes no reference rate for ZAR"):
            self.answer("100 zar in usd")


class CacheTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.path = Path(temp.name, "cache", "ask", "rates.json")
        for name, value in (("_cache_path", lambda: self.path),):
            patcher = patch.object(lookup, name, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.child = json.dumps(lookup.parse_ecb(ecb_xml(), datetime.now(timezone.utc).date()))

    def ask(self, text, fetched=None):
        fetch = Mock(return_value=self.child) if fetched is None else fetched
        with patch.object(lookup, "fetch", fetch):
            return lookup.answer(lookup.ask_query(text)), fetch

    def test_the_rates_are_fetched_once_a_day(self):
        _, fetch = self.ask("100 usd in eur")
        fetch.assert_called_once_with("ecb")
        self.assertTrue(self.path.exists())
        _, again = self.ask("usd to jpy", fetched=Mock(side_effect=AssertionError("fetched again")))
        self.assertEqual(again.call_count, 0)
        stored = json.loads(self.path.read_text())
        stored["fetched"] -= lookup.CACHE_SECONDS + 1
        self.path.write_text(json.dumps(stored))
        _, stale = self.ask("usd to jpy")
        stale.assert_called_once_with("ecb")

    def test_a_damaged_or_future_cache_is_not_trusted(self):
        self.ask("100 usd in eur")
        good = json.loads(self.path.read_text())
        for damage in ({**good, "rates": {**good["rates"], "USD": "abc"}}, {**good, "schema": "other"},
                       {**good, "fetched": time.time() + 3600}, {**good, "date": "2026-13-40"}, "nope"):
            with self.subTest(damage=str(damage)[:40]):
                self.path.write_text(json.dumps(damage))
                _, fetch = self.ask("usd to eur")
                fetch.assert_called_once_with("ecb")

    def test_an_unwritable_cache_still_answers(self):
        self.path.parent.parent.mkdir(parents=True)
        self.path.parent.write_text("a file where the folder should be")
        text, _ = self.ask("100 usd in eur")
        self.assertTrue(text.startswith("100 USD = "))

    def test_a_bad_child_record_is_not_answered_or_cached(self):
        with self.assertRaises(ValueError):
            self.ask("100 usd in eur", fetched=Mock(return_value='{"schema": "x"}'))
        self.assertFalse(self.path.exists())


class AddressTests(unittest.TestCase):
    def test_a_public_address_is_answered_and_the_service_named(self):
        text = lookup.address_answer("8.8.4.4\n", 4)
        self.assertEqual(text.splitlines()[0], "Public IPv4 address: 8.8.4.4")
        self.assertIn("api.ipify.org (ipify)", text)
        self.assertIn("Public IPv6 address: 2001:4860:4860::8888",
                      lookup.address_answer("2001:4860:4860::8888", 6))

    def test_untrusted_replies_are_refused(self):
        for reply, version in (("192.168.1.10", 4), ("10.0.0.1", 4), ("127.0.0.1", 4), ("::1", 6),
                               ("8.8.4.4", 6), ("2001:4860:4860::8888", 4), ("<html>", 4), ("", 4)):
            with self.subTest(reply=reply), self.assertRaises(ValueError):
                lookup.address_answer(reply, version)


class ChildTests(unittest.TestCase):
    def test_timeout_and_cancel_kill_and_reap_the_only_child(self):
        for failure, expected in ((subprocess.TimeoutExpired("lookup", 15), lookup.NoAnswer),
                                  (KeyboardInterrupt(), KeyboardInterrupt)):
            child = Mock()
            child.communicate.side_effect = [failure, (b"", b"")]
            child.poll.return_value = None
            with patch.object(lookup.subprocess, "Popen", return_value=child) as popen:
                with self.assertRaises(expected):
                    lookup.fetch("address", 4)
            popen.assert_called_once()
            self.assertEqual(popen.call_args.args[0][-4:], ["--worker", "address", "--ip-version", "4"])
            child.kill.assert_called_once()
            self.assertEqual(child.communicate.call_args_list[0].kwargs, {"timeout": lookup.FETCH_TIMEOUT})

    def test_deadline_reaps_a_real_stalled_process(self):
        popen = subprocess.Popen
        children = []

        def stalled(_argv, **kwargs):
            child = popen([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)
            children.append(child)
            return child

        started = time.monotonic()
        with patch.object(lookup.subprocess, "Popen", side_effect=stalled), \
                patch.object(lookup, "FETCH_TIMEOUT", 0.2):
            with self.assertRaisesRegex(ValueError, "longer than"):
                lookup.fetch("ecb")
        self.assertLess(time.monotonic() - started, 3)
        self.assertEqual(len(children), 1)
        self.assertIsNotNone(children[0].returncode)
        self.assertTrue(children[0].stdout.closed)

    def test_the_worker_names_the_service_on_failure(self):
        cases = (
            ("ecb", 4, HTTPError(lookup.ECB_URL, 429, "busy", {}, None), "The ECB is busy"),
            ("ecb", 4, URLError("down"), "The ECB is unreachable"),
            ("address", 4, HTTPError("u", 503, "x", {}, None), "ipify is unavailable (HTTP 503)"),
            ("address", 6, URLError("no route"), "may have no IPv6 address"),
            ("ecb", 4, ValueError("more than an answer should hold"), "more than an answer"),
        )
        for kind, version, error, message in cases:
            err = io.StringIO()
            with self.subTest(message=message), patch.object(lookup, "_get", side_effect=error), \
                    contextlib.redirect_stderr(err), contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(lookup.worker(kind, version), 1)
            self.assertIn(message, err.getvalue())

    def test_the_worker_asks_only_the_named_service(self):
        for kind, version, url in (("ecb", 4, lookup.ECB_URL), ("address", 4, "https://api.ipify.org"),
                                   ("address", 6, "https://api6.ipify.org")):
            reply = ecb_xml() if kind == "ecb" else b"8.8.4.4"
            out = io.StringIO()
            with self.subTest(url=url), patch.object(lookup, "_get", return_value=reply) as get, \
                    contextlib.redirect_stdout(out):
                self.assertEqual(lookup.worker(kind, version), 0)
            self.assertEqual(get.call_args.args[0], url)
            self.assertEqual(get.call_args.args[1], lookup.MAX_XML if kind == "ecb" else lookup.MAX_ADDRESS)


if __name__ == "__main__":
    unittest.main()
