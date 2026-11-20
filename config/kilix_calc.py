"""Exact arithmetic for Kilix Ask and `kilix calculator`.

A calculation is a closed expression, so it is answered here and does not go
to a command model or a Needle job. The expression is parsed with ast and
walked over an allowlist. eval() is not used. The same lowering lives in
kilix-calculator; keep the two allowlists the same.
"""

from __future__ import annotations

import ast
import operator
import re
import sys

NOT_CALCULATION = 3
_MAX_EXPONENT = 1024
_MAX_DIGITS = 4096

_BINARY = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY = {ast.UAdd: operator.pos, ast.USub: operator.neg}

_PREFIX = re.compile(
    r"^(?:please\s+)?(?:what(?:\s+is|'s)|whats|how\s+much\s+is|calculate|compute)\s+",
    re.IGNORECASE,
)
_WORDS = {
    "zero": "0", "one": "1", "two": "2", "three": "3", "four": "4",
    "five": "5", "six": "6", "seven": "7", "eight": "8", "nine": "9",
    "ten": "10", "eleven": "11", "twelve": "12", "thirteen": "13",
    "fourteen": "14", "fifteen": "15", "sixteen": "16", "seventeen": "17",
    "eighteen": "18", "nineteen": "19", "twenty": "20", "thirty": "30",
    "forty": "40", "fifty": "50", "sixty": "60", "seventy": "70",
    "eighty": "80", "ninety": "90",
}
_SMALL = {word: int(value) for word, value in _WORDS.items()}
_SCALE = {
    "hundred": 100,
    "thousand": 1_000,
    "million": 1_000_000,
    "billion": 1_000_000_000,
}
_NUM_WORD = "|".join(sorted(
    [*_SMALL, *_SCALE, "a", "an", "and"], key=len, reverse=True,
))
# A hyphen joins a written number ("two-thousand", "twenty-one"). A spaced
# hyphen stays subtraction.
_SPOKEN = re.compile(
    rf"\b(?:{_NUM_WORD})(?:(?:[ \t]+|-)(?:{_NUM_WORD}))*\b",
    re.IGNORECASE,
)
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
_AND = re.compile(r"(?<=\d)\s+and\s+(?=\d)", re.IGNORECASE)
_PERCENT_OF = re.compile(
    r"(\d+(?:\.\d+)?)\s*(?:%|percent)\s+of\s+(\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
_PHRASES = (
    (re.compile(r"\bmultiplied\s+by\b", re.IGNORECASE), "*"),
    (re.compile(r"\bdivided\s+by\b", re.IGNORECASE), "/"),
    (re.compile(r"\btimes\b", re.IGNORECASE), "*"),
    (re.compile(r"\bplus\b", re.IGNORECASE), "+"),
    (re.compile(r"\bminus\b", re.IGNORECASE), "-"),
)
_TIMES_X = re.compile(r"(?<=[\d)])\s*[x×]\s*(?=[\d(])", re.IGNORECASE)
_EXPR = re.compile(r"^[0-9.+\-*/%()\s]+$")
_APP = re.compile(
    r"\b(?:open|launch|start|install|close|quit)\b.*\bcalculat",
    re.IGNORECASE,
)


class CalculatorError(Exception):
    """A rejected or unevaluable expression."""


def _cardinal(parts: list[str]) -> int | None:
    """One written number. A second hundred in the same group is not one number."""
    current = 0
    total = 0
    used = False
    saw_hundred = False
    for index, part in enumerate(parts):
        if part == "and":
            return None
        if part in {"a", "an"}:
            nxt = parts[index + 1] if index + 1 < len(parts) else ""
            if nxt not in _SCALE:
                return None
            current += 1
            used = True
            continue
        if part in _SMALL:
            current += _SMALL[part]
            used = True
            continue
        if part not in _SCALE:
            return None
        scale = _SCALE[part]
        if scale == 100:
            if saw_hundred:
                return None
            saw_hundred = True
            current = (current or 1) * 100
        else:
            if current == 0 and total != 0:
                return None
            total += (current or 1) * scale
            current = 0
        used = True
    if not used:
        return None
    return total + current


def _pending_hundred(parts: list[str]) -> int | None:
    """The value when a hundred is still open, before a larger scale consumes it."""
    current = 0
    total = 0
    used = False
    pending = False
    saw_hundred = False
    for index, part in enumerate(parts):
        if part in {"a", "an"}:
            nxt = parts[index + 1] if index + 1 < len(parts) else ""
            if nxt not in _SCALE:
                return None
            current += 1
            used = True
            continue
        if part in _SMALL:
            current += _SMALL[part]
            used = True
            continue
        if part not in _SCALE:
            return None
        scale = _SCALE[part]
        if scale == 100:
            if saw_hundred:
                return None
            saw_hundred = True
            current = (current or 1) * 100
            pending = True
        else:
            if current == 0 and total != 0:
                return None
            total += (current or 1) * scale
            current = 0
            pending = False
        used = True
    if not used or not pending:
        return None
    return total + current


def _remainder(parts: list[str]) -> tuple[int, int] | None:
    """A remainder below 100, optionally times one larger scale.

    The scale multiplies the whole cardinal ("two hundred and fifty thousand"
    is 250 thousand), so this returns the small value and that multiplier.
    """
    if not parts:
        return None
    current = 0
    multiplier = 1
    saw_small = False
    saw_scale = False
    for part in parts:
        if part in _SMALL:
            if saw_scale:
                return None
            current += _SMALL[part]
            saw_small = True
            continue
        if part == "hundred" or part not in _SCALE or part in {"a", "an"}:
            return None
        if not saw_small or current >= 100 or saw_scale:
            return None
        multiplier = _SCALE[part]
        saw_scale = True
    if not saw_small or current <= 0 or current >= 100:
        return None
    return current, multiplier


def _spoken_number(span: str) -> str | None:
    """Read one written number, or a sum when "and" joins separate numbers.

    "and" is inside one cardinal only after a hundred that is still open,
    followed by a remainder below 100 ("two hundred and fifty", and
    "two hundred and fifty thousand"). Any other "and" is addition, so
    "two and two" and "two and two hundred" stay sums. The result may be
    an expression such as "2 + 200".
    """
    parts = [part for part in re.split(r"[\s-]+", span.strip().lower()) if part]
    if not parts:
        return None
    if "and" not in parts:
        value = _cardinal(parts)
        return None if value is None else str(value)
    groups: list[list[str]] = []
    current_group: list[str] = []
    for part in parts:
        if part == "and":
            groups.append(current_group)
            current_group = []
        else:
            current_group.append(part)
    groups.append(current_group)
    values: list[int] = []
    index = 0
    while index < len(groups):
        group = groups[index]
        if not group:
            return None
        if index + 1 < len(groups):
            base = _pending_hundred(group)
            remainder = _remainder(groups[index + 1])
            if base is not None and remainder is not None:
                small, multiplier = remainder
                values.append((base + small) * multiplier)
                index += 2
                continue
        value = _cardinal(group)
        if value is None:
            return None
        values.append(value)
        index += 1
    if len(values) == 1:
        return str(values[0])
    return " + ".join(str(value) for value in values)


def lower(expression: str) -> str:
    """Turn a question or a written sum into an arithmetic expression."""
    text = expression.strip().replace("’", "'").replace("×", "x")
    text = re.sub(r"[?!.]+$", "", text).strip()
    text = _PREFIX.sub("", text).strip()
    text = _SPOKEN.sub(
        lambda match: _spoken_number(match.group(0)) or match.group(0), text)
    text = _WORD.sub(lambda match: _WORDS[match.group(1).lower()], text)
    text = _AND.sub(" + ", text)
    text = _PERCENT_OF.sub(r"((\1)/100)*(\2)", text)
    for pattern, symbol in _PHRASES:
        text = pattern.sub(symbol, text)
    return _TIMES_X.sub("*", text)


def _eval(node: ast.AST) -> float:
    if isinstance(node, ast.Expression):
        return _eval(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalculatorError("only numbers are allowed")
        return node.value
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY:
        return _UNARY[type(node.op)](_eval(node.operand))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY:
        left, right = _eval(node.left), _eval(node.right)
        if isinstance(node.op, ast.Pow) and abs(right) > _MAX_EXPONENT:
            raise CalculatorError(f"exponent above {_MAX_EXPONENT}")
        if isinstance(node.op, (ast.Div, ast.FloorDiv, ast.Mod)) and right == 0:
            raise CalculatorError("division by zero")
        return _BINARY[type(node.op)](left, right)
    raise CalculatorError("unsupported expression")


def evaluate(expression: str) -> float:
    """Evaluate an arithmetic expression, or raise CalculatorError."""
    if len(expression) > _MAX_DIGITS:
        raise CalculatorError("expression too long")
    converted = conversion(expression)
    if converted is not None:
        return converted[0]
    expression = lower(expression)
    if len(expression) > _MAX_DIGITS:
        raise CalculatorError("expression too long")
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as error:
        raise CalculatorError("syntax error") from error
    try:
        return _eval(tree)
    except CalculatorError:
        raise
    except (ArithmeticError, ValueError) as error:
        raise CalculatorError(str(error)) from error


def format_result(value: float) -> str:
    """Print an integer exactly. A float that is a whole number stays short."""
    if isinstance(value, bool):
        raise CalculatorError("only numbers are allowed")
    if isinstance(value, int):
        text = str(value)
        if len(text.lstrip("-")) > _MAX_DIGITS:
            raise CalculatorError("result too long")
        return text
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


_DATA_PREFIXES = {
    "": ("", 1), "k": ("kilo", 1000), "m": ("mega", 1000**2), "g": ("giga", 1000**3),
    "t": ("tera", 1000**4), "p": ("peta", 1000**5), "ki": ("kibi", 1024),
    "mi": ("mebi", 1024**2), "gi": ("gibi", 1024**3), "ti": ("tebi", 1024**4), "pi": ("pebi", 1024**5),
}
_DATA_SYMBOL = re.compile(r"(?<![a-z])([kmgtp]i?)?([bB])(?![a-z])", re.ASCII | re.IGNORECASE)


def expand_data_symbols(text: str) -> str:
    """Preserve the bit/byte distinction before a conversion folds its words to lowercase."""
    def expand(match):
        prefix, _ = _DATA_PREFIXES[(match[1] or "").lower()]
        return prefix + ("byte" if match[2] == "B" else "bit")
    return _DATA_SYMBOL.sub(expand, text)


DATA_UNITS = {}
for _symbol, (_prefix, _size) in _DATA_PREFIXES.items():
    _bit_label = "bits" if not _symbol else ("k" if _symbol == "k" else _symbol.capitalize()) + "b"
    for _word, _scale, _label in (("byte", _size, _symbol.capitalize() + "B"), ("bit", _size / 8, _bit_label)):
        DATA_UNITS[_prefix + _word] = DATA_UNITS[_prefix + _word + "s"] = (_scale, _label)

_LENGTH = {
    alias: (scale, label)
    for scale, label, aliases in (
        (0.001, "mm", "mm millimeter millimeters"), (0.01, "cm", "cm centimeter centimeters"),
        (1, "m", "m meter meters metre metres"), (1000, "km", "km kilometer kilometers kilometre kilometres"),
        (0.0254, "in", "in inch inches"), (0.3048, "ft", "ft foot feet"), (0.9144, "yd", "yd yard yards"),
    )
    for alias in aliases.split()
}
TEMPERATURE_UNITS = {"c": "°C", "celsius": "°C", "centigrade": "°C", "f": "°F",
                     "fahrenheit": "°F", "k": "K", "kelvin": "K"}
_CONVERSION = re.compile(
    r"^(?:how many (?P<to_a>[a-z]+) (?:are )?in )?"
    r"(?P<num>-?\d+(?:\.\d+)?)\s*(?P<src>[a-z]+)"
    r"(?:\s+(?:in|to)\s+(?P<to_b>[a-z]+))?$",
    re.IGNORECASE,
)


def _unit(name: str) -> tuple[str, float, str]:
    key = name.lower()
    if key in DATA_UNITS:
        return "info", *DATA_UNITS[key]
    if key in _LENGTH:
        return "length", *_LENGTH[key]
    if key in TEMPERATURE_UNITS:
        return "temp", 0.0, TEMPERATURE_UNITS[key]
    raise KeyError(key)


def convert_temperature(value: float, source: str, target: str) -> float:
    celsius = {"°C": value, "°F": (value - 32) * 5 / 9, "K": value - 273.15}[source]
    if celsius < -273.15 - 1e-9:
        raise CalculatorError("that is below absolute zero")
    if source == target:
        return value
    return {"°C": celsius, "°F": celsius * 9 / 5 + 32, "K": celsius + 273.15}[target]


def conversion(expression: str) -> tuple[float, str] | None:
    """Return a converted value and its unit label, or None."""
    text = lower(expand_data_symbols(expression)).strip()
    match = _CONVERSION.fullmatch(text)
    if match is None:
        return None
    target = match["to_a"] or match["to_b"]
    if target is None or (match["to_a"] and match["to_b"]):
        return None
    try:
        src_kind, src_scale, src_label = _unit(match["src"])
        dst_kind, dst_scale, dst_label = _unit(target)
    except KeyError:
        return None
    if src_kind != dst_kind:
        raise CalculatorError("those units are not the same kind")
    number = float(match["num"])
    if src_kind == "temp":
        value = convert_temperature(number, src_label, dst_label)
    else:
        value = number * src_scale / dst_scale
    return float(f"{value:.10g}"), dst_label


def ask_query(text: str) -> str | None:
    """Return the result when the whole request is one calculation.

    Anything else, including opening the calculator, returns None so Ask can
    keep its normal route. A request that is only a broken sum raises.
    """
    raw = text.strip()
    if not raw or _APP.search(raw):
        return None
    converted = conversion(raw)
    if converted is not None:
        value, unit = converted
        return f"{format_result(value)} {unit}"
    lowered = lower(raw).strip()
    if not lowered or not _EXPR.fullmatch(lowered) or not re.search(r"[+\-*/%]", lowered):
        return None
    return format_result(evaluate(lowered))


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:1] == ["--ask"]:
        try:
            result = ask_query(" ".join(argv[1:]))
        except CalculatorError as error:
            print(f"kilix calculator: {error}", file=sys.stderr)
            return 1
        if result is None:
            return NOT_CALCULATION
        print(result)
        return 0
    if not argv:
        print("usage: kilix calculator EXPRESSION", file=sys.stderr)
        return 2
    try:
        text = " ".join(argv)
        converted = conversion(text)
        if converted is not None:
            value, unit = converted
            print(f"{format_result(value)} {unit}")
        else:
            print(format_result(evaluate(text)))
    except CalculatorError as error:
        print(f"kilix calculator: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
