"""测试公共夹具：零依赖（TF-IDF + 纯内存向量 + SQLite 临时库）"""

import pytest

from hello_agents.memory import (
    MemoryConfig,
    MemoryManager,
    TFIDFEmbedding,
)


@pytest.fixture
def embedding() -> TFIDFEmbedding:
    return TFIDFEmbedding(dim=64)


@pytest.fixture
def config(tmp_path) -> MemoryConfig:
    return MemoryConfig(
        sqlite_path=str(tmp_path / "memory.db"),
        embedding_backend="tfidf",
        embedding_dim=64,
    )


@pytest.fixture
def manager(config, embedding) -> MemoryManager:
    mgr = MemoryManager(config=config, embedding=embedding)
    yield mgr
    mgr.close()
