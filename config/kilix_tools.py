"""Direct Ask answers that are worked out, not looked up.

Named maths (roots, powers, percentages, primes, number bases), unit
conversion, small text tools, random picks, and the meaning of a status
number. A request that is not entirely one of them returns 3 so Ask can
keep its normal route. Nothing here reads a file, runs a program, or uses
the network. Plain arithmetic stays in kilix_calc; this file hands it the
operands and never evaluates text itself.
"""

from __future__ import annotations

import base64
import hashlib
import math
import re
import secrets
import statistics
import sys
import urllib.parse
import uuid
from decimal import Decimal, InvalidOperation
from fractions import Fraction
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import kilix_calc  # noqa: E402
from kilix_asktext import clean  # noqa: E402

NOT_TOOL = 3
_ASK = r"(?:what(?:'s| is| are)|whats|how much is|calculate|compute|find|give me|tell me|show me|work out)"
_NUM = r"-?\d[\d,]*(?:\.\d+)?"
_MAX_TEXT = 4096
_LIMIT = 10 ** 15


class ToolError(Exception):
    """A recognized request that has no answer."""


# --- numbers ----------------------------------------------------------------

def _value(text: str) -> float:
    """One operand: digits, a written number, or a plain sum. Never eval()."""
    text = text.strip().replace(",", "")
    text = re.sub(r"\bpi\b|π", repr(math.pi), text, flags=re.IGNORECASE)
    text = re.sub(r"\btau\b", repr(math.tau), text, flags=re.IGNORECASE)
    text = re.sub(
        r"(?<![\w.])(\d+(?:\.\d+)?)e([+-]?\d{1,3})(?![\w.])",
        lambda match: format(Decimal(match.group(0)), "f"), text, flags=re.IGNORECASE)
    lowered = kilix_calc.lower(text).strip()
    if not lowered or not re.fullmatch(r"[0-9.+\-*/%()\s]+", lowered):
        raise ValueError(text)
    try:
        return kilix_calc.evaluate(lowered)
    except kilix_calc.CalculatorError as error:
        raise ToolError(str(error)) from error


def _whole(text: str, *, limit: int = _LIMIT) -> int:
    value = _value(text)
    if isinstance(value, float):
        if not value.is_integer():
            raise ToolError("that needs a whole number")
        value = int(value)
    if abs(value) > limit:
        raise ToolError("that number is too large")
    return value


def _fmt(value: float, places: int = 10) -> str:
    if isinstance(value, bool):
        raise ToolError("only numbers are allowed")
    if isinstance(value, int):
        return str(value)
    if math.isnan(value) or math.isinf(value):
        raise ToolError("that has no finite answer")
    if value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.{places}g}"


def _money(value: float) -> str:
    return f"{value:,.2f}"


def _is_prime(number: int) -> bool:
    if number < 2:
        return False
    if number < 4:
        return True
    if number % 2 == 0 or number % 3 == 0:
        return False
    step = 5
    while step * step <= number:
        if number % step == 0 or number % (step + 2) == 0:
            return False
        step += 6
    return True


def _factorize(number: int) -> list[int]:
    factors = []
    step = 2
    while step * step <= number:
        while number % step == 0:
            factors.append(step)
            number //= step
        step += 1 if step == 2 else 2
    if number > 1:
        factors.append(number)
    return factors


_ROMAN = ((1000, "M"), (900, "CM"), (500, "D"), (400, "CD"), (100, "C"), (90, "XC"),
          (50, "L"), (40, "XL"), (10, "X"), (9, "IX"), (5, "V"), (4, "IV"), (1, "I"))


def _to_roman(number: int) -> str:
    if not 1 <= number <= 3999:
        raise ToolError("roman numerals cover 1 to 3999")
    out = []
    for value, letters in _ROMAN:
        while number >= value:
            out.append(letters)
            number -= value
    return "".join(out)


def _from_roman(text: str) -> int:
    text = text.upper()
    total = index = 0
    for value, letters in _ROMAN:
        while text.startswith(letters, index):
            total += value
            index += len(letters)
    if index != len(text) or total == 0 or _to_roman(total) != text:
        raise ToolError("that is not a roman numeral")
    return total


_BASES = {"hex": 16, "hexadecimal": 16, "base 16": 16, "binary": 2, "base 2": 2,
          "octal": 8, "base 8": 8, "decimal": 10, "base 10": 10, "denary": 10}
_BASE = "|".join(sorted(_BASES, key=len, reverse=True))


def _parse_based(text: str, base: int | None) -> int:
    text = text.strip().lower().replace("_", "").replace(",", "")
    try:
        if text.startswith(("0x", "0b", "0o")):
            return int(text, 0)
        return int(text, base or 10)
    except ValueError as error:
        raise ToolError("those digits do not fit that base") from error


def _show_based(number: int, base: int) -> str:
    if abs(number) > 10 ** 60:
        raise ToolError("that number is too large")
    sign = "-" if number < 0 else ""
    number = abs(number)
    if base == 16:
        return f"{sign}0x{number:x}"
    if base == 2:
        return f"{sign}0b{number:b}"
    if base == 8:
        return f"{sign}0o{number:o}"
    return f"{sign}{number}"


def _numbers(text: str) -> list[float]:
    parts = [part for part in re.split(r"\s*(?:,|\band\b|;)\s*|\s+", text.strip()) if part]
    if len(parts) < 2 or len(parts) > 200:
        raise ValueError(text)
    return [_value(part) for part in parts]


