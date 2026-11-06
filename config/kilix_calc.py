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
_TENS_ONES = re.compile(
    r"\b(twenty|thirty|forty|fifty|sixty|seventy|eighty|ninety)"
    r"[-\s]+(one|two|three|four|five|six|seven|eight|nine)\b",
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
_WORD = re.compile(r"\b(" + "|".join(_WORDS) + r")\b", re.IGNORECASE)
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


def lower(expression: str) -> str:
    """Turn a question or a written sum into an arithmetic expression."""
    text = expression.strip().replace("’", "'").replace("×", "x")
    text = re.sub(r"[?!.]+$", "", text).strip()
    text = _PREFIX.sub("", text).strip()

    def tens_ones(match: re.Match[str]) -> str:
        return str(int(_WORDS[match.group(1).lower()]) + int(_WORDS[match.group(2).lower()]))

    text = _TENS_ONES.sub(tens_ones, text)
    text = _WORD.sub(lambda match: _WORDS[match.group(1).lower()], text)
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
    if isinstance(value, float) and value.is_integer() and abs(value) < 1e15:
        return str(int(value))
    return f"{value:.10g}"


def ask_query(text: str) -> str | None:
    """Return the result when the whole request is one calculation.

    Anything else, including opening the calculator, returns None so Ask can
    keep its normal route. A request that is only a broken sum raises.
    """
    raw = text.strip()
    if not raw or _APP.search(raw):
        return None
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
        print(format_result(evaluate(" ".join(argv))))
    except CalculatorError as error:
        print(f"kilix calculator: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
