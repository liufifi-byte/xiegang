"""C 端可见文本清洗层。

所有写入 transcript / members 的 LLM 生成文本，入库前必须经过本模块清洗。
解决的问题（来自真实议会三轮 review）：

1. 内部 ev_id 暴露：`[ev_17237a00aa94]` → 剥除或替换为（依据N）
2. markdown 星号泄漏：`**加粗**` → 加粗
3. 英文专家 ID 混排：Politician/Economist → 政治家/经济学家
4. 贬义误称：LLM 自发把"政治家"写成"政客" → 统一纠正
5. 英文 stance 泄漏：against/for/neutral → 反对/支持/中立
6. 系统术语泄漏："受本次调研步骤预算或时间限制…"/"Agent 总运行时间已达到上限"
   → 统一压缩为一句用户可读的降级说明
7. 连续标点错误：`？。` `；。` `。。` `：。` → 规整
8. R0/R1/R2 裸编号：`Politician R1` → `政治家（第1轮）`（中文语境下更自然）

证据渲染（evidence formatting）：
9. 原文倾倒 → 每条 ≤80 字摘要 + 来源域名 + 可信度标签
10. 信源分级：官方/判决/主流财经媒体 vs 自媒体，渲染时分级标注

标签规则化：
11. "判断基础"标签按证据条数 + degraded 状态规则计算，不让 LLM 自评
"""
from __future__ import annotations

import re
from urllib.parse import urlparse


# ============================================================
# 1. 基础文本清洗
# ============================================================

# 英文 expert_id → 中文名（与 profiles.py display_name 保持一致）
_EXPERT_NAME_MAP = {
    "politician": "政治家",
    "economist": "经济学家",
    "mathematician": "数学家",
    "psychologist": "心理学家",
    "artist": "艺术家",
    "historian": "历史学家",
    # 大写开头形式（LLM 偶尔会在句首大写）
    "Politician": "政治家",
    "Economist": "经济学家",
    "Mathematician": "数学家",
    "Psychologist": "心理学家",
    "Artist": "艺术家",
    "Historian": "历史学家",
}

# 贬义/不当称呼 → 正确称呼
_MISNAME_MAP = {
    "政客": "政治家",
}

# 英文 stance → 中文（V3 新枚举 + 旧枚举兼容）
_STANCE_MAP = {
    "support": "建议推进",
    "oppose": "不建议",
    "cautious": "谨慎推进",
    "against": "反对",
    "for": "支持",
    "neutral": "中立",
    # 常见句式形态
    " to against": " 为反对",
    " to for": " 为支持",
}

# 系统术语泄漏 → 用户可读降级说明
_SYSTEM_PHRASE_PATTERNS = [
    # R0 fallback 旧文案
    (re.compile(r"Agent\s*总运行时间已达到上限[。；]?", re.IGNORECASE), ""),
    # R0 fallback 新文案（runner.py + research_graph.py 两个来源拼接后的长串）
    (
        re.compile(
            r"受本次调研(?:步骤预算或时间|时间上限)限制[，,]?"
            r"(?:未能完成全面取证[；;]?建议读者把以下判断视为初步结论而非完整推理[。；]?)?"
            r"(?:未能完成完整推理[。；]?)?"
        ),
        "",
    ),
    (re.compile(r"受本次调研时间上限限制[，,]?未能完成完整推理[。；]?"), ""),
    (re.compile(r"受本次调研步骤预算限制[，,]?未能完成完整推理[。；]?"), ""),
    # 兜底：孤立出现的预算字眼
    (re.compile(r"步骤预算或时间限制[。；]?"), ""),
]

# 连续标点规整（顺序敏感：先长后短）
_PUNCT_CLEANUPS = [
    (re.compile(r"？[。；]\s*"), "？"),
    (re.compile(r"；[。]\s*"), "。"),
    (re.compile(r"：[。]\s*"), "："),
    (re.compile(r"[。]{2,}\s*"), "。"),
    (re.compile(r"[；]{2,}\s*"), "；"),
    (re.compile(r"，\s*。"), "。"),
]


def strip_ev_ids(text: str) -> str:
    """剥除正文中的 [ev_xxx] 内部 evidence ID。"""
    return re.sub(r"\s*\[ev_[0-9a-fA-F]+\]", "", text or "")


def strip_markdown_emphasis(text: str) -> str:
    """把 **加粗** / *斜体* 的星号去掉（C 端不渲染 markdown）。"""
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text or "")
    text = re.sub(r"(?<!\*)\*([^*\n]+)\*(?!\*)", r"\1", text)
    return text


def unify_expert_names(text: str) -> str:
    """英文 expert_id 与贬义称呼统一为中文名。"""
    text = text or ""
    for wrong, right in _EXPERT_NAME_MAP.items():
        text = re.sub(rf"\b{wrong}\b", right, text)
    for wrong, right in _MISNAME_MAP.items():
        text = text.replace(wrong, right)
    # "政治家 R1" / "政治家（第1轮）"——把裸轮次编号转成中文
    text = re.sub(r"(政治家|经济学家|数学家|心理学家|艺术家|历史学家)\s*R([0-3])", r"\1（第\2轮）", text)
    return text