def _maths(low: str) -> str | None:
    ask = rf"(?:{_ASK} )?(?:the )?"

    match = re.fullmatch(rf"{ask}(?P<kind>square|cube) root of (?P<x>.+)|{ask}sqrt ?\(?(?P<x2>[^()]+)\)?", low)
    if match:
        value = _value(match["x"] or match["x2"])
        if match["kind"] == "cube":
            root = math.copysign(abs(value) ** (1 / 3), value)
            nearest = round(root)
            return _fmt(nearest if nearest ** 3 == value else root)
        if value < 0:
            raise ToolError("a negative number has no real square root")
        root = math.isqrt(int(value)) if float(value).is_integer() and value < 10 ** 30 else None
        if root is not None and root * root == value:
            return str(root)
        return _fmt(math.sqrt(value))
    match = re.fullmatch(rf"{ask}(?P<x>.+?) (?P<kind>squared|cubed)", low)
    if match:
        value = _value(match["x"])
        return _fmt(value ** (2 if match["kind"] == "squared" else 3))
    match = re.fullmatch(
        rf"{ask}(?P<x>.+?) (?:to the power(?: of)?|raised to(?: the power(?: of)?)?|\^|\*\*) ?(?P<y>.+)"
        rf"|{ask}(?P<x2>.+?) to the (?P<y2>\S+?)(?:st|nd|rd|th)?(?: power)", low)
    if match:
        base, power = _value(match["x"] or match["x2"]), _value(match["y"] or match["y2"])
        if abs(power) > 1024:
            raise ToolError("exponent above 1024")
        try:
            return _fmt(base ** power)
        except (OverflowError, ZeroDivisionError) as error:
            raise ToolError("that has no finite answer") from error
    match = re.fullmatch(rf"{ask}(?P<x>.+?)(?: factorial|!)|{ask}factorial of (?P<x2>.+)", low)
    if match:
        number = _whole(match["x"] or match["x2"])
        if not 0 <= number <= 1000:
            raise ToolError("factorials run from 0 to 1000")
        return str(math.factorial(number))
    match = re.fullmatch(
        rf"{ask}(?P<part>half|a half|one half|a third|one third|a quarter|one quarter|a fourth|a fifth|a tenth)"
        r" of (?P<x>.+)", low)
    if match:
        divisor = {"half": 2, "third": 3, "quarter": 4, "fourth": 4, "fifth": 5, "tenth": 10}[
            match["part"].split()[-1]]
        return _fmt(_value(match["x"]) / divisor)
    match = re.fullmatch(rf"{ask}(?P<kind>double|twice|triple|thrice) (?P<x>.+)", low)
    if match:
        return _fmt(_value(match["x"]) * (2 if match["kind"] in ("double", "twice") else 3))
    match = re.fullmatch(
        rf"{ask}(?P<x>.+?) (?:mod|modulo) (?P<y>.+)"
        rf"|{ask}remainder (?:of|when) (?P<x2>.+?) (?:is )?divided by (?P<y2>.+)", low)
    if match:
        left, right = _value(match["x"] or match["x2"]), _value(match["y"] or match["y2"])
        if right == 0:
            raise ToolError("division by zero")
        return _fmt(left % right)

    match = re.fullmatch(
        rf"{ask}(?P<fn>sin|sine|cos|cosine|tan|tangent)(?: of)? ?\(?(?P<x>[^()]+?)\)?"
        r"(?: ?(?P<unit>degrees?|deg|°|radians?|rad))?", low)
    if match:
        angle = _value(match["x"])
        radians = match["unit"] and match["unit"].startswith("rad") or "pi" in match["x"]
        function = {"s": math.sin, "c": math.cos, "t": math.tan}[match["fn"][0]]
        result = function(angle if radians else math.radians(angle))
        if abs(result) > 1e15:
            raise ToolError("that tangent is not defined")
        label = f"{match['fn'][:3]}({_fmt(angle)}{' rad' if radians else '°'})"
        return f"{label} = {_fmt(round(result, 12) + 0.0)}"
    match = re.fullmatch(
        rf"{ask}(?:(?P<nat>ln|natural log(?:arithm)?)|log(?:arithm)?(?: base (?P<base>\S+)|(?P<b2>2|10))?)"
        r"(?: of)? ?\(?(?P<x>[^()]+?)\)?", low)
    if match:
        value = _value(match["x"])
        if value <= 0:
            raise ToolError("a logarithm needs a positive number")
        if match["nat"]:
            return _fmt(math.log(value))
        base = _value(match["base"] or match["b2"] or "10")
        if base <= 0 or base == 1:
            raise ToolError("that is not a logarithm base")
        result = math.log(value, base)
        return _fmt(round(result) if abs(result - round(result)) < 1e-12 else result)

    match = re.fullmatch(
        rf"{ask}(?P<kind>average|mean|median|sum|total|product|max|maximum|largest|biggest|min|minimum|smallest)"
        r" of (?P<list>.+)", low)
    if match:
        values = _numbers(match["list"])
        kind = match["kind"]
        if kind in ("average", "mean"):
            return _fmt(statistics.fmean(values))
        if kind == "median":
            return _fmt(statistics.median(values))
        if kind in ("sum", "total"):
            return _fmt(sum(values))
        if kind == "product":
            return _fmt(math.prod(values))
        return _fmt(max(values) if kind in ("max", "maximum", "largest", "biggest") else min(values))
    match = re.fullmatch(
        rf"(?:{_ASK} )?round (?P<x>.+?)(?: (?:to|at) (?P<n>\d{{1,2}}) (?:decimal )?(?:places?|decimals?|digits?)"
        r"| to the nearest (?P<near>whole number|integer|ten|hundred|thousand|tenth|hundredth))?", low)
    if match:
        value = _value(match["x"])
        places = int(match["n"]) if match["n"] else {
            None: 0, "whole number": 0, "integer": 0, "ten": -1, "hundred": -2,
            "thousand": -3, "tenth": 1, "hundredth": 2}[match["near"]]
        if places > 15:
            raise ToolError("at most 15 decimal places")
        quantum = Decimal(1).scaleb(-places)
        rounded = Decimal(repr(float(value))).quantize(quantum, rounding="ROUND_HALF_UP")
        return format(rounded, "f") if places > 0 else str(int(rounded))

    match = re.fullmatch(
        rf"(?:{_ASK} )?(?:a |an )?(?P<p>{_NUM}) ?(?:%|percent) tip (?:on|for|of) \$?(?P<x>{_NUM})(?: dollars| bucks)?"
        rf"|(?:{_ASK} )?(?:the )?tip (?:on|for) \$?(?P<x2>{_NUM})(?: dollars| bucks)?(?: at (?P<p2>{_NUM}) ?(?:%|percent))?", low)
    if match:
        bill = float((match["x"] or match["x2"]).replace(",", ""))
        rate = float((match["p"] or match["p2"] or "20").replace(",", ""))
        tip = bill * rate / 100
        return f"{_money(tip)} tip at {_fmt(rate)}%, {_money(bill + tip)} total"
    match = re.fullmatch(
        rf"(?:{_ASK} )?(?P<p>{_NUM}) ?(?:%|percent) (?P<kind>off|discount on|more than|less than|added to)"
        rf"(?: of)? \$?(?P<x>{_NUM})", low)
    if match:
        rate, base = float(match["p"].replace(",", "")), float(match["x"].replace(",", ""))
        sign = 1 if match["kind"] in ("more than", "added to") else -1
        return _fmt(round(base * (1 + sign * rate / 100), 10))
    match = re.fullmatch(
        rf"(?:what (?:percent|percentage) of (?P<y>{_NUM}) is (?P<x>{_NUM})"
        rf"|(?P<x2>{_NUM}) is what (?:percent|percentage) of (?P<y2>{_NUM})"
        rf"|(?:{_ASK} )?(?P<x3>{_NUM}) (?:out of|as a (?:percent|percentage) of) (?P<y3>{_NUM})(?: as a (?:percent|percentage))?)", low)
    if match:
        part = float((match["x"] or match["x2"] or match["x3"]).replace(",", ""))
        whole = float((match["y"] or match["y2"] or match["y3"]).replace(",", ""))
        if whole == 0:
            raise ToolError("division by zero")
        return f"{_fmt(round(part / whole * 100, 6))}%"
    match = re.fullmatch(
        rf"(?:{_ASK} )?(?:the )?(?:percent|percentage) (?:change|increase|decrease|difference)"
        rf" (?:from|between) (?P<a>{_NUM}) (?:to|and) (?P<b>{_NUM})", low)
    if match:
        first, second = (float(match[key].replace(",", "")) for key in ("a", "b"))
        if first == 0:
            raise ToolError("a change from zero has no percentage")
        change = (second - first) / abs(first) * 100
        return f"{'+' if change > 0 else ''}{_fmt(round(change, 6))}%"

    match = re.fullmatch(r"is (?P<n>[\d,]+) (?:a )?prime(?: number)?", low)
    if match:
        number = _whole(match["n"], limit=10 ** 14)
        if _is_prime(number):
            return f"yes, {number} is prime"
        factors = _factorize(number)
        detail = f" ({' × '.join(map(str, factors))})" if len(factors) > 1 else ""
        return f"no, {number} is not prime{detail}"
    match = re.fullmatch(rf"(?:{_ASK} )?(?:the )?(?:prime )?(?:factors|factorization|factorisation) of (?P<n>[\d,]+)"
                         r"|factor(?:ize|ise)? (?P<n2>[\d,]+)", low)
    if match:
        number = _whole(match["n"] or match["n2"], limit=10 ** 14)
        if number < 2:
            raise ToolError("factor a whole number above 1")
        factors = _factorize(number)
        return f"{number} is prime" if len(factors) == 1 else " × ".join(map(str, factors))
    match = re.fullmatch(
        rf"(?:{_ASK} )?(?:the )?(?P<kind>gcd|gcf|hcf|lcm|greatest common (?:divisor|factor)|least common multiple|lowest common multiple)"
        r" of (?P<list>.+)", low)
    if match:
        values = [_whole(_fmt(value)) for value in _numbers(match["list"])]
        if match["kind"] in ("lcm", "least common multiple", "lowest common multiple"):
            return str(math.lcm(*values))
        return str(math.gcd(*values))

    match = re.fullmatch(
        rf"(?:{_ASK} |convert )?(?:(?P<from>{_BASE}) )?(?P<x>[0-9a-fxob_,]+)(?: in (?P<from2>{_BASE}))?"
        rf" (?:in|to|into|as) (?P<to>{_BASE})", low)
    if match:
        source = _BASES.get(match["from"] or match["from2"] or "")
        number = _parse_based(match["x"], source)
        return _show_based(number, _BASES[match["to"]])
    match = re.fullmatch(
        rf"(?:{_ASK} |convert )?(?P<x>.+?) (?:in|to|into|as) roman(?: numerals?)?"
        rf"|(?:{_ASK} )?(?:the )?roman numeral (?:for|of) (?P<x2>.+)", low)
    if match:
        return _to_roman(_whole(match["x"] or match["x2"]))
    match = re.fullmatch(
        rf"(?:{_ASK} )?(?:the )?roman numeral (?P<r>[ivxlcdm]+)(?: in (?:numbers|decimal|digits|arabic))?"
        rf"|(?:{_ASK} |convert )?(?P<r2>[ivxlcdm]{{2,}}) (?:in|to|as) (?:numbers|decimal|digits|arabic)", low)
    if match:
        return str(_from_roman(match["r"] or match["r2"]))

    # A sum the calculator left alone because it names pi or uses 1e6.
    if re.search(r"\bpi\b|π|\btau\b|\d(?:\.\d+)?e[+-]?\d", low) and re.search(r"[+\-*/x]|times|plus|minus|divided", low):
        body = re.sub(rf"^{_ASK} ", "", low)
        try:
            return _fmt(_value(body))
        except ValueError:
            return None
    return None


