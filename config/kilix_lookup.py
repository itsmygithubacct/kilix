"""Currency and public-address answers for Ask, from two outside services.

A service is contacted only when a question asks for it, and every answer
names the service it came from:

- Currency: the European Central Bank's daily euro reference rates
  (eurofxref-daily.xml). The rates are cached for a day, and the answer
  states their date. The ECB publishes them for information only.
- Public address: ipify (api.ipify.org, api6.ipify.org). The service sees
  the address by its nature; the answer says which service was asked.

Only the bounded child contacts a service; cancelling the command reaps it.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

NOT_LOOKUP = 3
FETCH_TIMEOUT = 15          # the child's whole budget
REQUEST_TIMEOUT = 8         # one HTTP request
CACHE_SECONDS = 24 * 3600
ECB_URL = "https://www.ecb.europa.eu/stats/eurofxref/eurofxref-daily.xml"
ADDRESS_URLS = {4: "https://api.ipify.org", 6: "https://api6.ipify.org"}
MAX_XML = 64 * 1024
MAX_ADDRESS = 64
USER_AGENT = "Kilix-Ask/0.2.4"
CACHE_SCHEMA = "kilix.ask.ecb-rates/v1"
ECB_NOTICE = ("Reference rates are published for information only; "
              "the ECB discourages using them for transactions.")

# Names people use, by ISO code. Longer names are tried first, so
# "canadian dollars" is CAD and "dollars" alone is USD.
NAMES = {
    "EUR": ("euro", "euros", "€"),
    "USD": ("dollar", "dollars", "us dollar", "us dollars", "american dollar", "american dollars",
            "buck", "bucks", "$"),
    "JPY": ("yen", "japanese yen", "¥"),
    "GBP": ("pound", "pounds", "british pound", "british pounds", "pound sterling", "pounds sterling",
            "sterling", "quid", "£"),
    "CHF": ("franc", "francs", "swiss franc", "swiss francs"),
    "CAD": ("canadian dollar", "canadian dollars"),
    "AUD": ("australian dollar", "australian dollars", "aussie dollar", "aussie dollars"),
    "NZD": ("new zealand dollar", "new zealand dollars"),
    "HKD": ("hong kong dollar", "hong kong dollars"),
    "SGD": ("singapore dollar", "singapore dollars"),
    "CNY": ("yuan", "chinese yuan", "renminbi", "rmb"),
    "INR": ("rupee", "rupees", "indian rupee", "indian rupees", "₹"),
    "KRW": ("won", "korean won", "south korean won", "₩"),
    "MXN": ("peso", "pesos", "mexican peso", "mexican pesos"),
    "PHP": ("philippine peso", "philippine pesos"),
    "BRL": ("real", "reais", "brazilian real", "brazilian reais"),
    "SEK": ("swedish krona", "swedish kronor", "kronor"),
    "NOK": ("norwegian krone", "norwegian kroner"),
    "DKK": ("danish krone", "danish kroner"),
    "ISK": ("icelandic krona", "icelandic kronur"),
    "PLN": ("zloty", "zlotys", "złoty", "polish zloty"),
    "CZK": ("koruna", "czech koruna", "czech crown", "czech crowns"),
    "HUF": ("forint", "forints", "hungarian forint"),
    "RON": ("leu", "lei", "romanian leu"),
    "TRY": ("lira", "liras", "turkish lira", "₺"),
    "ZAR": ("rand", "rands", "south african rand"),
    "ILS": ("shekel", "shekels", "israeli shekel", "new shekel"),
    "THB": ("baht", "thai baht"),
    "IDR": ("rupiah", "indonesian rupiah"),
    "MYR": ("ringgit", "malaysian ringgit"),
}
ECB_CODES = frozenset(NAMES)
# Codes people ask about that have no ECB reference rate. They are
# recognised so the answer can say so instead of passing the question on.
OTHER_CODES = {
    "RUB": "The ECB suspended its rouble reference rate in March 2022.",
    "BTC": "Bitcoin is not a currency the ECB publishes a rate for.",
    "ETH": "Ether is not a currency the ECB publishes a rate for.",
    "BGN": "Bulgaria adopted the euro, so the ECB no longer publishes a lev rate.",
}
for _code in ("ARS", "CLP", "COP", "PEN", "EGP", "NGN", "KES", "PKR", "BDT", "SAR", "AED", "QAR",
              "KWD", "TWD", "VND", "UAH", "KZT", "MAD", "LKR", "NPR"):
    OTHER_CODES.setdefault(_code, "")
AMBIGUOUS = {"krone": "Danish krone (DKK) or Norwegian krone (NOK)",
             "kroner": "Danish kroner (DKK) or Norwegian kroner (NOK)",
             "krona": "Swedish krona (SEK) or Icelandic krona (ISK)",
             "crown": "Czech crown (CZK) or a krone or krona",
             "crowns": "Czech crowns (CZK) or kroner or kronor"}
ZERO_DECIMALS = frozenset(("JPY", "KRW", "ISK", "IDR"))
# Codes that are also English words count only when written in capitals:
# "100 TRY in EUR" is Turkish lira, "try to" is not.
WORD_CODES = frozenset(("TRY", "PEN", "MAD", "COP"))

_WORDS = {name: code for code, names in NAMES.items() for name in names}
_WORDS.update({code.lower(): code for code in (*ECB_CODES, *OTHER_CODES)})
_WORDS.update({name: "?" + name for name in AMBIGUOUS})
_CUR = "|".join(re.escape(w) for w in sorted(_WORDS, key=len, reverse=True))
_TOKEN = rf"(?:(?<![\w$€£¥₹₩₺])(?:{_CUR})(?![\w$€£¥₹₩₺]))"
_SYM = r"[$€£¥₹₩₺]"
_NUM = r"\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+"
_AMOUNT = rf"(?P<pre>{_SYM})?\s*(?P<amount>{_NUM}|an?|one)(?:\s*(?P<post>{_SYM}))?"
_FROM = rf"(?:\s*(?P<base>{_TOKEN}))?"
_TO = rf"(?P<quote>{_TOKEN})"
_INTO = r"(?:in|to|into|as|in terms of|converted to|worth in|equals how many|is how many)"
_CURRENCY_FORMS = (
    rf"(?:(?:please )?(?:convert|change|exchange|turn) )?{_AMOUNT}{_FROM} {_INTO} {_TO}",
    rf"(?:how much|what)(?: is| are| would be|'s|s| will) {_AMOUNT}{_FROM}(?: be)?(?: worth)? "
    rf"(?:in|to|into|worth in) {_TO}",
    rf"how many {_TO} (?:is|are|in|for|to|per|make|do i get for|would i get for|equals?) "
    rf"(?:(?:the|a|an|one) (?P<base2>{_TOKEN})|{_AMOUNT}{_FROM})",
)
_RATE_FORMS = (
    rf"(?:what(?:'s|s| is) )?(?:the )?(?:current |today's |latest )?(?:exchange |conversion |currency )?rate "
    rf"(?:of |from |for |between )?(?:the )?(?P<base>{_TOKEN}) (?:to|and|vs|versus|into|in|against|/) "
    rf"(?:the )?(?P<quote>{_TOKEN})",
    rf"(?:(?:the|an?) )?(?:exchange |conversion )?rate (?P<base>{_TOKEN}) (?P<quote>{_TOKEN})",
    rf"(?:exchange rate )?(?P<base>{_TOKEN}) ?(?:to|vs|versus|/|against) ?(?P<quote>{_TOKEN})"
    rf"(?: (?:exchange |conversion )?rate)?(?: today| now)?",
    rf"(?:how much is|what is|what's|whats) (?:the|a|an|one) (?P<base>{_TOKEN}) (?:worth )?(?:in|to|against) "
    rf"(?P<quote>{_TOKEN})",
)
_ADDRESS_FORMS = (
    r"(?:(?:what(?:'s|s| is)|show(?: me)?|tell me|get|check|find|give me|look up)\s+)?"
    r"(?:my |our |the |this (?:computer|machine|laptop)'s )?"
    r"(?:public|external|internet|wan|outside|outward[- ]facing|outward|global|real) "
    r"ip(?:v(?P<version>[46]))?(?: address)?(?: is)?",
    r"what ip(?:v(?P<version>[46]))?(?: address)? (?:does|do|will) (?:the internet|websites|other sites|"
    r"the outside world|sites|servers) see(?: me as| for me)?",
    r"what(?:'s|s| is) my ip(?:v(?P<version>[46]))?(?: address)? (?:on|to) the internet",
)


@dataclass(frozen=True)
class Query:
    kind: str                       # "currency", "rate" or "address"
    base: str = ""
    quote: str = ""
    amount: Decimal | None = None
    version: int = 4


class NoAnswer(ValueError):
    """A question Ask recognised but cannot answer; the message says why."""


def _clean(text: str) -> str:
    text = " ".join(text.replace("’", "'").split())
    text = re.sub(r"[?!.]+$", "", text).strip()
    text = re.sub(r"^(?:please|hey|ok|okay)[, ]+|,?\s+please$", "", text, flags=re.I).strip()
    return text


def _code(token: str | None) -> str:
    """The ISO code for a matched token; "" when it is only an English word."""
    if not token:
        return ""
    code = _WORDS[token.casefold()]
    if code in WORD_CODES and token != code:
        return ""
    if code.startswith("?"):
        word = code[1:]
        raise NoAnswer(f'"{word}" could be the {AMBIGUOUS[word]}. Name the country or the code.')
    return code


def _amount(match: re.Match) -> Decimal | None:
    raw = match.groupdict().get("amount")
    if raw is None:
        return None
    if raw.casefold() in ("a", "an", "one"):
        return Decimal(1)
    try:
        value = Decimal(raw.replace(",", ""))
    except InvalidOperation:
        return None
    if not value.is_finite() or value <= 0 or value >= Decimal("1e15"):
        raise NoAnswer("Give an amount above zero and below a thousand trillion.")
    return value


def ask_query(text: str) -> Query | None:
    """Recognise a complete currency or public-address question, or None."""
    text = _clean(text)
    if not text or len(text) > 160:
        return None
    for pattern in _ADDRESS_FORMS:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            return Query("address", version=int(match["version"] or 4))
    for pattern in _CURRENCY_FORMS:
        match = re.fullmatch(pattern, text, re.I)
        if not match:
            continue
        groups = match.groupdict()
        symbols = {g for g in (groups.get("pre"), groups.get("post")) if g}
        if len(symbols) > 1:
            return None
        base_token = groups.get("base") or groups.get("base2") or (symbols.pop() if symbols else None)
        if not base_token:
            return None  # "100 in euros": which currency is 100?
        base, quote = _code(base_token), _code(groups["quote"])
        if not base or not quote:
            return None
        if groups.get("base") and symbols and _code(symbols.pop()) != base:
            return None  # "$100 euros in yen"
        return Query("currency", base, quote, _amount(match) if not groups.get("base2") else Decimal(1))
    for pattern in _RATE_FORMS:
        match = re.fullmatch(pattern, text, re.I)
        if match:
            base, quote = _code(match["base"]), _code(match["quote"])
            return Query("rate", base, quote) if base and quote else None
    return None


# --- the ECB file ------------------------------------------------------------

def _get(url: str, limit: int, accept: str) -> bytes:
    request = Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
        data = response.read(limit + 1)
    if len(data) > limit:
        raise ValueError("The service sent more than an answer should hold.")
    return data


def parse_ecb(data: bytes, today: date) -> dict:
    """The validated rates record from eurofxref-daily.xml."""
    if b"<!DOCTYPE" in data or b"<!ENTITY" in data:
        raise ValueError("The ECB file has a document type declaration; it is not used.")
    import xml.etree.ElementTree as ElementTree
    try:
        root = ElementTree.fromstring(data)
    except ElementTree.ParseError as error:
        raise ValueError("The ECB file could not be read.") from error
    days = [e for e in root.iter() if e.tag.endswith("Cube") and "time" in e.attrib]
    if len(days) != 1:
        raise ValueError("The ECB file does not hold one day of rates.")
    stamp = days[0].attrib["time"]
    try:
        day = date.fromisoformat(stamp)
    except ValueError as error:
        raise ValueError("The ECB file has no valid date.") from error
    if not today - timedelta(days=10) <= day <= today + timedelta(days=1):
        raise ValueError(f"The ECB file is dated {stamp}, too far from today to use.")
    rates = {}
    for cube in days[0]:
        code, rate = cube.attrib.get("currency", ""), cube.attrib.get("rate", "")
        if not re.fullmatch(r"[A-Z]{3}", code) or code == "EUR" or code in rates:
            raise ValueError("The ECB file lists an invalid currency.")
        try:
            value = Decimal(rate)
        except InvalidOperation as error:
            raise ValueError(f"The ECB rate for {code} is not a number.") from error
        if not value.is_finite() or not Decimal("0.00001") <= value <= Decimal("10000000"):
            raise ValueError(f"The ECB rate for {code} is out of range.")
        rates[code] = rate
    if not 10 <= len(rates) <= 60:
        raise ValueError("The ECB file lists too few or too many currencies.")
    return {"schema": CACHE_SCHEMA, "date": stamp, "rates": rates}


def _cache_path() -> Path:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    from kilix_sdk.paths import cache_dir
    return Path(cache_dir()) / "ask" / "ecb-eurofxref-daily.json"


def read_cache(now: float) -> dict | None:
    try:
        record = json.loads(_cache_path().read_text(encoding="utf-8"))
        fetched = record["fetched"]
        if record.get("schema") != CACHE_SCHEMA or not isinstance(fetched, (int, float)) \
                or not now - CACHE_SECONDS <= fetched <= now + 300:
            return None
        checked = parse_record(record, datetime.fromtimestamp(fetched, timezone.utc).date())
        return {**checked, "fetched": fetched}
    except (OSError, ValueError, KeyError, TypeError):
        return None


def write_cache(record: dict, now: float) -> None:
    path = _cache_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.{os.getpid()}")
        temporary.write_text(json.dumps({**record, "fetched": now}, sort_keys=True), encoding="utf-8")
        os.replace(temporary, path)
    except OSError:
        pass  # an unwritable cache only costs the next question a fetch


def parse_record(record: object, today: date) -> dict:
    """Validate a rates record from the child or the cache."""
    if not isinstance(record, dict) or record.get("schema") != CACHE_SCHEMA \
            or not isinstance(record.get("rates"), dict) or not isinstance(record.get("date"), str):
        raise ValueError("The rates record is not usable.")
    xml = "".join(f'<Cube currency="{c}" rate="{r}"/>' for c, r in record["rates"].items()
                  if isinstance(c, str) and isinstance(r, str) and re.fullmatch(r"[A-Z]{3}", c)
                  and re.fullmatch(r"\d+(?:\.\d+)?", r))
    if xml.count("<Cube ") != len(record["rates"]):
        raise ValueError("The rates record is not usable.")
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", record["date"]):
        raise ValueError("The rates record is not usable.")
    return parse_ecb(f'<r><Cube><Cube time="{record["date"]}">{xml}</Cube></Cube></r>'.encode(), today)


# --- answers -----------------------------------------------------------------

def _per_euro(rates: dict, code: str) -> Decimal:
    if code == "EUR":
        return Decimal(1)
    if code not in rates:
        reason = OTHER_CODES.get(code) or f"The ECB publishes no reference rate for {code}."
        listed = textwrap.fill(", ".join(sorted(("EUR", *rates))) + ".", 76,
                               initial_indent="  ", subsequent_indent="  ")
        raise NoAnswer(f"{reason} Its rates cover:\n{listed}")
    return Decimal(rates[code])


def _significant(value: Decimal, digits: int = 6) -> str:
    if value == 0:
        return "0"
    exponent = value.adjusted() - digits + 1
    text = format(value.quantize(Decimal(1).scaleb(exponent), rounding=ROUND_HALF_EVEN), "f")
    return text.rstrip("0").rstrip(".") if "." in text else text


def _given(value: Decimal, code: str) -> str:
    """An amount as typed: 100 stays 100, 1250.5 stays 1,250.5."""
    text = f"{value.normalize():,f}" if value == value.to_integral_value() else f"{value:,f}"
    return f"{text} {code}"


def _money(value: Decimal, code: str) -> str:
    places = Decimal(1) if code in ZERO_DECIMALS else Decimal("0.01")
    rounded = value.quantize(places, rounding=ROUND_HALF_EVEN)
    if rounded == 0:
        return f"{_significant(value, 3)} {code}"
    return f"{rounded:,} {code}"


def currency_answer(query: Query, record: dict) -> str:
    rates = record["rates"]
    base, quote = _per_euro(rates, query.base), _per_euro(rates, query.quote)
    rate = quote / base                           # QUOTE per one BASE
    day = date.fromisoformat(record["date"]).strftime("%a %-d %b %Y")
    lines = []
    if query.kind == "currency":
        amount = query.amount or Decimal(1)
        lines.append(f"{_given(amount, query.base)} = {_money(amount * rate, query.quote)}")
    lines.append(f"1 {query.base} = {_significant(rate)} {query.quote}; "
                 f"1 {query.quote} = {_significant(1 / rate)} {query.base}")
    if "EUR" not in (query.base, query.quote) and query.base != query.quote:
        lines.append(f"Worked through the euro: 1 EUR = {rates[query.base]} {query.base} "
                     f"and {rates[query.quote]} {query.quote}.")
    lines.append(f"Computed from the ECB euro reference rates of {day}. Source: ECB statistics.")
    lines.append(ECB_NOTICE)
    return "\n".join(lines)


def address_answer(text: str, version: int) -> str:
    raw = text.strip()
    try:
        address = ipaddress.ip_address(raw)
    except ValueError as error:
        raise ValueError("ipify did not answer with an address.") from error
    if address.version != version:
        raise ValueError(f"ipify answered with an IPv{address.version} address to an IPv{version} question.")
    if not address.is_global:
        raise ValueError(f"ipify answered with {address}, which is not a public address; it is not used.")
    host = ADDRESS_URLS[version].removeprefix("https://")
    return (f"Public IPv{version} address: {address}\n"
            f"Asked {host} (ipify), which sees this address on every request it gets.")


# --- the bounded child ---------------------------------------------------------

def worker(kind: str, version: int) -> int:
    """Fetch one thing and print it raw; the parent validates and answers."""
    try:
        if kind == "ecb":
            record = parse_ecb(_get(ECB_URL, MAX_XML, "application/xml, text/xml"),
                               datetime.now(timezone.utc).date())
            print(json.dumps(record))
        else:
            print(_get(ADDRESS_URLS[version], MAX_ADDRESS, "text/plain").decode("ascii", "replace").strip())
        return 0
    except HTTPError as error:
        message = "busy; try again later." if error.code == 429 else f"unavailable (HTTP {error.code})."
    except (URLError, OSError, TimeoutError):
        message = ("unreachable over IPv6. This connection may have no IPv6 address, or it is offline."
                   if kind == "address" and version == 6 else "unreachable: offline, or the request timed out.")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        return 1
    service = "The ECB" if kind == "ecb" else "ipify"
    print(f"{service} is {message}", file=sys.stderr)
    return 1


def fetch(kind: str, version: int = 4) -> str:
    """Run the child and return what it printed, or raise with its message."""
    child = None
    try:
        child = subprocess.Popen(
            [sys.executable, "-B", str(Path(__file__).resolve()), "--worker", kind, "--ip-version", str(version)],
            stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        out, err = child.communicate(timeout=FETCH_TIMEOUT)
        if child.returncode != 0:
            raise NoAnswer(err.decode("utf-8", "replace").strip() or "The lookup failed.")
        return out.decode("utf-8", "replace")
    except subprocess.TimeoutExpired as error:
        raise NoAnswer(f"The lookup took longer than {FETCH_TIMEOUT} seconds; try again later.") from error
    except OSError as error:
        raise NoAnswer("Could not start the lookup; try again.") from error
    finally:
        if child is not None and child.poll() is None:
            child.kill()
            child.communicate()


def answer(query: Query) -> str:
    if query.kind == "address":
        return address_answer(fetch("address", query.version), query.version)
    now = time.time()
    record = read_cache(now)
    if record is None:
        try:
            record = parse_record(json.loads(fetch("ecb")), datetime.now(timezone.utc).date())
        except json.JSONDecodeError as error:
            raise NoAnswer("The ECB lookup returned no rates.") from error
        write_cache(record, now)
    return currency_answer(query, record)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--worker"]:
        parser = argparse.ArgumentParser()
        parser.add_argument("--worker", choices=("ecb", "address"), required=True)
        parser.add_argument("--ip-version", type=int, choices=(4, 6), default=4)
        options = parser.parse_args(argv)
        return worker(options.worker, options.ip_version)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_lookup.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        query = ask_query(" ".join(argv[1:]))
        if query is None:
            return NOT_LOOKUP
        print(answer(query))
        return 0
    except KeyboardInterrupt:
        print("Lookup cancelled.", file=sys.stderr)
    except ValueError as error:      # NoAnswer included: recognised, not answerable
        print(str(error), file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
