"""Neo4j 图存储

语义记忆的知识图谱写入与邻居扩展。
neo4j 包缺失或未配置服务时，create_neo4j_store() 返回 None，
调用方据此跳过图谱逻辑（不模拟图谱）。
"""

from typing import Any


class Neo4jStore:
    """Neo4j 图存储封装（同步 driver）"""

    def __init__(self, uri: str, user: str, password: str):
        from neo4j import GraphDatabase  # 惰性导入

        self._driver = GraphDatabase.driver(uri, auth=(user, password))
        self._driver.verify_connectivity()

    def upsert_entity(
        self, name: str, properties: dict[str, Any] | None = None
    ) -> None:
        """创建或更新实体节点（按 name 去重）"""
        self._driver.execute_query(
            "MERGE (e:Entity {name: $name}) SET e += $properties",
            name=name,
            properties=properties or {},
        )

    def upsert_relation(
        self,
        source: str,
        target: str,
        relation: str,
        properties: dict[str, Any] | None = None,
    ) -> None:
        """创建或更新一条三元组关系（关系类型需为合法标识符）"""
        rel = "".join(ch if ch.isalnum() or ch == "_" else "_" for ch in relation)
        self._driver.execute_query(
            "MERGE (a:Entity {name: $source}) "
            "MERGE (b:Entity {name: $target}) "
            f"MERGE (a)-[r:{rel}]->(b) SET r += $properties",
            source=source,
            target=target,
            properties=properties or {},
        )

    def neighbors(self, name: str, limit: int = 5) -> list[dict[str, Any]]:
        """查询实体的一跳邻居（含关系类型）"""
        records, _, _ = self._driver.execute_query(
            "MATCH (a:Entity {name: $name})-[r]-(b:Entity) "
            "RETURN type(r) AS relation, b.name AS neighbor LIMIT $limit",
            name=name,
            limit=max(limit, 0),
        )
        return [
            {"relation": record["relation"], "neighbor": record["neighbor"]}
            for record in records
        ]

    def delete_entity(self, name: str) -> bool:
        """删除实体及其全部关系"""
        records, _, _ = self._driver.execute_query(
            "MATCH (e:Entity {name: $name}) DETACH DELETE e RETURN count(e) AS n",
            name=name,
        )
        return bool(records and records[0]["n"])

    def clear(self) -> None:
        """清空全部实体与关系"""
        self._driver.execute_query("MATCH (n:Entity) DETACH DELETE n")

    def close(self) -> None:
        """关闭连接"""
        self._driver.close()


def create_neo4j_store(config) -> Neo4jStore | None:
    """创建图存储；未配置或 neo4j 不可用时返回 None"""
    if not config.neo4j_uri or not config.neo4j_password:
        return None
    try:
        return Neo4jStore(
            uri=config.neo4j_uri,
            user=config.neo4j_user,
            password=config.neo4j_password,
        )
    except Exception:
        # 服务不可达 / 版本不兼容 / 依赖异常：降级为无图模式
        return None