# --- units ------------------------------------------------------------------

def _table(*rows: tuple[float, str, str]) -> dict[str, tuple[float, str]]:
    table = {}
    for factor, label, names in rows:
        for name in names.split("|"):
            table[name] = (factor, label)
    return table


_UNITS = {
    "length": _table(
        (0.001, "mm", "mm|millimeter|millimeters|millimetre|millimetres"),
        (0.01, "cm", "cm|centimeter|centimeters|centimetre|centimetres"),
        (1, "m", "m|meter|meters|metre|metres"),
        (1000, "km", "km|kilometer|kilometers|kilometre|kilometres|klick|klicks"),
        (0.0254, "in", "in|inch|inches"), (0.3048, "ft", "ft|foot|feet"),
        (0.9144, "yd", "yd|yard|yards"), (1609.344, "mi", "mi|mile|miles"),
        (1852, "nmi", "nmi|nautical_mile|nautical_miles"),
        (9.4607304725808e15, "light years", "light_year|light_years|ly"),
    ),
    "mass": _table(
        (1e-6, "mg", "mg|milligram|milligrams"), (0.001, "g", "g|gram|grams"),
        (1, "kg", "kg|kilo|kilos|kilogram|kilograms"),
        (1000, "t", "tonne|tonnes|metric_ton|metric_tons"),
        (0.028349523125, "oz", "oz|ounce|ounces"), (0.45359237, "lb", "lb|lbs|pound|pounds"),
        (6.35029318, "st", "stone|stones"), (907.18474, "short tons", "ton|tons|short_ton|short_tons"),
    ),
    "volume": _table(
        (0.001, "ml", "ml|milliliter|milliliters|millilitre|millilitres|cc"),
        (1, "L", "l|liter|liters|litre|litres"),
        (0.00492892159375, "tsp", "tsp|teaspoon|teaspoons"),
        (0.01478676478125, "tbsp", "tbsp|tablespoon|tablespoons"),
        (0.0295735295625, "fl oz", "floz|fluid_ounce|fluid_ounces"),
        (0.2365882365, "cups", "cup|cups"), (0.473176473, "pt", "pt|pint|pints"),
        (0.946352946, "qt", "qt|quart|quarts"), (3.785411784, "gal", "gal|gallon|gallons"),
        (1000, "m³", "cubic_meter|cubic_meters|cubic_metre|cubic_metres"),
    ),
    "time": _table(
        (0.001, "ms", "ms|millisecond|milliseconds"), (1, "s", "s|sec|secs|second|seconds"),
        (60, "min", "min|mins|minute|minutes"), (3600, "h", "h|hr|hrs|hour|hours"),
        (86400, "days", "d|day|days"), (604800, "weeks", "wk|week|weeks"),
        (1209600, "fortnights", "fortnight|fortnights"),
        (2629746, "months", "month|months"), (31556952, "years", "yr|yrs|year|years"),
        (315569520, "decades", "decade|decades"), (3155695200, "centuries", "century|centuries"),
    ),
    "speed": _table(
        (0.44704, "mph", "mph|miles_per_hour"), (1 / 3.6, "km/h", "km/h|kph|kmh|kmph|kilometers_per_hour|kilometres_per_hour"),
        (1, "m/s", "m/s|mps|meters_per_second|metres_per_second"),
        (0.514444444, "kn", "kn|knot|knots"), (0.3048, "ft/s", "ft/s|fps|feet_per_second"),
    ),
    "area": _table(
        (1, "m²", "square_meter|square_meters|square_metre|square_metres|sqm"),
        (0.09290304, "sq ft", "square_foot|square_feet|sqft"),
        (4046.8564224, "acres", "acre|acres"), (10000, "ha", "ha|hectare|hectares"),
        (1e6, "km²", "square_kilometer|square_kilometers|square_kilometre|square_kilometres"),
        (2589988.110336, "sq mi", "square_mile|square_miles"),
    ),
    "data": _table(
        (0.125, "bits", "bit|bits"), (1, "B", "b|byte|bytes"),
        (1e3, "KB", "kb|kilobyte|kilobytes"), (1e6, "MB", "mb|megabyte|megabytes"),
        (1e9, "GB", "gb|gigabyte|gigabytes"), (1e12, "TB", "tb|terabyte|terabytes"),
        (1e15, "PB", "pb|petabyte|petabytes"),
        (1024, "KiB", "kib|kibibyte|kibibytes"), (1024 ** 2, "MiB", "mib|mebibyte|mebibytes"),
        (1024 ** 3, "GiB", "gib|gibibyte|gibibytes"), (1024 ** 4, "TiB", "tib|tebibyte|tebibytes"),
    ),
    "energy": _table(
        (1, "J", "j|joule|joules"), (1000, "kJ", "kj|kilojoule|kilojoules"),
        (4.184, "cal", "cal|calorie|calories"), (4184, "kcal", "kcal|kilocalorie|kilocalories"),
        (3600, "Wh", "wh|watt_hour|watt_hours"), (3.6e6, "kWh", "kwh|kilowatt_hour|kilowatt_hours"),
        (1055.05585262, "BTU", "btu|btus"),
    ),
    "pressure": _table(
        (1, "Pa", "pa|pascal|pascals"), (1000, "kPa", "kpa|kilopascal|kilopascals"),
        (100000, "bar", "bar|bars"), (6894.757293168, "psi", "psi"),
        (101325, "atm", "atm|atmosphere|atmospheres"),
    ),
    "power": _table(
        (1, "W", "w|watt|watts"), (1000, "kW", "kw|kilowatt|kilowatts"),
        (745.69987158227, "hp", "hp|horsepower"),
    ),
    "angle": _table(
        (1, "°", "degree|degrees|deg"), (180 / math.pi, "rad", "rad|radian|radians"),
        (360, "turns", "turn|turns|revolution|revolutions"),
    ),
}
_TEMPERATURE = {"c": "°C", "celsius": "°C", "centigrade": "°C", "f": "°F", "fahrenheit": "°F",
                "k": "K", "kelvin": "K"}
