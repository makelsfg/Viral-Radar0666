"""大佬分析报告模板与两个 Agent 提示词的约束单测。"""
from bkr.expert.prompts import REPORT_TEMPLATE, REVIEWER_SYSTEM, WRITER_SYSTEM


def test_template_has_five_sections_only():
    for head in ("## 1. 结论摘要", "## 2. 市场与需求信号", "## 3. 竞争格局",
                 "## 4. 产品与差异化", "## 5. 证据附录"):
        assert head in REPORT_TEMPLATE
    for bad in ("价格与利润", "行动方案", "反方观点"):
        assert bad not in REPORT_TEMPLATE


def test_writer_forbids_extra_sections_and_caps_length():
    assert "不写以下内容" in WRITER_SYSTEM
    assert "价格与利润测算" in WRITER_SYSTEM
    assert "3500" in WRITER_SYSTEM


def test_reviewer_drops_removed_checks():
    assert "利润测算是否给出公式" not in REVIEWER_SYSTEM
    assert "7 天验证与 30 天放大" not in REVIEWER_SYSTEM
    assert "模板外" in REVIEWER_SYSTEM
