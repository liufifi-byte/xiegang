"""工具测试（不打真实 API）。"""
from app.tools.calculator import calculator


def test_calculator_basic_arithmetic():
    assert calculator("2 + 2") == "4"
    assert calculator("10 * 5") == "50"
    assert calculator("100 / 4") == "25.0"


def test_calculator_handles_float():
    result = calculator("1.5 + 2.5")
    assert "4" in result


def test_calculator_with_functions():
    assert "2.0" in calculator("math.sqrt(4)")
    assert "100" in calculator("abs(-100)")


def test_calculator_rejects_dunder_import():
    """关键测试：阻止 __import__ 之类注入。"""
    result = calculator("__import__('os')")
    assert "Error" in result


def test_calculator_rejects_semicolon_injection():
    result = calculator("1; print('hack')")
    assert "Error" in result


def test_topic_specific_tools_callable():
    """只验证可调用，不打真实 API。"""
    from app.tools.topic_specific import (
        historical_risk_lookup, market_window_check,
        trend_research, contrarian_search, data_table,
    )
    for fn in [historical_risk_lookup, market_window_check,
               trend_research, contrarian_search, data_table]:
        assert callable(fn)
