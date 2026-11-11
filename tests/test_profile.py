"""卖点词表：关键词归一化、YAML 命中、LLM 输出校验、参数值并入。"""
from bkr.analysis_profile import profile_for
from bkr.profile_llm import (
    clean_title,
    normalize_keyword,
    param_values,
    sample_titles,
    validate_profile,
)


def test_normalize_keyword_strips_platform_suffix():
    assert normalize_keyword("羽绒服 · 天猫") == "羽绒服"
    assert normalize_keyword("羽绒服·1688") == "羽绒服"
    assert normalize_keyword("羽绒服 | 拼多多") == "羽绒服"
    assert normalize_keyword(" 露营灯 ") == "露营灯"


def test_clean_title_removes_promo_words():
    text = clean_title("【限时秒杀】90白鸭绒羽绒服 包邮 正品")
    assert "秒杀" not in text and "包邮" not in text
    assert "白鸭绒羽绒服" in text


def test_yaml_profile_hit():
    p = profile_for("羽绒服 · 天猫", use_db=False)
    assert p["source"] == "manual"
    assert p["name"] == "服饰户外"
    assert "羽绒" in p["features"]


def test_unknown_category_has_no_generic_words():
    """未命中的品类不再退回通用词表，否则会产出「看起来正常」的垃圾统计。"""
    p = profile_for("露营灯", use_db=False)
    assert p["source"] == "none"
    assert p["features"] == []


def test_validate_drops_words_without_evidence():
    titles = ["90白鸭绒羽绒服 中长款", "轻薄羽绒服 白鸭绒填充"]
    raw = {
        "features": ["白鸭绒", "羽绒服", "不存在的词", "包邮", "123", "中长款"],
        "materials": ["白鸭绒", "鹅绒"],
    }
    profile, hit_rate = validate_profile(
        raw, keyword="羽绒服", titles=titles,
        keys=["充绒量"], values=["白鸭绒"],
    )
    words = profile["features"]
    assert "白鸭绒" in words           # 标题与参数都有依据
    assert "中长款" in words
    assert "羽绒服" not in words       # 品类词本身
    assert "包邮" not in words         # 促销词
    assert "不存在的词" not in words
    assert "123" not in words
    assert 0 < hit_rate <= 1


def test_param_values_split_and_filter_specs():
    rows = [
        {"params": {"材质": "316不锈钢", "功能": "保温,吸管", "容量": "500ML"}},
        {"params": {"材质": "316不锈钢", "功能": "保温"}},
    ]
    words = param_values(rows)
    assert "316不锈钢" in words
    assert "保温" in words and "吸管" not in words   # 吸管只出现 1 次
    assert "500ML" not in words                      # 纯规格不算卖点词


def test_sample_titles_dedup_and_cap():
    rows = [{"title": f"羽绒服 第{i}款 中长款", "sold_lb": i} for i in range(120)]
    rows.append({"title": "羽绒服 第1款 中长款", "sold_lb": 999})   # 重复
    titles = sample_titles(rows, cap=60)
    assert len(titles) == 60
    assert len(set(titles)) == len(titles)
