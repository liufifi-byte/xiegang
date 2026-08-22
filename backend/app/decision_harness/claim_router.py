"""待确认项分流路由（2026-08-22 彩排复盘：用户型问题被派去搜索的根因修复）。

blocking_unknowns 里混着两类问题：
- user 型：只有用户本人才知道（家庭状况/个人偏好/私有财产细节）——
  搜索引擎永远查不到，查回来的是导购软文；应转成"问用户"；
- fact 型：公开可查证（政策/价格/法律条文/行业数据）——走搜索+证据闸门。

启发式分类（确定性、零成本、可离线测试）。判不准时保守走 fact
（宁可白搜一次，不把该问用户的憋回去——搜过没有 evidence 自然落回
unknown，最终仍会进 remaining_uncertainties）。
"""
from __future__ import annotations

import re

# 用户私有信息的强信号（自己/家庭/偏好/身体/具体持有物）
_USER_PATTERNS = re.compile(
    r"我(们|自己|家|的)|父母|配偶|丈夫|妻子|爱人|孩子|老人|家人|家底|"
    r"存款|工资|年薪|月薪|收入|积蓄|房贷|车(是|型|况|龄)|我开|家里那台|"
    r"意愿|偏好|想要|打算|顾虑|底线|健康|病史|体检|婚|恋|感情|"
    r"现在(的|这份|这家)|目前(的|这份|这家)|本(人|公司|部门)"
)

# 公开事实的强信号（可查证的外部世界）
_FACT_PATTERNS = re.compile(
    r"政策|补贴|法规|法律|条文|规定|税率|利率|价格|市场|行情|行业|"
    r"统计|数据|报告|研究|论文|标准|资质|排名|对比|评测|IRR|通胀|"
    r"汇率|房价|租金|薪酬|offer 行情|公司(财报|背景|口碑)|竞业(协议|限制)的(法律|司法)"
)

# 问题式措辞（"你的/您/什么型号"= 问人的）
_QUESTION_TO_USER = re.compile(
    r"(具体|多少|哪个|哪些|什么)(型号|版本|数字|金额|数额|水平|情况|想法|意见|态度)"
)


def classify_unknown(text: str) -> str:
    """返回 "user" 或 "fact"。user 强信号优先（误判成搜索的代价更高）。"""
    if not text:
        return "fact"
    if _QUESTION_TO_USER.search(text) and _USER_PATTERNS.search(text):
        return "user"
    user_score = len(_USER_PATTERNS.findall(text))
    fact_score = len(_FACT_PATTERNS.findall(text))
    if user_score > 0 and user_score >= fact_score:
        return "user"
    return "fact"


def split_unknowns(items: list[str]) -> tuple[list[str], list[str]]:
    """(user 型, fact 型) 两清单，保持原序。"""
    user = [t for t in items if classify_unknown(t) == "user"]
    fact = [t for t in items if classify_unknown(t) == "fact"]
    return user, fact
