"""A deliberately small arithmetic evaluator for expert agents."""

from __future__ import annotations

import ast
import math
import operator
import re
from typing import Mapping


class SafeCalculatorError(ValueError):
    """Raised when an expression requests behavior outside the safe grammar."""


_BINARY_OPERATORS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPERATORS = {ast.UAdd: operator.pos, ast.USub: operator.neg}
_PERCENT = re.compile(r"(?<![\w.])(\d+(?:\.\d+)?)%")
_FUNCTIONS = {
    "abs": abs,
    "round": round,
    "min": min,
    "max": max,
    "sqrt": math.sqrt,
    "log": math.log,
    "log10": math.log10,
    "exp": math.exp,
    "floor": math.floor,
    "ceil": math.ceil,
}


def _number(value: object) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise SafeCalculatorError("only numeric values are allowed")
    result = float(value)
    if not math.isfinite(result):
        raise SafeCalculatorError("non-finite values are not allowed")
    return result


def _eval_node(node: ast.AST, variables: Mapping[str, float]) -> float:
    if isinstance(node, ast.Constant):
        return _number(node.value)
    if isinstance(node, ast.Name):
        if node.id not in variables:
            raise SafeCalculatorError(f"unknown variable: {node.id}")
        return _number(variables[node.id])
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPERATORS:
        return _UNARY_OPERATORS[type(node.op)](_eval_node(node.operand, variables))
    if isinstance(node, ast.BinOp) and type(node.op) in _BINARY_OPERATORS:
        left = _eval_node(node.left, variables)
        right = _eval_node(node.right, variables)
        if isinstance(node.op, ast.Pow) and abs(right) > 12:
            raise SafeCalculatorError("exponent is too large")
        try:
            result = _BINARY_OPERATORS[type(node.op)](left, right)
        except (ArithmeticError, OverflowError) as exc:
            raise SafeCalculatorError(str(exc)) from exc
        return _number(result)
    if isinstance(node, ast.Call) and not node.keywords:
        function_name = None
        if isinstance(node.func, ast.Name):
            function_name = node.func.id
        elif (
            isinstance(node.func, ast.Attribute)
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "math"
        ):
            function_name = node.func.attr
        if function_name not in _FUNCTIONS:
            raise SafeCalculatorError("function is not allowed")
        arguments = [_eval_node(argument, variables) for argument in node.args]
        try:
            return _number(_FUNCTIONS[function_name](*arguments))
        except (ArithmeticError, TypeError, ValueError) as exc:
            raise SafeCalculatorError(str(exc)) from exc
    raise SafeCalculatorError(f"unsupported expression: {type(node).__name__}")


def _evaluate(expression: str, variables: Mapping[str, float] | None = None) -> float:
    if not expression or len(expression) > 500:
        raise SafeCalculatorError("expression must be between 1 and 500 characters")
    normalized = _PERCENT.sub(r"(\1/100)", expression)
    try:
        parsed = ast.parse(normalized, mode="eval")
    except SyntaxError as exc:
        raise SafeCalculatorError("invalid arithmetic expression") from exc
    return _eval_node(parsed.body, variables or {})


def safe_calculate(expression: str, variables: Mapping[str, float] | None = None) -> float:
    """Evaluate arithmetic without imports, arbitrary attributes, or code execution."""
    result = _evaluate(expression, variables)
    return int(result) if result.is_integer() else result


def calculator(expression: str) -> str:
    """Backward-compatible string interface used by the legacy council tools."""
    try:
        value = _evaluate(expression)
        preserves_float = any(
            marker in expression for marker in ("/", ".", "sqrt", "log", "exp")
        )
        if value.is_integer() and not preserves_float:
            return str(int(value))
        return str(value)
    except SafeCalculatorError as exc:
        return f"Error: {exc}"