_JOINED = (
    (r"\bmiles (?:per|an) hour\b", "mph"), (r"\b(?:kilometers|kilometres|km) (?:per|an) hour\b", "km/h"),
    (r"\b(?:meters|metres) per second\b", "m/s"), (r"\bfeet per second\b", "ft/s"),
    (r"\bfl(?:uid)?\.? (?:oz|ounces?)\b", "floz"), (r"\bnautical (miles?)\b", r"nautical_\1"),
    (r"\blight[ -]?(years?)\b", r"light_\1"), (r"\bmetric (tons?)\b", r"metric_\1"),
    (r"\bshort (tons?)\b", r"short_\1"),
    (r"\b(?:sq\.?|square) ?(m|meters?|metres?)\b", "square_meters"),
    (r"\b(?:sq\.?|square) ?(?:ft|foot|feet)\b", "square_feet"),
    (r"\b(?:sq\.?|square) ?(?:km|kilometers?|kilometres?)\b", "square_kilometers"),
    (r"\b(?:sq\.?|square) ?(?:mi|miles?)\b", "square_miles"),
    (r"\bcubic (meters?|metres?)\b", r"cubic_\1"),
    (r"\b(watt|kilowatt)[ -](hours?)\b", r"\1_\2"),
    (r"\bdegrees? (celsius|centigrade|fahrenheit|kelvin|c|f)\b", r"\1"),
    (r"°\s?(c|f)\b", r"\1"), (r"(?<=\d)\s?°(?=\s|$)", " degrees"),
    (r"(\d)\s?'(?=\s|\d|$)", r"\1 ft "), (r'(\d)\s?"(?=\s|$)', r"\1 in "),
)
_UNIT_WORD = r"[a-z][a-z/_³²]*"
_CURRENCY = re.compile(
    r"\b(?:usd|eur|gbp|jpy|cad|aud|chf|cny|inr|mxn|btc|eth|dollars?|euros?|pounds? sterling|yen|"
    r"rupees?|pesos?|bitcoin)\b", re.IGNORECASE)


def _unit(name: str) -> tuple[str, float, str] | None:
    name = name.lower()
    for kind, table in _UNITS.items():
        if name in table:
            return kind, table[name][0], table[name][1]
    if name in _TEMPERATURE:
        return "temperature", 0.0, _TEMPERATURE[name]
    return None


