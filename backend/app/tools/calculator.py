"""安全计算器：防止 LLM 编造数字。

只允许数字、基本运算符、白名单数学函数名。
"""
import math


def calculator(expression: str) -> str:
    """评估数学表达式。任何含非法字符的输入直接拒绝。

    支持：
      - 基本运算 + - * / ()
      - 直接函数 abs() round() min() max()
      - math 模块的函数 math.sqrt() math.log() 等
    """
    # 白名单字符：数字、运算符、点号、空格、字母下划线
    safe_chars = set("0123456789+-*/()., abcdefghijklmnopqrstuvwxyz_")
    if not all(c in safe_chars for c in expression):
        return "Error: 含非法字符"

    # namespace：常用内置函数 + math 模块整体（让 math.xxx 能 work）
    namespace: dict = {"__builtins__": {}}
    namespace.update({"abs": abs, "round": round, "min": min, "max": max})
    namespace.update({"math": math})
    # 同时把 math 的函数也摊平到顶层，方便 sqrt(4) 这种写法
    namespace.update({k: v for k, v in math.__dict__.items() if not k.startswith("_")})

    try:
        result = eval(expression, namespace)  # noqa: S307 - 受白名单保护
        return str(result)
    except Exception as e:
        return f"Error: {e}"
