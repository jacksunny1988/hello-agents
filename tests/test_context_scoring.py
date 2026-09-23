"""打分器测试：关键词重叠 / 向量相似度 / 缓存"""

from hello_agents.memory import cosine_similarity


def test_cosine_similarity_is_exported_from_memory_package():
    assert cosine_similarity([1.0, 0.0], [1.0, 0.0]) == 1.0
