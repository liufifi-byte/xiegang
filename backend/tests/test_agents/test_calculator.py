import pytest

from app.tools.calculator import SafeCalculatorError, safe_calculate


def test_calculator_handles_arithmetic_variables_and_percentages():
    assert safe_calculate("salary * (1 + growth)", {"salary": 100, "growth": 0.2}) == 120
    assert safe_calculate("1000 * 12%") == 120
    assert safe_calculate("(10 + 5) ** 2") == 225


@pytest.mark.parametrize(
    "expression",
    [
        "__import__('os').system('id')",
        "open('/etc/passwd').read()",
        "(1).__class__",
        "[x for x in range(3)]",
        "2 ** 999",
    ],
)
def test_calculator_rejects_code_and_unsafe_operations(expression):
    with pytest.raises(SafeCalculatorError):
        safe_calculate(expression)