def unify_stance_words(text: str) -> str:
    """英文 stance 词在中文句子里替换为中文。"""
    text = text or ""
    # 只替换前后是中文/标点的场景，避免误伤 URL 等英文串
    for en, zh in _STANCE_MAP.items():
        text = re.sub(
            rf"(?<=[\u4e00-\u9fff，。；：、\s])(?:立场至\s*)?{re.escape(en)}(?=[\u4e00-\u9fff，。；：、\s。])",
            lambda m: (m.group(0).replace(en, zh)),
            text,
        )
    return text


def remove_system_phrases(text: str) -> str:
    """清掉所有内部系统术语泄漏。"""
    text = text or ""
    for pattern, repl in _SYSTEM_PHRASE_PATTERNS:
        text = pattern.sub(repl, text)
    return text


def clean_punctuation(text: str) -> str:
    """连续标点规整。"""
    text = text or ""
    for pattern, repl in _PUNCT_CLEANUPS:
        text = pattern.sub(repl, text)
    # 清洗后可能留下 "；。" 残渣二次处理
    text = re.sub(r"；\s*。", "。", text)
    # 行尾/段首多余空白
    text = re.sub(r"[ \t]{2,}", " ", text)
    return text


# 系统短语被剥除后可能留下的孤立引导语（后面没内容了）
_ORPHAN_LEADS = [
    re.compile(r"仍需确认[：:]\s*[；。]?\s*$"),
    re.compile(r"^[，。；]\s*"),  # 段首孤立标点
]


def _strip_orphan_leads(text: str) -> str:
    """删除系统术语剥除后残留的空引导结构。"""
    for pattern in _ORPHAN_LEADS:
        text = pattern.sub("", text)
    return text


def clean_visible_text(text: str) -> str:
    """组合清洗入口——所有写入 transcript 的 LLM 文本必经此函数。"""
    if not text:
        return text
    text = strip_ev_ids(text)
    text = strip_markdown_emphasis(text)
    text = remove_system_phrases(text)
    text = _strip_orphan_leads(text)
    text = unify_expert_names(text)
    text = unify_stance_words(text)
    text = clean_punctuation(text)
    text = _strip_orphan_leads(text)  # 标点规整后可能再产生孤立引导
    return text.strip()


# ============================================================
# 2. 证据展示（摘要化 + 信源分级）
# ============================================================

# 官方 / 判决 / 主流财经媒体域名（trusted）
_TRUSTED_DOMAINS = {
    "gov.cn", "court.gov.cn", "wenshu.court.gov.cn", "npc.gov.cn",
    "stats.gov.cn", "pbc.gov.cn", "safe.gov.cn", "mof.gov.cn",
    "xinhuanet.com", "news.cn", "people.com.cn", "cctv.com", "cnr.cn",
    "caixin.com", "yicai.com", "21jingji.com", "thepaper.cn",
    "jiemian.com", "caixinmedia.com", "stcn.com", "cs.com.cn",
    "ce.cn", "economicdaily.com.cn", "cnstock.com", "nbd.com.cn",
}

# 自媒体 / UGC 域名（blog 级）
_BLOG_DOMAINS = {
    "csdn.net", "blog.csdn.net", "zhihu.com", "zhuanlan.zhihu.com",
    "mp.weixin.qq.com", "weixin.qq.com", "bilibili.com", "reddit.com",
    "jianshu.com", "cnblogs.com", "163.com", "sohu.com", "sina.com.cn",
    "toutiao.com", "douyin.com", "xiaohongshu.com",
}

# 招聘导流软文特征词——命中即过滤
_SPAM_MARKERS = (
    "设为星标", "扫码关注", "点击关注", "网申时间", "简历投递",
    "关注公众号", "回复关键词", "添加微信", "领取资料", "限时领取",
    "秋招网申", "春招网申",
)

# 招聘站域名黑名单
_RECRUIT_DOMAINS = {
    "zhaopin.com", "51job.com", "liepin.com", "lagou.com",
    "zhipin.com", "bosszhipin.com", "goufang.com", "fang.com",
    "anjuke.com", "lianjia.com", "beike.com",
}


def classify_source(url: str) -> str:
    """按域名给来源分级：trusted / media / blog。"""
    try:
        host = (urlparse(url or "").netloc or "").lower()
    except Exception:
        return "media"
    host = host.removeprefix("www.")
    for d in _TRUSTED_DOMAINS:
        if host == d or host.endswith("." + d):
            return "trusted"
    for d in _BLOG_DOMAINS:
        if host == d or host.endswith("." + d):
            return "blog"
    return "media"


_SOURCE_LABEL = {
    "trusted": "权威来源",
    "media": "公开媒体",
    "blog": "自媒体",
}


def is_recruit_spam(item: dict) -> bool:
    """识别招聘软文 / 导流文，命中则不得进入证据。"""
    content = str(item.get("content") or "")
    title = str(item.get("title") or "")
    blob = title + content
    if any(marker in blob for marker in _SPAM_MARKERS):
        return True
    url = str(item.get("url") or "")
    try:
        host = (urlparse(url).netloc or "").lower().removeprefix("www.")
    except Exception:
        return False
    for d in _RECRUIT_DOMAINS:
        if host == d or host.endswith("." + d):
            return True
    return False


