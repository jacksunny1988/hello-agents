"""笔记检索测试：分词 / 覆盖率 / 综合打分"""

from hello_agents.notes.search import (
    BODY_WEIGHT,
    TITLE_WEIGHT,
    coverage,
    score,
    tokenize,
)


def test_tokenize_英文数字小写化():
    assert tokenize("HTTPX 0.28 RAG_pipeline") == ["httpx", "0", "28", "rag_pipeline"]


def test_tokenize_中文按_bigram():
    assert tokenize("依赖冲突") == ["依赖", "赖冲", "冲突"]


def test_tokenize_单字成段取该字():
    assert tokenize("好") == ["好"]


def test_tokenize_中文标点分段不产生跨段_bigram():
    assert tokenize("依赖 冲突") == ["依赖", "冲突"]


def test_tokenize_中英混合():
    assert tokenize("用 httpx 排查依赖冲突") == [
        "httpx",
        "用",
        "排查",
        "查依",
        "依赖",
        "赖冲",
        "冲突",
    ]


def test_tokenize_纯标点与空白为空():
    assert tokenize("，。！？ ——") == []


def test_coverage_空_needles_为_0():
    assert coverage([], ["a"]) == 0.0


def test_coverage_无交集与全交集():
    assert coverage(["a", "b"], ["c"]) == 0.0
    assert coverage(["a", "b"], ["a", "b", "c"]) == 1.0
    assert coverage(["a", "b"], ["a"]) == 0.5


def test_coverage_重复_needles_去重():
    assert coverage(["a", "a"], ["a"]) == 1.0


def test_score_空查询为_0():
    assert score("标题", ["tag"], "正文", "   ") == 0.0


def test_score_纯标点查询为_0():
    assert score("标题", [], "正文", "，。！") == 0.0


def test_score_标题命中高于正文命中():
    title_hit = score("依赖冲突", [], "无关正文", "依赖冲突")
    body_hit = score("无关标题", [], "依赖冲突", "依赖冲突")
    assert title_hit == TITLE_WEIGHT
    assert body_hit == BODY_WEIGHT
    assert title_hit > body_hit


def test_score_标签计入标题侧():
    assert score("无关", ["依赖冲突"], "无关", "依赖冲突") == TITLE_WEIGHT


def test_score_全命中为_1():
    assert score("依赖冲突", [], "依赖冲突", "依赖冲突") == 1.0


def test_score_取值在_0_到_1_之间():
    value = score(
        "依赖冲突排查", ["deps"], "## 现象\n\nhttpx 版本冲突。", "依赖冲突 httpx"
    )
    assert 0.0 < value < 1.0