def _temperature(value: float, source: str, target: str) -> float:
    kelvin = {"°C": value + 273.15, "°F": (value - 32) * 5 / 9 + 273.15, "K": value}[source]
    if kelvin < -1e-9:
        raise ToolError("that is below absolute zero")
    return {"°C": kelvin - 273.15, "°F": (kelvin - 273.15) * 9 / 5 + 32, "K": kelvin}[target]


def _convert(low: str) -> str | None:
    text = low
    for pattern, replacement in _JOINED:
        text = re.sub(pattern, replacement, text)
    text = re.sub(r"(?<=\d),(?=\d{3}\b)", "", text)
    amount = rf"(?P<amount>(?:{_NUM}|an?|one|half an?|half a) ?{_UNIT_WORD}(?:(?: and|,)? {_NUM} ?{_UNIT_WORD}){{0,3}})"
    target = rf"(?P<to>{_UNIT_WORD})"
    match = (
        re.fullmatch(rf"(?:{_ASK} |convert |change )?{amount} (?:in|to|into|as|equals?|=) (?:how many )?{target}", text)
        or re.fullmatch(rf"how many {target} (?:are |is )?(?:there )?(?:in|to|per|make|make up|is|are|equals?) {amount}", text)
        or re.fullmatch(rf"{amount} (?:is|are|equals?|=) how many {target}", text)
        or re.fullmatch(rf"how (?:long|far|heavy|much|big|fast|hot|cold) is {amount} in {target}", text)
    )
    if match is None:
        return None
    goal = _unit(match["to"])
    if goal is None:
        return None
    pieces = re.findall(rf"({_NUM}|an?|one|half an?|half a) ?({_UNIT_WORD})", match["amount"])
    if not pieces or "".join(a + b for a, b in pieces).replace(" ", "") != re.sub(
            r"\band\b|,|\s", "", match["amount"]):
        return None
    total = 0.0
    kinds = set()
    first_label = ""
    for raw, name in pieces:
        unit = _unit(name)
        if unit is None:
            return None
        number = {"a": 1.0, "an": 1.0, "one": 1.0, "half a": 0.5, "half an": 0.5}.get(raw)
        if number is None:
            number = float(raw.replace(",", ""))
        kinds.add(unit[0])
        first_label = first_label or unit[2]
        if unit[0] == "temperature":
            if len(pieces) != 1:
                return None
            if goal[0] != "temperature":
                raise ToolError("those units are not the same kind")
            return f"{_fmt(round(_temperature(number, unit[2], goal[2]), 6))} {goal[2]}"
        total += number * unit[1]
    if len(kinds) != 1:
        raise ToolError("those units are not the same kind")
    ounce = ("oz", "ounce", "ounces")
    if goal[0] == "volume" and kinds == {"mass"} and all(name in ounce for _raw, name in pieces):
        # Ounces measured against a volume are fluid ounces.
        total = total / _UNITS["mass"]["oz"][0] * _UNITS["volume"]["floz"][0]
        kinds = {"volume"}
    elif kinds == {"volume"} and match["to"] in ounce:
        goal = ("volume", *_UNITS["volume"]["floz"])
    if goal[0] != next(iter(kinds)):
        raise ToolError(f"{first_label} and {goal[2]} are not the same kind of unit")
    value = total / goal[1]
    shown = _fmt(round(value, 6)) if abs(value) >= 0.001 else _fmt(value, 6)
    return f"{shown} {goal[2]}"


# --- text -------------------------------------------------------------------

def _quoted(text: str) -> str:
    text = text.strip()
    if len(text) >= 2 and text[0] == text[-1] and text[0] in "\"'`":
        return text[1:-1]
    return text


