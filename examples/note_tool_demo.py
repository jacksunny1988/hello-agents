"""NoteTool 离线示例

演示结构化笔记的创建、更新、过滤、检索、摘要与索引自愈，
全程不触网、不需要任何 API key。产物落在 ./notes/（已在 .gitignore 中忽略；
要版本化自己的笔记时，删掉 .gitignore 里的 `notes/` 那一行即可）。
"""

from pathlib import Path

from hello_agents.notes import NoteConfig, NoteStore, NoteType
from hello_agents.tools.builtin import NoteTool

NOTES = [
    (
        "项目进展 - 第一阶段",
        (
            "# 项目进展 - 第一阶段\n\n## 完成情况\n\n已完成数据模型层的重构。\n\n"
            "## 下一步计划\n\n重构业务逻辑层。"
        ),
        NoteType.TASK_STATE,
        ["refactoring", "phase1"],
    ),
    (
        "依赖冲突排查",
        (
            "# 依赖冲突排查\n\n## 现象\n\nhttpx 版本冲突导致启动失败。\n\n"
            "## 阻塞\n\n等待上游修复。"
        ),
        NoteType.BLOCKER,
        ["deps", "phase1"],
    ),
    (
        "向量检索选型",
        "# 向量检索选型\n\n## 结论\n\n本地 TF-IDF 兜底，配置后升级 Qdrant。",
        NoteType.DECISION,
        ["rag"],
    ),
]


def main() -> None:
    config = NoteConfig(notes_dir=Path("./notes"))
    store = NoteStore(config)
    tool = NoteTool(store)

    print("== 创建 ==")
    for title, body, note_type, tags in NOTES:
        response = tool.run(
            {
                "action": "create",
                "title": title,
                "body": body,
                "type": note_type,
                "tags": tags,
            }
        )
        print(f"  {response.text}")

    print("\n== 更新 ==")
    blocker = store.read(store.list(type=NoteType.BLOCKER)[0].id)
    response = tool.run(
        {
            "action": "update",
            "id": blocker.id,
            "body": blocker.body + "\n\n## 结论\n\n锁定 httpx<0.28。",
        }
    )
    print(f"  {response.text}")

    print("\n== 列出（type=task_state）==")
    print(tool.run({"action": "list", "type": NoteType.TASK_STATE}).text)

    print("\n== 检索「依赖冲突」==")
    print(tool.run({"action": "search", "query": "依赖冲突"}).text)

    print("\n== 全库摘要 ==")
    print(tool.run({"action": "summary"}).text)

    print("\n== 索引自愈 ==")
    scratch = store.create("临时草稿", "## 草稿\n\n用完即弃。")
    (config.notes_dir / scratch.file_path).unlink()
    print(f"  删除文件: {scratch.file_path}")
    print(f"  verify: {store.verify().summary()}")
    print(f"  rebuild 条目数: {store.rebuild_index()}")
    print(f"  verify: {store.verify().summary()}")

    print(f"\n产物目录: {config.notes_dir.resolve()}")


if __name__ == "__main__":
    main()
