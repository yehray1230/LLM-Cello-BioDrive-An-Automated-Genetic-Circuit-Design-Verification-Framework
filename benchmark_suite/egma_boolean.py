from __future__ import annotations

from dataclasses import dataclass
from itertools import product
import re
from typing import Mapping


_TOKEN_PATTERN = re.compile(
    r"\s*(?:(?P<identifier>[A-Za-z][A-Za-z0-9_]*)|"
    r"(?P<operator>!|~|&&|&|\|\||\||\(|\)))"
)
_OPERATORS = {"NOT", "AND", "OR"}


class BooleanExpressionError(ValueError):
    """Raised when an expression is outside the frozen EGMA Boolean grammar."""


@dataclass(frozen=True)
class BooleanNode:
    kind: str
    value: str | None = None
    left: BooleanNode | None = None
    right: BooleanNode | None = None


def _tokenize(expression: str) -> list[str]:
    if not isinstance(expression, str) or not expression.strip():
        raise BooleanExpressionError("Boolean expression must be a non-empty string.")
    tokens: list[str] = []
    position = 0
    while position < len(expression):
        if expression[position:].strip() == "":
            break
        match = _TOKEN_PATTERN.match(expression, position)
        if match is None:
            raise BooleanExpressionError(
                f"Unexpected token at character {position}: {expression[position]!r}."
            )
        token = match.group("identifier") or match.group("operator")
        upper = token.upper()
        tokens.append(upper if upper in _OPERATORS else token)
        position = match.end()
    return tokens


class _Parser:
    def __init__(self, tokens: list[str]) -> None:
        self.tokens = tokens
        self.position = 0

    def parse(self) -> BooleanNode:
        node = self._parse_or()
        if self.position != len(self.tokens):
            raise BooleanExpressionError(
                f"Unexpected token {self.tokens[self.position]!r}."
            )
        return node

    def _peek(self) -> str | None:
        if self.position >= len(self.tokens):
            return None
        return self.tokens[self.position]

    def _accept(self, *tokens: str) -> bool:
        if self._peek() not in tokens:
            return False
        self.position += 1
        return True

    def _parse_or(self) -> BooleanNode:
        node = self._parse_and()
        while self._accept("OR", "|", "||"):
            node = BooleanNode("OR", left=node, right=self._parse_and())
        return node

    def _parse_and(self) -> BooleanNode:
        node = self._parse_not()
        while self._accept("AND", "&", "&&"):
            node = BooleanNode("AND", left=node, right=self._parse_not())
        return node

    def _parse_not(self) -> BooleanNode:
        if self._accept("NOT", "!", "~"):
            return BooleanNode("NOT", left=self._parse_not())
        return self._parse_primary()

    def _parse_primary(self) -> BooleanNode:
        if self._accept("("):
            node = self._parse_or()
            if not self._accept(")"):
                raise BooleanExpressionError("Missing closing parenthesis.")
            return node
        token = self._peek()
        if token is None:
            raise BooleanExpressionError("Unexpected end of expression.")
        if token in _OPERATORS or token in {"!", "~", "&&", "&", "||", "|", ")"}:
            raise BooleanExpressionError(f"Expected an input symbol, got {token!r}.")
        self.position += 1
        return BooleanNode("SYMBOL", value=token)


def parse_boolean_expression(expression: str) -> BooleanNode:
    """Parse NOT/AND/OR with precedence NOT > AND > OR."""

    return _Parser(_tokenize(expression)).parse()


def expression_symbols(node: BooleanNode) -> frozenset[str]:
    if node.kind == "SYMBOL":
        if node.value is None:
            raise BooleanExpressionError("Symbol node is missing its value.")
        return frozenset({node.value})
    symbols: set[str] = set()
    if node.left is not None:
        symbols.update(expression_symbols(node.left))
    if node.right is not None:
        symbols.update(expression_symbols(node.right))
    return frozenset(symbols)


def canonical_expression(node: BooleanNode) -> str:
    if node.kind == "SYMBOL":
        if node.value is None:
            raise BooleanExpressionError("Symbol node is missing its value.")
        return node.value
    if node.kind == "NOT" and node.left is not None:
        return f"(NOT {canonical_expression(node.left)})"
    if node.kind in {"AND", "OR"} and node.left is not None and node.right is not None:
        return (
            f"({canonical_expression(node.left)} {node.kind} "
            f"{canonical_expression(node.right)})"
        )
    raise BooleanExpressionError(f"Invalid Boolean AST node: {node.kind!r}.")


def evaluate_boolean(node: BooleanNode, values: Mapping[str, bool | int]) -> bool:
    if node.kind == "SYMBOL":
        if node.value not in values:
            raise BooleanExpressionError(f"Missing value for symbol {node.value!r}.")
        return bool(values[node.value])
    if node.kind == "NOT" and node.left is not None:
        return not evaluate_boolean(node.left, values)
    if node.kind == "AND" and node.left is not None and node.right is not None:
        return evaluate_boolean(node.left, values) and evaluate_boolean(
            node.right, values
        )
    if node.kind == "OR" and node.left is not None and node.right is not None:
        return evaluate_boolean(node.left, values) or evaluate_boolean(
            node.right, values
        )
    raise BooleanExpressionError(f"Invalid Boolean AST node: {node.kind!r}.")


def canonical_truth_table(
    expression: str,
    input_symbols: list[str],
    output_symbol: str,
) -> list[dict[str, int]]:
    """Return rows in ascending binary order for the declared input ordering."""

    if len(input_symbols) not in {2, 3}:
        raise BooleanExpressionError("EGMA tasks require exactly two or three inputs.")
    if len(set(input_symbols)) != len(input_symbols):
        raise BooleanExpressionError("Input symbols must be unique.")
    if output_symbol in input_symbols:
        raise BooleanExpressionError("Output symbol must differ from every input.")
    node = parse_boolean_expression(expression)
    declared = frozenset(input_symbols)
    used = expression_symbols(node)
    if used != declared:
        missing = sorted(declared - used)
        unexpected = sorted(used - declared)
        details = []
        if missing:
            details.append(f"unused declared inputs: {', '.join(missing)}")
        if unexpected:
            details.append(f"undeclared symbols: {', '.join(unexpected)}")
        raise BooleanExpressionError("; ".join(details))
    rows: list[dict[str, int]] = []
    for bits in product((0, 1), repeat=len(input_symbols)):
        values = dict(zip(input_symbols, bits, strict=True))
        row = dict(values)
        row[output_symbol] = int(evaluate_boolean(node, values))
        rows.append(row)
    return rows