def _text_tools(text: str, low: str) -> str | None:
    def original(match: re.Match, name: str) -> str:
        start, end = match.span(name)
        return _quoted(text[start:end])

    match = re.fullmatch(
        r"(?:how many|count(?: the)?|number of) (?P<what>words|characters|chars|letters|vowels|consonants)"
        r"(?: are| is)?(?: there)? in (?:the (?:word|phrase|sentence|text|string) )?(?P<t>.+)", low)
    if match:
        body = original(match, "t")
        what = match["what"]
        if what == "words":
            count = len(body.split())
        elif what in ("characters", "chars"):
            count = len(body)
        elif what == "letters":
            count = sum(1 for char in body if char.isalpha())
        elif what == "vowels":
            count = sum(1 for char in body.lower() if char in "aeiou")
        else:
            count = sum(1 for char in body.lower() if char.isalpha() and char not in "aeiou")
        return str(count)
    match = re.fullmatch(
        r"(?:how long is|what(?:'s| is) the length of) (?:the (?:word|phrase|sentence|text|string) )?(?P<t>[\"'`].+[\"'`])", low)
    if match:
        return f"{len(original(match, 't'))} characters"
    match = re.fullmatch(
        r"(?:convert|make|put|write|say) (?:the (?:word|phrase|text) )?(?P<t>[\"'`].+[\"'`]) (?:in|to|into|as) "
        r"(?P<kind>upper ?case|lower ?case|title ?case|all caps|caps)"
        r"|(?:the (?:word|phrase|text) )?(?P<t3>[\"'`].+[\"'`]|[a-z0-9' -]{1,60}) (?:in|as) "
        r"(?P<kind3>upper ?case|lower ?case|title ?case|all caps)"
        r"|(?P<kind2>upper ?case|lower ?case|title ?case|capitali[sz]e) (?:the (?:word|phrase|text) )?(?P<t2>.+)", low)
    if match and not re.match(r"(?:rename|move|save|write|name|find|list|show|open|type|print|set)\b", low):
        body = original(match, "t" if match["t"] else "t3" if match["t3"] else "t2")
        kind = (match["kind"] or match["kind3"] or match["kind2"]).replace(" ", "")
        if kind in ("uppercase", "allcaps", "caps"):
            return body.upper()
        if kind == "lowercase":
            return body.lower()
        return body.title() if kind == "titlecase" else body[:1].upper() + body[1:]
    match = re.fullmatch(
        r"(?:reverse|spell)(?: the (?:word|phrase|text|string))? (?P<t>.+?)(?P<back> backwards?| in reverse)?", low)
    if match and (low.startswith("reverse") or match["back"]):
        return original(match, "t")[::-1]
    match = re.fullmatch(r"(?:how do (?:you|i) spell|spell(?: out)?(?: the word)?) (?P<t>[a-z'-]{2,40})", low)
    if match:
        return "-".join(original(match, "t").upper())
    match = re.fullmatch(
        r"(?:base ?64[ -]?(?P<dir>encode|decode)|(?P<dir2>encode|decode)(?: this)?(?: (?:in|to|as|from|with))? base ?64)"
        r"(?: of| for)? (?P<t>.+)"
        r"|(?P<dir3>encode|decode) (?P<t2>.+) (?:in|to|as|from|with) base ?64", low)
    if match:
        direction = match["dir"] or match["dir2"] or match["dir3"]
        body = original(match, "t" if match["t"] else "t2")
        if direction == "encode":
            return base64.b64encode(body.encode()).decode()
        try:
            padded = body + "=" * (-len(body) % 4)
            return base64.b64decode(padded, validate=True).decode()
        except (ValueError, UnicodeDecodeError) as error:
            raise ToolError("that is not base64 text") from error
    match = re.fullmatch(r"url[ -]?(?P<dir>encode|decode|escape|unescape) (?P<t>.+)", low)
    if match:
        body = original(match, "t")
        if match["dir"] in ("encode", "escape"):
            return urllib.parse.quote(body, safe="")
        return urllib.parse.unquote(body)
    match = re.fullmatch(
        r"(?:what(?:'s| is) the |the )?(?P<algo>md5|sha-?1|sha-?224|sha-?256|sha-?384|sha-?512)(?:sum)?"
        r"(?: hash| digest| checksum| sum)?(?: of| for) (?:the (?:word|text|string) )?(?P<t>.+)"
        r"|(?:hash|digest) (?P<t2>.+) (?:with|using|as) (?P<algo2>md5|sha-?1|sha-?224|sha-?256|sha-?384|sha-?512)", low)
    if match:
        algo = (match["algo"] or match["algo2"]).replace("-", "")
        body = original(match, "t" if match["t"] else "t2")
        return hashlib.new(algo, body.encode()).hexdigest()
    match = re.fullmatch(r"rot ?13 (?:of )?(?P<t>.+)", low)
    if match:
        import codecs
        return codecs.encode(original(match, "t"), "rot13")
    match = re.fullmatch(
        r"(?:what(?:'s| is) )?(?:the )?(?P<kind>ascii|unicode)(?: code| value| number| code ?point)?"
        r" (?:for|of) (?:the (?:letter|character|symbol) )?(?P<t>.+)", low)
    if match:
        body = original(match, "t")
        if body.lower() in ("space", "a space"):
            body = " "
        if len(body) != 1:
            return None
        code = ord(body)
        return f"{code} (0x{code:02x}, U+{code:04X})"
    match = re.fullmatch(
        r"(?:what(?:'s| is) |which |what )?(?:the )?(?:character|letter|symbol|char)(?: is| for| has)?"
        r" (?:ascii|unicode|code|code ?point)(?: code)? (?:u\+)?(?P<n>[0-9a-fx]+)|chr ?\(?(?P<n2>\d+)\)?", low)
    if match:
        raw = match["n"] or match["n2"]
        try:
            code = int(raw, 16) if (raw.startswith("0x") or "u+" in low) else int(raw)
            char = chr(code)
        except (ValueError, OverflowError) as error:
            raise ToolError("that is not a character code") from error
        return repr(char) if not char.isprintable() or char.isspace() else char
    return None


# --- random -----------------------------------------------------------------

def _random(text: str, low: str) -> str | None:
    rng = secrets.SystemRandom()
    if re.fullmatch(r"(?:flip|toss|throw) (?:a |the )?coin|coin ?(?:flip|toss)|heads or tails", low):
        return rng.choice(("heads", "tails"))
    match = re.fullmatch(
        r"roll (?:a |an |the |one )?(?:six[- ]sided )?di(?:e|ce)"
        r"|roll (?P<n>\d{1,3}|two|three|four|five|six) (?:di(?:e|ce))"
        r"|(?:roll )?(?:a |an )?(?P<n2>\d{0,3})d(?P<sides>\d{1,4})(?: ?(?P<sign>[+-]) ?(?P<mod>\d{1,4}))?", low)
    if match:
        words = {"two": 2, "three": 3, "four": 4, "five": 5, "six": 6}
        raw = match["n"] or match["n2"] or "1"
        count = words.get(raw) or int(raw)
        sides = int(match["sides"] or 6)
        if not 1 <= count <= 100 or not 2 <= sides <= 1000:
            raise ToolError("roll 1 to 100 dice with 2 to 1000 sides")
        rolls = [rng.randint(1, sides) for _ in range(count)]
        bonus = int(match["mod"] or 0) * (-1 if match["sign"] == "-" else 1)
        if count == 1 and not bonus:
            return str(rolls[0])
        shown = " + ".join(map(str, rolls)) + (f" {match['sign']} {abs(bonus)}" if bonus else "")
        return f"{sum(rolls) + bonus} ({shown})"
    match = re.fullmatch(
        r"(?:pick|choose|give me|generate|get|think of)?\s*(?:a |an )?(?:random )?(?:number|integer)"
        r"(?: (?:between|from) (?P<a>-?\d{1,15}) (?:and|to|-) (?P<b>-?\d{1,15}))?"
        r"|random (?P<a2>-?\d{1,15}) ?(?:to|-) ?(?P<b2>-?\d{1,15})", low)
    if match and ("number" in low or "integer" in low or match["a2"]):
        first = int(match["a"] or match["a2"] or 1)
        second = int(match["b"] or match["b2"] or 100)
        return str(rng.randint(min(first, second), max(first, second)))
    match = re.fullmatch(
        r"(?:pick|choose|select)(?: one)?(?: of| from| between)?:? (?P<t>.+)"
        r"|(?P<t2>.+ or .+)(?:, | )(?:pick|choose) (?:one|for me)", low)
    if match:
        start, end = match.span("t" if match["t"] else "t2")
        options = [part.strip(" ,") for part in re.split(r",|\bor\b|\band\b", text[start:end])]
        options = [option for option in options if option]
        chosen = text[start:end]
        listed = " or " in f" {chosen.lower()} " or ":" in low[:match.start("t" if match["t"] else "t2")]
        if (len(options) >= 2 and listed and len(chosen) <= 160
                and not re.match(r"(?:up|back|out|off|it|the session)\b", chosen.lower())
                and all(len(option.split()) <= 5 for option in options)):
            return rng.choice(options)
        return None
    match = re.fullmatch(r"shuffle:? (?P<t>.+)", low)
    if match:
        start, end = match.span("t")
        items = [part.strip() for part in re.split(r",|\band\b", text[start:end]) if part.strip()]
        if len(items) >= 2:
            rng.shuffle(items)
            return ", ".join(items)
        return None
    match = re.fullmatch(
        r"(?:generate|make|create|give me|suggest|new|get)?\s*(?:a |an |me a )?(?:new )?"
        r"(?:(?P<n>\d{1,3})[- ](?:character|char|letter|digit) )?(?:random |strong |secure |good )*"
        r"(?P<kind>password|passcode|pin)(?: (?:of|with) (?P<n2>\d{1,3}) (?:characters|chars|letters|digits))?", low)
    if match:
        length = int(match["n"] or match["n2"] or (6 if match["kind"] == "pin" else 20))
        if not 4 <= length <= 128:
            raise ToolError("a password is 4 to 128 characters")
        if match["kind"] == "pin":
            return "".join(secrets.choice("0123456789") for _ in range(length))
        alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789"
        symbols = "!@#%^&*-_=+"
        while True:
            chars = [secrets.choice(alphabet + symbols) for _ in range(length)]
            if (any(c.islower() for c in chars) and any(c.isupper() for c in chars)
                    and any(c.isdigit() for c in chars) and any(c in symbols for c in chars)):
                return "".join(chars)
    if re.fullmatch(r"(?:generate|make|create|give me|new|get)?\s*(?:a |an |me a )?(?:new |random )?(?:uuid|guid)", low):
        return str(uuid.uuid4())
    return None