def _smart_truncate(text: str, limit: int = 80) -> str:
    """优先在句号/分号处截断的智能摘要（避免"依据，"这类词中残句）。"""
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    head = text[:limit]
    # 依次找最近的完整句读边界（句号 > 分号 > 逗号 > 顿号）
    for sep in ("。", "；", "，", "、"):
        idx = head.rfind(sep)
        if idx > limit // 2:
            return head[: idx + 1]
    # 没有任何边界（口语化长句）：切到词边界不现实，直接截 + 省略号
    return head.rstrip("，。；、依据 ") + "…"


def domain_of(url: str) -> str:
    try:
        return (urlparse(url or "").netloc or "").removeprefix("www.")
    except Exception:
        return ""


def format_evidence_list(records: list[dict]) -> list[str]:
    """把证据记录渲染成编号 + 摘要 + 域名 + 分级标签的行。

    输入 records 为 evidence ledger dump（含 claim_summary/source_url/source_title/kind）。
    """
    lines: list[str] = []
    for idx, record in enumerate(records, start=1):
        kind = {
            "user_fact": "用户信息",
            "web_source": "网页来源",
            "calculation": "计算结果",
            "inference": "推断假设",
        }.get(record.get("kind"), "分析依据")
        claim = str(
            record.get("supports_claim")
            or record.get("claim_summary")
            or ""
        ).strip()
        summary = _smart_truncate(claim)
        url = str(record.get("source_url") or "")
        dom = domain_of(url)
        grade = classify_source(url)
        grade_label = _SOURCE_LABEL.get(grade, "公开媒体")
        if dom:
            tail = f"（{dom} · {grade_label}）"
        else:
            tail = ""
        lines.append(f"{idx}. [{kind}]{tail} {summary}")
    return lines


# ============================================================
# 3. 判断基础标签规则化
# ============================================================

_BASIS_LABEL_ZH = {
    "strong": "判断基础较强",
    "moderate": "判断基础一般",
    "limited": "判断基础有限",
}


def compute_basis_level(evidence_count: int, degraded: bool = False) -> str:
    """标签不再让 LLM 自评——按证据条数 + degraded 状态规则计算。

    规则（对齐产品语义）：
      - degraded（含 fallback / 超时 / 审核受限）→ 一律 limited
      - 证据 >= 4 条 → strong
      - 证据 2-3 条 → moderate
      - 证据 0-1 条 → limited
    """
    if degraded:
        return "limited"
    if evidence_count >= 4:
        return "strong"
    if evidence_count >= 2:
        return "moderate"
    return "limited"


def basis_label_zh(level: str) -> str:
    return _BASIS_LABEL_ZH.get(level, "判断基础有限")


# ============================================================
# 4. 确定性质量检查（spec §12.1 规则版——不增加模型调用）
# ============================================================

# 系统状态词（正文出现即违规）
_SYSTEM_LEAK_MARKERS = (
    "预算", "超时", "工具错误", "降级", "受本次调研", "步骤预算",
    "时间上限", "请与其他专家交叉比对", "可核验来源",
    "独立判断",  # spec §12.1: 不得出现两个"独立判断"
)


def quality_gate_issues(
    judgment: str = "",
    analysis_points: list[str] | None = None,
    action: str = "",
    key_question: str = "",
    evidence_text_sample: str = "",
    lens_title: str = "",
    expected_lens: str = "",
) -> list[str]:
    """spec §12.1 确定性规则检查。返回问题列表（空 = 通过）。

    检查项：
    1. 正文出现系统状态词（预算/超时/降级/独立判断等）
    2. 字数与条数越限（judgment ≤200，analysis ≤3 条，question 只一个问号）
    3. lens_title 与角色配置不一致
    4. 正文包含证据摘要长串（≥30 字连续相同视为泄漏）
    5. key_question 出现多个问号
    """
    issues: list[str] = []
    full_text = " ".join(
        filter(None, [judgment, *(analysis_points or []), action, key_question])
    )

    # 1. 系统词
    for marker in _SYSTEM_LEAK_MARKERS:
        if marker in full_text:
            issues.append(f"正文含系统词「{marker}」")

    # 2. 条数
    if analysis_points and len(analysis_points) > 3:
        issues.append(f"analysis_points {len(analysis_points)} 条 > 3")

    # 3. lens 一致性
    if expected_lens and lens_title and lens_title != expected_lens:
        issues.append(f"lens_title「{lens_title}」≠ 角色「{expected_lens}」")

    # 4. 证据原文泄漏（≥30 字连续子串出现在正文）
    if evidence_text_sample and len(evidence_text_sample) >= 30:
        probe = evidence_text_sample[:30]
        if probe in full_text:
            issues.append("正文包含证据原文长串（≥30 字）")

    # 5. key_question 多问号
    if key_question.count("？") + key_question.count("?") > 1:
        issues.append("key_question 包含多个问号")

    return issues
