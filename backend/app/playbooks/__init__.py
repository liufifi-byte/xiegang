"""决策路径 Playbook - PRD v1.1 §10 三大场景固定模板.

每个 Playbook 提供:
- decision_goal               默认决策目标
- decision_options            比较方案
- evaluation_research_hints   仅评测参考用,不作为固定调研清单展示给用户(PRD v1.1 §6.8)
- candidate_questions         关键问题候选池(只问用户专属信息)
- final_deliverables          最终交付
- fact_keys                   从用户原文里尝试识别的事实 key(labels)
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class QuestionTemplate:
    """Playbook 候选问题模板(L1)。LLM 不应自由发挥超出此结构。"""

    id: str
    question: str
    why_it_matters: str
    answer_type: str  # single_choice / multi_choice / range_choice / number / short_text
    options: tuple[tuple[str, str], ...] = ()  # (value, label)
    allow_custom: bool = False
    unknown_assumption: str = ""

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "question": self.question,
            "why_it_matters": self.why_it_matters,
            "answer_type": self.answer_type,
            "options": [{"value": v, "label": lbl} for v, lbl in self.options],
            "allow_custom": self.allow_custom,
            "unknown_assumption": self.unknown_assumption,
        }


@dataclass(frozen=True)
class Playbook:
    category: str
    decision_goal: str
    decision_options: tuple[str, ...]
    analysis_dimensions: tuple[str, ...]
    # PRD v1.1 §6.8 / §10: 不再作为 fixed autonomous_research 展示给用户
    # 仅留作 Agent 评测判定用(configuration_reference_research_directions)
    evaluation_research_hints: tuple[str, ...]
    final_deliverables: tuple[str, ...]
    candidate_questions: tuple[QuestionTemplate, ...]
    fact_keys: tuple[str, ...] = field(default_factory=tuple)
    keywords: tuple[str, ...] = field(default_factory=tuple)

    def to_brief_dict(self) -> dict:
        return {
            "decision_goal": self.decision_goal,
            "decision_options": list(self.decision_options),
            "analysis_dimensions": list(self.analysis_dimensions),
            "final_deliverables": list(self.final_deliverables),
        }


# PRD §10.1 / §10.2 / §10.3 三套 playbook 全部内嵌(避免再加3个文件)

UNKNOWN_OPTION = ("unknown", "不确定，按保守情况估算")


REAL_ESTATE_PLAYBOOK = Playbook(
    category="real_estate",
    decision_goal="判断用户在当前生活、财务和家庭条件下是否适合购买目标住房，而不是单纯预测房价涨跌。",
    decision_options=("现在购买", "延后六至十八个月购买", "继续租住并保留资金", "降低总价/更换区域"),
    analysis_dimensions=("现金流压力", "租买成本", "持有期限", "生活稳定性"),
    evaluation_research_hints=(
        "当前房贷利率",
        "当地购房政策和交易税费",
        "目标区域成交价和租金",
        "租售比与流动性",
        "可比较房源",
        "必要的历史周期信息",
    ),
    final_deliverables=(
        "当前建议及适用条件",
        "月供、总利息和现金流压力计算",
        "租买成本及三至五年盈亏平衡分析",
        "三种市场情景下的结果",
        "预算上限和安全边界",
        "看房、核验与谈价清单",
        "未来30天行动计划",
        "结论翻转条件",
    ),
    candidate_questions=(
        QuestionTemplate(
            id="housing_purpose",
            question="这套房主要用于？",
            why_it_matters="自住和投资的成本、税务和持有策略完全不同。",
            answer_type="single_choice",
            options=(
                ("self_live", "自住"),
                ("self_live_plus_appreciation", "自住兼顾升值"),
                ("investment", "投资"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按自住需求估算，更看重现金流安全而非升值空间。",
        ),
        QuestionTemplate(
            id="holding_horizon",
            question="预计至少居住或持有多久？",
            why_it_matters="持有期会显著影响交易成本摊销和租买结果。",
            answer_type="range_choice",
            options=(
                ("lt_3", "3年以内"),
                ("3_5", "3—5年"),
                ("5_10", "5—10年"),
                ("gte_10", "10年以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按3年以内的较短持有期进行压力测试。",
        ),
        QuestionTemplate(
            id="relocation_likelihood",
            question="未来三年换城市的可能性？",
            why_it_matters="短期迁徙会显著影响房子使用价值和出售成本。",
            answer_type="single_choice",
            options=(
                ("low", "很低"),
                ("possible", "有可能"),
                ("high", "较高"),
                ("confirmed", "已确定"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按未来三年大概率不会迁移估算。",
        ),
        QuestionTemplate(
            id="down_payment_range",
            question="可用首付大致是多少？",
            why_it_matters="首付决定了贷款总额、月供和资金占用结构。",
            answer_type="range_choice",
            options=(
                ("lt_50", "50万以内"),
                ("50_100", "50—100万"),
                ("100_200", "100—200万"),
                ("gte_200", "200万以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按首付为目标总价的30%估算。",
        ),
        QuestionTemplate(
            id="monthly_payment_ratio",
            question="希望月供控制在收入的多少以内？",
            why_it_matters="月供占比直接决定现金流是否安全。",
            answer_type="range_choice",
            options=(
                ("lt_20", "20%以内"),
                ("20_30", "20%—30%"),
                ("30_40", "30%—40%"),
                ("gte_40", "40%以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按月供不超过税后月收入30%的稳健线估算。",
        ),
        QuestionTemplate(
            id="family_opinion",
            question="家庭意见是否一致？",
            why_it_matters="家庭分歧会显著影响决策可执行性。",
            answer_type="single_choice",
            options=(
                ("aligned", "一致支持"),
                ("mostly", "基本支持"),
                ("divided", "存在分歧"),
                ("not_discussed", "尚未讨论"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按家庭未明确参与、决策由用户自行负责估算。",
        ),
    ),
    fact_keys=("city", "down_payment", "target_total_price"),
    keywords=("买房", "购房", "置业", "房产"),
)


CAREER_PLAYBOOK = Playbook(
    category="offer",  # 复用现有 job_change/offer category, 通过 keywords 区分
    decision_goal="判断新机会是否比继续留任更符合用户的收入、成长、生活和长期职业方向，并明确可谈判条件和退出方案。",
    decision_options=("接受当前机会", "留在原岗位", "谈判后再决定", "延后行动并补足能力或信息"),
    analysis_dimensions=("现金与股权", "成长空间", "工作强度", "机会成本", "可逆性"),
    evaluation_research_hints=(
        "目标行业和岗位趋势",
        "公司公开经营与融资情况",
        "同类岗位薪酬和职责",
        "通用股权、奖金和竞业条款风险",
        "所在城市与行业机会密度",
    ),
    final_deliverables=(
        "接受、拒绝或谈判后接受的建议",
        "现金、股权、成长、强度与风险对比",
        "两至三年职业路径情景",
        "Offer 核验清单",
        "可直接使用的 HR / 上级谈判话术",
        "退出条件和止损方案",
        "30 / 60 / 90 天行动计划",
        "结论翻转条件",
    ),
    candidate_questions=(
        QuestionTemplate(
            id="primary_motivation",
            question="本次变化最主要想解决什么？",
            why_it_matters="明确核心目标会比较选项时的权衡方向。",
            answer_type="single_choice",
            options=(
                ("income", "收入"),
                ("growth", "成长"),
                ("management", "管理机会"),
                ("pivot", "转行"),
                ("work_life", "工作生活平衡"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按职业成长与生活质量综合考量估算。",
        ),
        QuestionTemplate(
            id="income_change",
            question="新机会的稳定收入变化大概是？",
            why_it_matters="现金变化影响生活稳定与决策可承受性。",
            answer_type="range_choice",
            options=(
                ("drop_gt_20", "下降20%以上"),
                ("flat", "基本持平"),
                ("up_10_30", "增长10%—30%"),
                ("up_gt_30", "增长30%以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按收入基本持平估算。",
        ),
        QuestionTemplate(
            id="worst_acceptable",
            question="最难接受的后果是？",
            why_it_matters="明确底线决定放弃条件。",
            answer_type="single_choice",
            options=(
                ("income_drop", "收入下降"),
                ("stagnation", "发展停滞"),
                ("overload", "工作过载"),
                ("company_fail", "公司失败"),
                ("relocation", "城市变化"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按收入、强度、稳定性综合低线估算。",
        ),
        QuestionTemplate(
            id="runway_months",
            question="能承担收入波动的时间？",
            why_it_matters="现金流缓冲决定能否承受下行场景。",
            answer_type="range_choice",
            options=(
                ("lt_3", "不足3个月"),
                ("3_6", "3—6个月"),
                ("6_12", "6—12个月"),
                ("gte_12", "12个月以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按6个月生活缓冲估算。",
        ),
        QuestionTemplate(
            id="fallback_option",
            question="新机会失败后你的退路？",
            why_it_matters="退路影响可逆性和决策风险。",
            answer_type="single_choice",
            options=(
                ("can_return", "可以回原行业"),
                ("other_offer", "有其他Offer"),
                ("rest_search", "可休整再找"),
                ("no_fallback", "暂无退路"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按暂无明确退路估算,决策提示注意风险。",
        ),
        QuestionTemplate(
            id="family_attitude",
            question="家庭或伴侣态度？",
            why_it_matters="家庭稳定影响迁移可行性。",
            answer_type="single_choice",
            options=(
                ("support", "支持"),
                ("conditional", "有条件支持"),
                ("oppose", "反对"),
                ("not_discussed", "尚未讨论"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按家庭可配合估算。",
        ),
        QuestionTemplate(
            id="decision_deadline",
            question="决策截止时间？",
            why_it_matters="时间压力影响谈判深度和验证空间。",
            answer_type="range_choice",
            options=(
                ("lt_1w", "一周内"),
                ("1_2w", "两周内"),
                ("1m", "一个月内"),
                ("longer", "更久"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按两周内做出决定估算。",
        ),
    ),
    fact_keys=("company", "income_change_pct", "deadline"),
    keywords=("offer", "入职", "录用", "跳槽", "转行", "换工作", "辞职"),
)


INVESTMENT_PLAYBOOK = Playbook(
    category="investment",  # gold/stocks/investment 都并入投资
    decision_goal="判断该资产是否适合用户当前的资金用途、持有期限和整体资产配置，而不是只预测短期价格。",
    decision_options=("现在一次性配置", "分批配置", "暂不配置", "使用其他资产实现同一目标"),
    analysis_dimensions=("预期收益", "最大回撤", "流动性", "持有期", "集中度"),
    evaluation_research_hints=(
        "最新价格与估值位置",
        "利率、汇率和宏观驱动",
        "历史回撤与波动",
        "产品费用、流动性和跟踪误差",
        "机构观点及其分歧",
        "相关资产替代方案",
    ),
    final_deliverables=(
        "是否适合配置及建议仓位区间",
        "一次性和分批方案比较",
        "上涨、震荡、下跌三种情景的盈亏计算",
        "买入、观察和退出条件",
        "主要风险与错误信号",
        "未来7天和30天行动清单",
        "结论翻转条件",
        "可查阅的信息来源",
    ),
    candidate_questions=(
        QuestionTemplate(
            id="investment_goal",
            question="这笔投资的主要目标？",
            why_it_matters="目标决定资产类别和持有策略。",
            answer_type="single_choice",
            options=(
                ("preserve", "保值"),
                ("diversify", "分散风险"),
                ("long_growth", "长期增值"),
                ("short_profit", "短期获利"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按兼顾保值与稳健增长估算。",
        ),
        QuestionTemplate(
            id="holding_period",
            question="这笔资金预计多久不用？",
            why_it_matters="持有期决定可承受波动幅度。",
            answer_type="range_choice",
            options=(
                ("lt_1y", "1年以内"),
                ("1_3y", "1—3年"),
                ("3_5y", "3—5年"),
                ("gte_5y", "5年以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按3年以内的中短期估算。",
        ),
        QuestionTemplate(
            id="position_ratio",
            question="本次投入占可支配资金比例？",
            why_it_matters="集中度决定单笔失败的影响面。",
            answer_type="range_choice",
            options=(
                ("lt_20", "不到20%"),
                ("20_40", "20%—40%"),
                ("40_60", "40%—60%"),
                ("gte_60", "60%以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按资金占比20%以内估算,分散原则。",
        ),
        QuestionTemplate(
            id="max_drawdown",
            question="最多能接受的浮亏？",
            why_it_matters="回撤承受力决定止损策略。",
            answer_type="range_choice",
            options=(
                ("lt_5", "5%以内"),
                ("around_10", "10%左右"),
                ("around_20", "20%左右"),
                ("gte_30", "30%以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按最大可接受浮亏10%估算,偏稳健。",
        ),
        QuestionTemplate(
            id="emergency_buffer",
            question="当前应急金情况？",
            why_it_matters="应急金不足时投入投资风险高。",
            answer_type="range_choice",
            options=(
                ("lt_3m", "不足3个月"),
                ("3_6m", "3—6个月"),
                ("6_12m", "6—12个月"),
                ("gte_12m", "12个月以上"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按应急金3—6个月估算。",
        ),
        QuestionTemplate(
            id="current_assets",
            question="当前主要资产？(最多选3项)",
            why_it_matters="资产结构影响分散度与配置建议。",
            answer_type="multi_choice",
            options=(
                ("cash", "现金"),
                ("savings", "存款/理财"),
                ("funds", "股票/基金"),
                ("property", "房产"),
                ("other", "其他"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按资产以现金/存款为主估算。",
        ),
    ),
    fact_keys=("amount", "asset_type", "holding_period"),
    keywords=("投资", "基金", "理财", "黄金", "贵金属", "股票", "个股"),
)


# general fallback(用户类别不在3大场景里时用)
GENERAL_PLAYBOOK = Playbook(
    category="general",
    decision_goal="帮助用户梳理并对比该问题的不同方向、各自代价与收益,而不是只给出一个答案。",
    decision_options=("立即行动", "延后到条件更清晰", "选择替代方案", "放弃当前选项"),
    analysis_dimensions=("核心目标", "现实约束", "替代方案", "最坏结果", "可逆性"),
    evaluation_research_hints=(
        "公开案例与同类决策的成功/失败经验",
        "可参考的数据区间",
    ),
    final_deliverables=(
        "结构化对比",
        "关键风险与条件",
        "可立即执行的行动清单",
        "结论翻转条件",
    ),
    candidate_questions=(
        QuestionTemplate(
            id="core_goal",
            question="这次决策你最想达成的核心目标？",
            why_it_matters="核心目标决定后续权衡偏重。",
            answer_type="short_text",
            unknown_assumption="按通用目标(收益+风险平衡)估算。",
        ),
        QuestionTemplate(
            id="dealbreaker",
            question="有什么是你不能接受的结果？",
            why_it_matters="明确底线可以让分析更聚焦。",
            answer_type="short_text",
            unknown_assumption="按财务+生活质量综合考量估算。",
        ),
        QuestionTemplate(
            id="deadline",
            question="决策的时间压力？",
            why_it_matters="紧迫程度决定验证空间和方案深度。",
            answer_type="range_choice",
            options=(
                ("urgent", "很紧急"),
                ("weeks", "几周内"),
                ("months", "几个月内"),
                ("no_rush", "不急"),
                UNKNOWN_OPTION,
            ),
            unknown_assumption="按几周内做出决定估算。",
        ),
    ),
    fact_keys=(),
    keywords=(),
)


PLAYBOOKS: dict[str, Playbook] = {
    "real_estate": REAL_ESTATE_PLAYBOOK,
    "offer": CAREER_PLAYBOOK,
    "job_change": CAREER_PLAYBOOK,
    "investment": INVESTMENT_PLAYBOOK,
    "gold": INVESTMENT_PLAYBOOK,
    "stocks": INVESTMENT_PLAYBOOK,
    "general": GENERAL_PLAYBOOK,
    "buy_bag": GENERAL_PLAYBOOK,
    "entrepreneurship": GENERAL_PLAYBOOK,
}


def select_playbook(category: str | None) -> Playbook:
    """根据 category 返回最合适的 Playbook。

    未知或 None 一律 fallback 到 GENERAL_PLAYBOOK。
    never raise(避免阻断决策路径生成)。
    """
    if category and category in PLAYBOOKS:
        return PLAYBOOKS[category]
    return GENERAL_PLAYBOOK