# --- reference ----------------------------------------------------------------

_SIGNAL_NOTES = {
    "SIGHUP": "hangup: the terminal closed, or reload configuration",
    "SIGINT": "interrupt from the keyboard, Ctrl-C",
    "SIGQUIT": "quit from the keyboard with a core dump, Ctrl-\\",
    "SIGILL": "illegal instruction", "SIGABRT": "the program aborted itself",
    "SIGBUS": "bus error: bad memory access", "SIGFPE": "arithmetic error, such as division by zero",
    "SIGKILL": "killed at once; it cannot be caught or ignored",
    "SIGUSR1": "user-defined signal 1", "SIGSEGV": "segmentation fault: invalid memory access",
    "SIGUSR2": "user-defined signal 2", "SIGPIPE": "wrote to a pipe nobody is reading",
    "SIGALRM": "a timer ran out", "SIGTERM": "polite request to stop; the default of kill",
    "SIGCHLD": "a child process stopped or exited", "SIGCONT": "continue after a stop",
    "SIGSTOP": "stopped; it cannot be caught or ignored", "SIGTSTP": "stop from the keyboard, Ctrl-Z",
    "SIGTTIN": "background process tried to read the terminal",
    "SIGTTOU": "background process tried to write the terminal",
    "SIGXCPU": "CPU time limit exceeded", "SIGXFSZ": "file size limit exceeded",
    "SIGWINCH": "the terminal window changed size",
}
_EXIT_NOTES = {
    0: "success", 1: "a general error", 2: "wrong usage of the command",
    126: "the command was found but could not be run (not executable)",
    127: "command not found", 128: "invalid exit argument",
    255: "exit status out of range, or an ssh connection error",
}
_COLORS = {
    "black": "#000000", "white": "#ffffff", "red": "#ff0000", "lime": "#00ff00", "green": "#008000",
    "blue": "#0000ff", "yellow": "#ffff00", "cyan": "#00ffff", "aqua": "#00ffff", "magenta": "#ff00ff",
    "fuchsia": "#ff00ff", "silver": "#c0c0c0", "gray": "#808080", "grey": "#808080", "maroon": "#800000",
    "olive": "#808000", "purple": "#800080", "teal": "#008080", "navy": "#000080", "orange": "#ffa500",
    "pink": "#ffc0cb", "brown": "#a52a2a", "gold": "#ffd700", "violet": "#ee82ee", "indigo": "#4b0082",
    "turquoise": "#40e0d0", "salmon": "#fa8072", "coral": "#ff7f50", "khaki": "#f0e68c", "beige": "#f5f5dc",
    "tan": "#d2b48c", "crimson": "#dc143c", "lavender": "#e6e6fa", "chocolate": "#d2691e",
}


def _signal(number: int | None, name: str | None) -> str:
    import signal
    try:
        found = signal.Signals(number) if number is not None else signal.Signals[name]
    except (ValueError, KeyError) as error:
        raise ToolError("that is not a signal on this system") from error
    note = _SIGNAL_NOTES.get(found.name, signal.strsignal(found) or "")
    return f"{found.name} ({int(found)}): {note}"


def _permissions(mode: str) -> str:
    special = int(mode[0]) if len(mode) == 4 else 0
    digits = mode[-3:]
    parts = []
    letters = ""
    for who, digit in zip(("owner", "group", "others"), digits):
        value = int(digit)
        can = [word for bit, word in ((4, "read"), (2, "write"), (1, "execute")) if value & bit]
        letters += "".join(char if value & bit else "-" for bit, char in ((4, "r"), (2, "w"), (1, "x")))
        parts.append(f"{who}: {', '.join(can) if can else 'nothing'}")
    extra = [word for bit, word in ((4, "setuid"), (2, "setgid"), (1, "sticky")) if special & bit]
    return f"{letters}\n" + "\n".join(parts) + (f"\nalso: {', '.join(extra)}" if extra else "")


