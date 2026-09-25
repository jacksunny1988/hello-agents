"""笔记关键词打分

零依赖的中英混合分词与覆盖率打分，供 NoteStore.search 使用。
不做 TF-IDF 加权：两个权重常量放在模块级，便于单测与后续调参。
"""

import re

_TOKEN_RE = re.compile(r"[0-9a-z_]+")
_CJK_RUN_RE = re.compile(r"[一-鿿]+")

TITLE_WEIGHT = 0.6
BODY_WEIGHT = 0.4


def tokenize(text: str) -> list[str]:
    """分词：英文/数字按 ``[0-9a-z_]+`` 小写，CJK 连续段按 bigram

    中文没有词边界：单字太散、整句太粗，bigram 是零依赖下的折中。
    单字成段时取该字本身。
    """
    lowered = text.lower()
    tokens = _TOKEN_RE.findall(lowered)
    for run in _CJK_RUN_RE.findall(lowered):
        if len(run) == 1:
            tokens.append(run)
        else:
            tokens.extend(run[i : i + 2] for i in range(len(run) - 1))
    return tokens


def coverage(needles: list[str], haystack: list[str]) -> float:
    """needles 被 haystack 覆盖的比例（去重后），空 needles 为 0.0"""
    unique = set(needles)
    if not unique:
        return 0.0
    return len(unique & set(haystack)) / len(unique)


def score(title: str, tags: list[str], body: str, query: str) -> float:
    """综合得分 = 0.6 × 标题/标签覆盖率 + 0.4 × 正文覆盖率，取值 ``[0, 1]``

    查询分词为空（空白、纯标点）时返回 0.0。
    """
    needles = tokenize(query)
    if not needles:
        return 0.0
    head = tokenize(f"{title} {' '.join(tags)}")
    return TITLE_WEIGHT * coverage(needles, head) + BODY_WEIGHT * coverage(
        needles, tokenize(body)
    )
