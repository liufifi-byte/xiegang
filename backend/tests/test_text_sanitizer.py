"""测试 text_sanitizer 的 C 端文本清洗规则。

每条用例都来自真实议会 R0/R1/R2 的脏文本（见 review 记录）。
"""
import pytest

from app.graph.nodes.text_sanitizer import (
    basis_label_zh,
    classify_source,
    clean_visible_text,
    compute_basis_level,
    format_evidence_list,
    is_recruit_spam,
)


# ============================================================
# 清洗规则（真实脏文本用例）
# ============================================================

def test_ev_id_stripped():
    """R1 数学家的真实案例：网页来源[ev_17237a00aa94]显示违约金。"""
    out = clean_visible_text("网页来源[ev_17237a00aa94]显示违约金 111 万元")
    assert "[ev_" not in out
    assert "显示违约金 111 万元" in out


def test_markdown_bold_stripped():
    """R2 艺术家：第三个选项：**内部跳槽**"""
    out = clean_visible_text("第三个选项：**内部跳槽**是可行的")
    assert "**" not in out
    assert "内部跳槽" in out


def test_english_expert_ids_replaced():
    """R2 数学家开头'Economist，你补上了…' + 'Politician R1'"""
    out = clean_visible_text("Economist，你补上了我漏算的变量。Politician R1 声称你手里没牌")
    assert "Economist" not in out
    assert "Politician" not in out
    assert "经济学家" in out
    assert "政治家" in out


def test_derogatory_misname_fixed():
    """LLM 自发把'政治家'写成'政客'（贬义）——必须纠正。"""
    out = clean_visible_text("政客提到的竞业协议赔偿")
    assert "政客" not in out
    assert "政治家" in out


def test_english_stance_word_replaced():
    """R2 心理学家结尾'调整立场至 against'"""
    out = clean_visible_text("推演让我调整立场至 against。")
    assert "against" not in out
    assert "反对" in out


def test_system_budget_phrase_removed():
    """R0 fallback 系统话术（新+旧两版）必须全清。"""
    cases = [
        "仍需确认：受本次调研步骤预算或时间限制，未能完成全面取证；建议读者把以下判断视为初步结论而非完整推理。",
        "受本次调研时间上限限制，未能完成完整推理。",
        "Agent 总运行时间已达到上限",
    ]
    for c in cases:
        out = clean_visible_text(c)
        assert "预算" not in out, f"'预算'残留于: {out!r}"
        assert "上限" not in out or "Agent" not in c, f"上限残留于: {out!r}"
        assert "推理" not in out or "完整推理" not in out, f"系统话术残留: {out!r}"


def test_punctuation_pairs_normalized():
    """R0 真实标点错误：？。 ；。 。。"""
    out = clean_visible_text("你确定吗？。这个方案。。可行；。")
    assert "？。" not in out
    assert "。。" not in out
    assert "；。" not in out


# ============================================================
# 证据展示
# ============================================================

def test_evidence_list_numbered_with_domain_and_grade():
    """证据渲染：编号 + 摘要 + 域名 + 分级标签。"""
    records = [
        {
            "kind": "web_source",
            "claim_summary": "法院判决该员工向腾讯返还、支付违约金共约111万元，竞业限制期限为一年。" * 3,
            "source_url": "https://wenshu.court.gov.cn/detail/123",
        },
        {
            "kind": "web_source",
            "claim_summary": "字节薪酬结构调整月薪上涨20%",
            "source_url": "https://blog.csdn.net/xiegang/a",
        },
    ]
    lines = format_evidence_list(records)
    assert len(lines) == 2
    assert lines[0].startswith("1. ")
    assert "wenshu.court.gov.cn" in lines[0]
    assert "权威来源" in lines[0]
    # 长证据被截断（≤80 字摘要 + … 或句截）
    assert len(lines[0]) < 200
    assert lines[1].startswith("2. ")
    assert "自媒体" in lines[1]  # CSDN 分为 blog


def test_evidence_summary_truncates_to_80_chars():
    """原文倾倒 → 摘要部分 ≤80 字（域名/标签尾巴不计入）。"""
    long_claim = "判决书显示该员工2016年9月5日入职腾讯担任内容平台部高级编辑经理职务主要负责内容编辑监控运营数据分析等工作" * 3
    lines = format_evidence_list([{"kind": "web_source", "claim_summary": long_claim, "source_url": "https://x.com/a"}])
    line = lines[0]
    # 摘要 = 去掉编号前缀和域名标签后的正文部分
    # 格式: "1. [网页来源]（x.com · 公开媒体） 摘要…"
    body = line.split("） ", 1)[-1] if "） " in line else line
    assert len(body) <= 90, f"摘要 {len(body)} 字未截断: {body[:50]}..."


# ============================================================
# 软文过滤
# ============================================================

def test_recruit_spam_detected():
    """R0 数学家的真实脏数据：公众号导流软文。"""
    spam = {
        "title": "字节待遇",
        "content": "网申时间为8月10日至10月31日 建议将公众号设为星标",
        "url": "https://mp.weixin.qq.com/s/abc",
    }
    assert is_recruit_spam(spam) is True
    legit = {
        "title": "竞业协议判例",
        "content": "法院判决返还竞业限制补偿金并支付违约金",
        "url": "https://wenshu.court.gov.cn/x",
    }
    assert is_recruit_spam(legit) is False
    recruit_site = {"title": "x", "content": "高薪职位", "url": "https://www.zhaopin.com/jobs"}
    assert is_recruit_spam(recruit_site) is True


def test_classify_source_levels():
    assert classify_source("https://wenshu.court.gov.cn/a") == "trusted"
    assert classify_source("https://www.thepaper.cn/b") == "trusted"
    assert classify_source("https://blog.csdn.net/a") == "blog"
    assert classify_source("https://www.zhihu.com/q") == "blog"
    assert classify_source("https://some-random.example.com/x") == "media"


# ============================================================
# 判断基础标签规则化
# ============================================================

def test_basis_level_rules():
    """标签按证据数+degraded 计算，与 LLM 自评无关。"""
    assert compute_basis_level(0) == "limited"
    assert compute_basis_level(1) == "limited"
    assert compute_basis_level(2) == "moderate"
    assert compute_basis_level(3) == "moderate"
    assert compute_basis_level(4) == "strong"
    assert compute_basis_level(5) == "strong"
    # degraded 一律 limited——修复'fallback 标一般'的倒挂
    assert compute_basis_level(5, degraded=True) == "limited"


def test_basis_label_zh_mapping():
    assert basis_label_zh("strong") == "判断基础较强"
    assert basis_label_zh("moderate") == "判断基础一般"
    assert basis_label_zh("limited") == "判断基础有限"