def _reference(low: str) -> str | None:
    mean = r"(?:what(?:'s| is| does)|explain|meaning of)"
    match = re.fullmatch(
        rf"(?:{mean} )?(?:an? |the )?(?:exit|return) (?:code|status)(?: of)? (?P<n>\d{{1,3}})(?: mean| stand for)?"
        r"|why did (?:it|the (?:command|program|process)) exit (?:with )?(?:code |status )?(?P<n2>\d{1,3})", low)
    if match:
        code = int(match["n"] or match["n2"])
        if code > 255:
            raise ToolError("an exit status is 0 to 255")
        if code in _EXIT_NOTES:
            return f"exit {code}: {_EXIT_NOTES[code]}"
        if 128 < code < 255:
            try:
                return f"exit {code}: ended by signal {code - 128}, {_signal(code - 128, None)}"
            except ToolError:
                pass
        return f"exit {code}: an error the program defines; its own manual says which"
    match = re.fullmatch(
        rf"(?:{mean} )?(?:the )?signal (?P<n>\d{{1,2}})(?: mean)?"
        rf"|(?:{mean} )?(?:the )?(?:signal )?(?P<name>sig[a-z0-9]{{2,8}})(?: signal)?(?: mean| do)?"
        r"|kill -(?P<n2>\d{1,2})", low)
    if match:
        if match["name"]:
            return _signal(None, match["name"].upper())
        return _signal(int(match["n"] or match["n2"]), None)
    match = re.fullmatch(
        rf"(?:{mean} )?(?:an? |the )?(?:http )?(?:status |error |code |status code |response )?(?P<n>[1-5]\d\d)"
        r"(?: error| status| code| response)?(?: mean)?", low)
    if match and re.search(r"http|status|error|code|response|mean", low):
        from http import HTTPStatus
        try:
            status = HTTPStatus(int(match["n"]))
        except ValueError as error:
            raise ToolError("that is not a registered HTTP status") from error
        detail = f": {status.description}" if status.description else ""
        return f"HTTP {int(status)} {status.phrase}{detail}"
    match = re.fullmatch(
        rf"(?:{mean} )?errno (?P<n>\d{{1,3}})(?: mean)?|(?:{mean} )?(?:the error )?(?P<name>e[a-z0-9]{{3,14}})(?: mean| error)?", low)
    if match:
        import errno
        import os
        if match["n"]:
            number = int(match["n"])
        else:
            number = getattr(errno, match["name"].upper(), None)
            if not isinstance(number, int):
                return None
        name = errno.errorcode.get(number)
        if name is None:
            raise ToolError("that is not an error number on this system")
        return f"{name} ({number}): {os.strerror(number)}"
    match = re.fullmatch(
        rf"(?:{mean} )?(?:chmod |permissions? |mode |file mode )(?P<m>[0-7]{{3,4}})(?: mean| do)?"
        rf"|(?:{mean} )?(?P<m2>[0-7]{{3,4}}) permissions?(?: mean)?", low)
    if match:
        return _permissions(match["m"] or match["m2"])
    match = re.fullmatch(
        rf"(?:{mean} )?(?P<p>[r-][w-][xsS-][r-][w-][xsS-][r-][w-][xtT-])(?: in octal| as a number| mean)?"
        r"|(?:chmod|octal|mode) (?:for|of) (?P<p2>[r-][w-][x-][r-][w-][x-][r-][w-][x-])", low)
    if match:
        letters = match["p"] or match["p2"]
        digits = "".join(str(sum(bit for bit, char in zip((4, 2, 1), letters[i:i + 3]) if char not in "-ST"))
                         for i in (0, 3, 6))
        return digits
    match = re.fullmatch(
        r"(?:what|which) port (?:is|does|for) (?P<s>[a-z0-9-]{2,20})(?: use| on| run on| listen on)?"
        r"|(?:what(?:'s| is) )?the (?:default )?port (?:for|of) (?P<s2>[a-z0-9-]{2,20})"
        r"|(?:what(?:'s| is) )?(?:the )?(?P<s3>[a-z0-9-]{2,20}) port(?: number)?", low)
    if match:
        import socket
        name = match["s"] or match["s2"] or match["s3"]
        extra = {"postgres": 5432, "postgresql": 5432, "mysql": 3306, "redis": 6379, "mongodb": 27017,
                 "rdp": 3389, "vnc": 5900, "ollama": 11434, "minecraft": 25565, "wireguard": 51820}
        for proto in ("tcp", "udp"):
            try:
                return f"{name}: {socket.getservbyname(name, proto)}/{proto}"
            except OSError:
                continue
        if name in extra:
            return f"{name}: {extra[name]}/{'udp' if name == 'wireguard' else 'tcp'}"
        return None
    match = re.fullmatch(
        r"(?:what(?:'s| is)|what (?:service )?(?:uses|runs on|is on)) port (?P<n>\d{1,5})(?: for| used for)?"
        r"|what(?:'s| is) port (?P<n2>\d{1,5}) (?:for|used for)", low)
    if match:
        import socket
        number = int(match["n"] or match["n2"])
        if not 1 <= number <= 65535:
            raise ToolError("a port is between 1 and 65535")
        names = []
        for proto in ("tcp", "udp"):
            try:
                names.append(f"{socket.getservbyport(number, proto)}/{proto}")
            except OSError:
                continue
        return f"port {number}: {', '.join(names)}" if names else f"port {number} has no registered service"
    match = re.fullmatch(
        r"(?:what(?:'s| is) )?(?:the )?(?:hex|rgb|html|css)(?: color)?(?: code| value)? (?:for|of) (?:the color )?(?P<c>[a-z]{3,10})"
        r"|(?:what(?:'s| is) )?(?P<c2>[a-z]{3,10}) in (?:hex|rgb)", low)
    if match:
        name = match["c"] or match["c2"]
        if name not in _COLORS:
            return None
        code = _COLORS[name]
        red, green, blue = (int(code[i:i + 2], 16) for i in (1, 3, 5))
        return f"{code}, rgb({red}, {green}, {blue})"
    match = re.fullmatch(r"(?:what (?:color|colour) is |(?:what(?:'s| is) )?(?:the )?rgb (?:of|for) )?#?(?P<h>[0-9a-f]{6})(?: in rgb)?", low)
    if match and ("#" in low or "rgb" in low or "color" in low or "colour" in low):
        code = "#" + match["h"]
        red, green, blue = (int(code[i:i + 2], 16) for i in (1, 3, 5))
        named = next((name for name, value in _COLORS.items() if value == code), "")
        return f"rgb({red}, {green}, {blue})" + (f", {named}" if named else "")
    return None


def answer(text: str) -> tuple[int, str]:
    """Return a status and the text to print. Status 3 is not one of these."""
    text = clean(text)
    if not text or len(text) > _MAX_TEXT:
        return NOT_TOOL, ""
    low = text.lower()
    for attempt in (
        lambda: _random(text, low),
        lambda: _reference(low),
        lambda: _text_tools(text, low),
        lambda: _maths(low),
        lambda: None if _CURRENCY.search(low) else _convert(low),
    ):
        try:
            found = attempt()
        except ValueError:
            found = None
        except (OverflowError, InvalidOperation, ZeroDivisionError) as error:
            raise ToolError("that has no finite answer") from error
        if found is not None:
            return 0, found
    return NOT_TOOL, ""


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] != ["--ask"]:
        print("usage: kilix_tools.py --ask TEXT", file=sys.stderr)
        return 2
    try:
        status, text = answer(" ".join(argv[1:]))
    except ToolError as error:
        print(f"kilix ask: {error}", file=sys.stderr)
        return 1
    if text:
        print(text)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
