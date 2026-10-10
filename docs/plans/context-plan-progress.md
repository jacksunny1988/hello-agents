# 执行进度：context-management plan

> **🔴 用户指令（2026-09-23，最新）**：「本次task执行结束后暂停」
> **含义**：Task 8 走完（实施者修复 → 修复面复审 → 收口）后**立即暂停**，
> **不派 Task 9**，停下来向用户汇报并等待指示。
> 之前的「继续推进task」授权**到此为止**。
> 暂停时需在汇报里带上：并发会话碰撞、Task 16 Step 0 计划外扩权、全仓 lint 基线（D8）。

分支 `feat/context-management`（从 main 切出）
基线提交 `a0f78f6` — docs/specs + docs/plans + context/__init__.py + ruff format

计划文件：`D:\projects\hello-agents\docs\plans\2026-09-23-context-management-plan.md`
Spec：`D:\projects\hello-agents\docs\specs\2026-09-23-context-management-design.md`

任务行号（计划文件内）：
1:42  2:240  3:448  4:520  5:924  6:1222  7:1490  8:1779  9:2104
10:2370  11:2694  12:2964  13:3023  14:3147  15:3300  16:3480
附录A:3525  附录B:3542

## 进度

- [x] Task 1 TTLCache — ✅ 完成。提交 `268098c`（初版）+ `3ae007c`（评审修复）。
  spec review ✅ / quality review ✅（修复后复审通过）。7 tests pass，lint 零告警。
- [x] Task 2 动态 token 预算 — ✅ 完成。提交 `76827f8`（初版）+ `dcc5e4e`（评审修复）。
  spec review ✅ / quality review ✅（"Ready to merge: Yes"，修复后复审进行中）。
  10 tests pass，lint 零告警。
  评审提出的 `BudgetPolicy` 契约缺口（缺 `name`、`system_instructions` 语义未写明）
  已修，并同步到计划与 Spec §4.2。
  刻意不做：裸「何」入疑问词表（会误伤「任何/几何」）、`200`/`10` 提为常量、
  引入类型检查器（超范围，记为后续建议）。
- [x] Task 3 补齐前置导出 — ✅ 完成。提交 `7d2ca1f`。spec review ✅ / quality review ✅
  （"Ready to merge: Yes"，无 Critical/Important）。87 passed。
  偏差：`memory/__init__.py` 的 import 因超 88 列改为括号换行（ruff format 自身输出）。
- [x] Task 4 核心数据类型 — ✅ 完成。`c30d2b9`（初版）+ `0cc2d5e`（评审修复）+ `3771327`（plan 同步）。
  spec review ✅ / quality review ✅（"Ready to merge: Yes"）/ **修复复审 ✅**（"Ready to merge: Yes"）。
  复审独立实测 8 项变异全 KILLED（含「两条范围检查全删」「顺序调换」两项自选），
  每次变异后 sha256 与 HEAD 一致、`git status --porcelain` 为空。
  实施者的 `match=` 偏离被判定**偏离合理**，复审给了 `re.search` 匹配矩阵证明
  `必须在` 后缀不会被和校验消息（`…必须等于 1.0`）骗过，而裸字段名会。
  21 个用例，套件 `108 passed, 1 failed, 1 skipped`。
  复审「检查过但故意不报」6 项，其中值得记的：
  - `context/__init__.py` 只导出 `ContextConfig, ContextPacket`，与 `base.py.__all__` 的 5 项
    不一致 —— 是 Task 4 的**临时 stopgap**，Task 13 重写该文件时会补齐，不是遗漏。
  - `to_dict()` 漂移检测只核顶层键，不核 `budget` 子 dict 的键。超出本轮要求原文，
    记为可选后续，不动。
- [x] Task 4 修复轮实施者偏离（D7）已关闭：复审判定合理。
- [x] Task 5 相关性打分器 — ✅ 关闭。`8f93d8d`（初版）+ `32227c5`（修复）。24 tests。
- [x] Task 6 A/B 实验 — ✅ 关闭。`d8e6597`（初版）+ `399c998`（修复）。48 tests。
- [x] Task 7 ContextBuilder 骨架 — ✅ 关闭。`b548ac7`（初版）+ `6feeef6`（修复）。42 tests。
  spec review ✅ / quality review（No → 修复 → 复审 **Yes**）。5/5 目标变异独立复跑 KILLED。
- [ ] Task 8 Gather — **派发中**。带 G-a…G-f 六条必修 + 变异对照表。
  ⚠️ G-c / G-d 是 **RAG 当前在线路径**（B15 要到 Task 12 才修），不是边角。
  实施者发现 2 个计划缺陷：
  (1) Task 4 删掉 ContextBuilder 后 `context/__init__.py` 导入失败 → 整个套件
      collection 报错，Task 5-12 全都会撞上。已做 3 行最小替换（Task 13 会重写该文件）。
      计划已同步修订。
  (2) ruff 0.16.8 **默认**规则集含 DTZ005/UP017（已用 `--isolated` 复现）。
      计划原文通篇 naive `datetime.now()`。已在计划层面统一改为 tz-aware UTC
      （30 处 now() + 3 处 import + `_calculate_recency` 重写）。
  12 个用例全部做了变异验证，12/12 杀死目标用例（含重放 B8 的 `== 0.5` 哨兵）。
  质量评审「可以合入，无 Critical」，但留 2 Important + 3 Minor 要修 → 修复版 `0cc2d5e`
  （见下表）。修复后 21 个用例，变异 11/11 全 KILLED。套件 `108 passed, 1 failed, 1 skipped`。
- [ ] Task 5 相关性打分器 — 待开始

## Task 4 修复轮（提交 `0cc2d5e`）的实施者偏离 D7

实施者报告一处**必要的**偏离：我指定的变异 #5（删 `relevance_weight` 范围检查）
**按我给的用例原文杀不掉** —— 结构性问题，非偶然：

> 两个权重和为 1.0 时必然同时越界。删掉 `relevance_weight` 检查后，紧邻的
> `recency_weight` 检查照样抛 `ConfigError`，裸 `pytest.raises(ConfigError)` 不看消息
> → SURVIVED。纯 `raises(ConfigError)` 下无法单独钉住任一侧。

处置：**保留我给的两行逐字不动，追加两行 `match=` 断言**（+1 行注释）钉住各自分支：

```python
with pytest.raises(ConfigError, match="relevance_weight 必须在"):
    ContextConfig(relevance_weight=1.5, recency_weight=-0.5)
with pytest.raises(ConfigError, match="recency_weight 必须在"):
    ContextConfig(relevance_weight=0.5, recency_weight=1.2)
```

`match` 用 `必须在` 后缀而非裸字段名 —— 和校验消息是
`recency_weight + relevance_weight 必须等于 1.0`，裸字段名会被它骗过。
加固后 S5 由 SURVIVED → KILLED。

**我的判断：偏离合理**（我给的用例有结构性缺陷，实施者的加固是对的，且没动我的原两行）。
已派独立复审核验这个论证与加固本身是否正确。

白捡一条：新增的 `(0.5, 1.2)`（和=1.7）让「**先范围、后和校验**」的顺序也有了回归网
（变异 X3 被杀）—— 顺序重要性此前无用例覆盖。

## Task 4 修复内容（对照上表）

| 项 | 落地 |
|---|---|
| Important #1 权重范围 | `base.py` +4 行：两条 `[0,1]` 闭区间检查，插在和校验**之前** |
| Important #2 校验分支覆盖 | 5 组用例（12 → 21），含 `parametrize` 四个限额字段 |
| Minor #1 下钳 | `test_packet_clamps_negative_relevance_score`（`-0.4 → 0.0`） |
| Minor #2 漂移 + round | `from dataclasses import fields` + `set(payload) == {f.name for f in fields(BuildStats)}`；四处超精度构造值 + 4 条舍入断言 |
| Minor #3 BuildResult | 构造真实 `BuildStats`，断言 `result.context == "ctx"` + `result.stats is stats` |

`round` 实测无差异：`round(1.2345,2)=1.23`、`round(0.042857,4)=0.0429`、
`round(0.98765,4)=0.9877`、`round(0.40123,4)=0.4012`。

「不要做的」清单全部遵守：未加 min_relevance↔weight 互校、未拆嵌套 dataclass、
未改 `asdict()`、未给 `experiment` 加运行时校验、字段/默认值/`__all__` 未动。

## Task 4 质量评审的处置（结论：可以合入，无 Critical）

| 项 | 处置 |
|---|---|
| Important #1 校验分支 4 处零覆盖 | 已派发 implementer 补 5 组用例（含 parametrize 四个限额字段） |
| Important #2 权重范围校验缺口 | **实现改动**，已派发：`relevance_weight` / `recency_weight` 各自校验 [0,1]，再校验和为 1；同步写入 spec §6 / §4.4 |
| Important #3 experiment 零运行时校验 | **Task 4 无法修**（experiment.py 不存在）。记入 Task 6：`ContextConfig.__post_init__` 补 isinstance 校验 + 补用例；已写入 plan Task 6 与 spec §6 |
| Important #4 文件合居 / 行数口径 | **已定案方案 A**：拆出 `builder.py`。plan 与 spec 已全面改写（提交 `3d55625`） |
| Minor #1 下钳无测试 | 已派发补 `relevance_score=-0.4 → 0.0` |
| Minor #2 to_dict 漂移检测 + round 未考核 | 已派发补 `set(payload) == {f.name for f in fields(BuildStats)}` + 超精度值 |
| Minor #3 BuildResult 传 stats=None | 已派发改为复用 BuildStats 实例并断言 `result.stats is stats` |
| Minor #4/#5/#6/#7 | 记录在案未修（评审列为可选）。其中 #7 `token_count` 非负校验若后续被证明会虚增预算再补 |

评审同时否决了三处「看似该修」的建议，理由已采纳：
- `min_relevance` 与权重**不要**互校（语义正交，强校会禁掉有意义的配置）
- `ContextConfig` / `BuildStats` **不要**拆嵌套子 dataclass（实验覆盖白名单依赖扁平结构）
- `to_dict()` **不要**换 `asdict()`（定点舍入 + 嵌套展开是显式契约）

## 方案 A 的导入区策略（重要，Task 7–11 实施时必须遵守）

`builder.py` 的导入区**按需逐任务补齐**，不要一次写全。理由：每个任务收尾都要
对触碰的文件跑 `ruff check` 清零，提前导入未使用的名字会触发 F401。
增量已写进 plan 的 Task 7–11 各自的 Step 3：
- Task 7：`logging`/`tiktoken`/`UTC,datetime`/`Any` + `_SOURCE_TYPES,ContextConfig` + budget/cache/experiment/scoring/core/tools.base
- Task 8：`hashlib` + `ToolStatus` + `ContextPacket`
- Task 9：`math`
- Task 10：`_TEMPLATE_ORDER` + `ContextSection`
- Task 11：`perf_counter` + `TYPE_CHECKING,Any` + `BuildResult,BuildStats` + `if TYPE_CHECKING: ExperimentSpec`
- [ ] Task 3 补齐前置导出
- [ ] Task 4 核心数据类型
- [ ] Task 5 相关性打分器
- [ ] Task 6 轻量 A/B 实验
- [ ] Task 7 ContextBuilder 骨架
- [ ] Task 8 Gather
- [ ] Task 9 Select
- [ ] Task 10 Structure/Compress
- [ ] Task 11 编排入口与统计
- [ ] Task 12 修 RAGTool 丢 score
- [ ] Task 13 公开导出
- [ ] Task 14 离线示例
- [ ] Task 15 README
- [ ] Task 16 全量验证

## 环境事实（执行前核实）

- `core/__init__.py` 已导出 `Message` / `MessageRole`；`ConfigError` 未导出（Task 3 加）
- `memory/__init__.py` 未导出 `cosine_similarity`（Task 3 加）
- 无 `[tool.ruff]` 段、无 ruff.toml、无用户级 ruff 配置
- **ruff 有效规则集远大于默认**（含 UP / DTZ / BLE / I / F / E）
- **BLE001 边界（2026-09-23 实测修正）**：只对**吞掉式** `except Exception` 报
  （`return` / `print` 后继续 = 报）；对**转抛式**（`raise X(...) from exc`）**不报**。
  实测三探针：A 吞掉 → Found 1 error；B 转抛 ConfigError → All checks passed；
  C 打日志继续 → Found 1 error。
  由此：`scoring.py` 的 `# noqa: BLE001` **必要**（吞掉式降级）；
  `builder.py` 的 tiktoken `except → raise ConfigError` **不应加** noqa（加了触发 RUF100）；
  `rag_tool.py:51` / `memory_tool.py:55` 是吞掉式（`return ToolResponse.error`）→ **会报**，
  P-1 的 `# noqa: BLE001` 修法仍然成立。
- `uv run ruff check hello_agents tests` = **39 errors**：
  - 25 → `context/base.py`（本次重写，应清零）
  - 1 → `context/cache.py`（UP046 non-pep695-generic-class，Task 1 引入）
  - 13 → 既有其他模块（search.py 3、core/llm.py 3、chain.py 1、
    rag_tool.py 1、memory_tool.py 1、calculator.py 1、neo4j_store.py 1、
    simple_agent.py 1、react_agent.py 1）
- **既有失败测试**：`tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing`
  在 HEAD（本分支未改任何 embedding 文件）即失败 → 既有缺陷，非本次引入。
  根因：`create_embedding` 里 `if not cfg.dashscope_api_key and backend == "auto"`
  把 API key 校验限死在 auto 分支；显式 `backend="dashscope"` 且 key 为 None 时
  不会抛错，`DashScopeEmbedding.__init__` 也不校验 key，于是静默返回。
  与 docstring「显式指定 backend 时失败会抛出异常，不做静默降级」矛盾。
  一行修复：去掉 `and backend == "auto"`。
  → Task 16 处理，需在最终报告里明确说明这是计划外的一行修复。

## 测试基线

- Task 1 之前：`69 passed, 1 failed, 1 skipped`
- Task 1 之后（含 6 个 cache 用例）：`75 passed, 1 failed, 1 skipped`
- Task 16 判定标准：**失败数不增加**（唯一失败是既有的 embedding 用例，另行修复）

## ⚠️ 并发会话冲突（重要，需向用户报告）

**另一个 Claude 会话在同一仓库、同一分支 `feat/context-management` 上并行工作**，
做的是另一个特性（structured note tool）。证据：
- 提交 `8230825 docs: add structured note tool design spec` 夹在我的 `7d2ca1f` 与
  `c30d2b9` 之间 —— **不是我提交的**。
- `docs/plans/2026-09-23-note-tool-plan.md`（42KB）与
  `docs/specs/2026-09-23-note-tool-design.md`（28KB）由该会话创建
  （时间戳 15:54 / 16:04，均在我这次会话开始之后）。

后果：我用 `git add docs/` 时把对方的 `note-tool-plan.md` 扫进了我的 `270ca4e`。
**没有数据丢失** —— 两个文件都在磁盘上且都已入库。

处置：
- **不改写历史**（对方会话正在活动，改写会破坏它的状态）。
- **收紧提交习惯**：从此只用 `git add <确切文件>`，绝不用 `git add docs/` 或 `-A`。
- 需在最终报告里向用户说明这一冲突，并建议两个特性分到不同分支/工作树。

## 中断与恢复记录

- 会话 `/exit` 后重启，用户要求**改用中文交流**并继续被中断的任务。
- 中断点：Task 4 的**代码质量评审**子代理因 API 429（5 小时用量配额耗尽，
  重置时间 19:47:03 +0800）而中止，只留下半句「先探查仓库结构」，无结论。
  Task 4 的实现（`c30d2b9`）与 spec 合规评审均已通过，只缺质量评审。
  已重新派发（中文提示）。
- 并发会话（note-tool）在我上次会话之后又提交了 `97e542d`、`9d907bd`。
  我的 `1fa7f59` 落在最前面。工作树当前干净。
- 测试基线：`99 passed, 1 failed, 1 skipped`。
- 机器提示：PATH 上的 `python3` 是 Store 存根（静默退出 49），一律用
  `uv run python` / `uv run pytest`。

## Task 5 计划测试块的预检（派发前我自己过了一遍变异视角）

计划 `tests/test_context_scoring.py` 的 16 个用例里，**4 处是混杂断言**（目标路径删掉仍绿）。
与 Task 2 同一类病，必须在实施时修掉，并做变异验证：

| # | 用例 | 混杂原因 | 修法 |
|---|---|---|---|
| C1 | `test_embedding_scorer_handles_empty_contents` | 删 `if not contents: return []` 后，`_embed_many(["查询"])` → `vectors[1:]` 仍是 `[]`，断言照样过 | 改用 `_CountingEmbedding`，断言 `embedding.calls == 0` |
| C2 | `test_embedding_scorer_returns_zero_for_empty_query` | 删 `if not query` 早退后，TF-IDF 对空串是零向量，cosine 仍给 0.0 | 同上：断言 `embedding.calls == 0`（钉住「不打点」而非「结果是 0」） |
| C3 | `test_embedding_scorer_ranks_identical_text_highest` | 两个 content 长度不同（7 字 vs 8 字）。若 score 是长度的单调函数（如 `1-len/100`）`same > unrelated` 仍成立 | ① `same == pytest.approx(1.0)`（已实测：`cosine(v,v)==1.0`）② 换**等长**对照 |
| C4 | `test_keyword_scorer_scores_english_overlap` | content 长度 27 vs 15 字符。长度型 score（`len/100`）同时满足 `related > unrelated` 与 `0.0 <= related <= 1.0` | 等长对照 + `pytest.approx` 差值钉住 Jaccard 真值 |

**另有 2 处无法用变异钉住、但保留为契约文档**（不必强改，报告里说明即可）：
- `test_keyword_scorer_returns_zero_for_empty_query`：空 query 下早退与 Jaccard 分母路径**都给 0.0**，
  删掉早退分支结果不变。它能杀死「空 query 返回非 0」类变异，只是钉不住早退分支本身。
- `test_score_many_matches_individual_scores`：钉的是 `score_many == 循环 score` 这个契约，
  与实现是否批量无关（向量实现的批量由 `test_embedding_scorer_batches_into_single_call` 单独钉）。

**2 处离线风险 —— 已实测作废，不必处理**：
我亲自验过 `create_embedding("auto")` 在**离线无 `DASHSCOPE_API_KEY`** 下能跑通：
按 dashscope → local → tfidf 探测，前两级失败即降级，最终返回 `TFIDFEmbedding(dim=512)`，
`embed_texts(['你好世界'])` 得到非零向量。所以
`test_create_relevance_scorer_embedding` / `test_create_relevance_scorer_auto_returns_usable_scorer`
的缺省构造**离线可用**，无需显式注入。
（顺带再次确认 Task 16 Step 0 那一行的 bug：`create_embedding.py:170` 的
`if not cfg.dashscope_api_key and backend == "auto"` —— 显式 `backend="dashscope"` 且
无 key 时**不会**走 170-171 的 raise，直接拿 `api_key=None` 构造 `DashScopeEmbedding`，
与 157 行 docstring「显式指定 backend 时失败会抛出异常，不做静默降级」矛盾。）

**但由此发现更严重的覆盖缺口 C5/C6 —— `create_relevance_scorer("auto")` 的两条分支零覆盖**：

| # | 缺口 | 说明 | 要求 |
|---|---|---|---|
| C5 | `auto` 的「优先向量」分支无断言 | `test_create_relevance_scorer_auto_returns_usable_scorer` 只断 `0.0 <= score <= 1.0`。若 `auto` 错误返回 `KeywordOverlapScorer`，断言照样过 —— 完全没验「优先向量」 | 工作环境向量可用时，断言 `isinstance(scorer, EmbeddingSimilarityScorer)` |
| C6 | `auto` 的「构造失败降级关键词」分支**零测试** | `create_relevance_scorer` 的 `except Exception` + `logger.warning` + 返回 `KeywordOverlapScorer()` 整条路径没有任何用例。与 Task 4 Important #2 同类（分支删掉测试仍绿） | 补一条：monkeypatch 让 `EmbeddingSimilarityScorer` 构造抛错，断言返回 `KeywordOverlapScorer` |

这两条是**分支覆盖缺口**，不是混杂断言，优先级高于 C1–C4。

**1 处潜在对齐缺陷**（实施时决定修或说明）：
`EmbeddingSimilarityScorer._embed_many` 末行
`return [vector for vector in vectors if vector is not None]`
—— 若批量返回的向量少于请求，列表会被**静默截断**，`score_many` 的
`vectors[0]` / `vectors[1:]` 与 `contents` 错位，分数张冠李戴。建议改为填充后
断言无 `None`（缺失即抛），而不是过滤。请用变异验证「截断」是否真的会导致错位。

**1 条 carried-forward 断言**（Task 3 评审转交）：
`test_cosine_similarity_is_exported_from_memory_package` 只验运行时可导入，
要补 `assert "cosine_similarity" in hello_agents.memory.__all__`；
`ConfigError` 同样补一条 `__all__` 成员资格断言。

## Task 6 计划测试块的预检（Task 5 实施期间并行做完，已实测）

12 个用例。**E1/E2 是分支零覆盖，E3 是坏掉的用例，E4 是缺正例** —— 必修。
E5–E9 是决策/说明项。所有标「已实测」的数字我都跑过脚本，不是估计。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| E1 | **必修·分支零覆盖** | `ExperimentSpec.__post_init__` 的 `if not self.name` **无任何测试**。删掉这 3 行，12 个用例全绿 | 补 `ExperimentSpec(name="", variants={"control": {}})` 抛 `ConfigError` |
| E2 | **必修·分支零覆盖** | `ExperimentAssigner._normalized_weights` 的 `if total <= 0` **无任何测试** | 补 `weights={"control": 0.0, "variant_a": 0.0}` 抛 `ConfigError`。**已实测**：零和与 `{-1.0, 1.0}`（和=0）都应 raise；`{0.0, 1.0}`（和=1）合法 |
| E3 | **必修·用例坏掉** | `test_seed_changes_assignment` 只断 `isinstance(a, str) and isinstance(b, str)` —— 恒真，与测试名声称的「seed 改变分流」**毫无关系**。而且：**已实测** `unit_id="session-1"` 在 `seed-a`/`seed-b` 下**都给 `variant_a`**，所以把断言改成 `assert a != b` 会**直接失败** | 改成向量级断言：取 ≥32 个 unit_id，两 seed 的**分配序列不同**。已实测 32-unit 序列不同（20/32 位差异），20 个 unit 里 11 个不同 |
| E4 | **必修·缺正例** | `test_config_rejects_non_spec_experiment` 只有负例。若 `base.py` 那段 isinstance 校验被改成**恒抛**，测试照样绿 | 补正例：`ContextConfig(experiment=<合法 ExperimentSpec 实例>)` 成功构造 |
| E5 | 决策 | `ExperimentAssigner.apply` 的 `except TypeError` 在**合法 spec 下不可达** —— `ExperimentSpec.__post_init__` 已拦未知字段，`dataclasses.replace` 只在字段名非法时抛 `TypeError`，而运行时不做类型检查。只有「构造后就地改 `spec.variants`」才进得去（`ExperimentSpec` 是可变 dataclass） | 二选一并说明理由：① 补一条用例，构造合法 spec 后就地塞入非法字段名，断言 `apply` 抛 `ConfigError`；② 删掉这个 `except`，让 `TypeError` 冒泡。**不要**留没测试的防御分支 |
| E6 | 必做·先实测再定带宽 | `test_assignment_distribution_follows_weights` 的带宽 `(0.70, 0.80)` 是估的。SHA-256 分流是**确定性**的，不是随机采样 —— 真实比例是个固定数 | **已实测**：3:1 权重、`seed="hello-agents"`、`spec.name="scoring_v1"`、`f"u{i}"`、n=2000 → `control=0.7570`；n=5000 → `0.7554`。**落进 (0.70, 0.80)**，但离上界只剩 0.043。建议：保留带宽的同时**把 0.7570 记成基线**，并**补一条按文档公式精确比对的用例**（见下） |
| E7 | 说明·契约非分支 | `test_single_variant_spec_always_returns_it` 删掉 `if len(names) == 1` 早退后仍绿。**已实测**单变体下 bucket 恒 < 1.0（200 样本全过），归一化权重累积到 1.0，`bucket < 1.0` 恒真 → 仍返回唯一变体 | **保留**为契约文档，报告里注明「钉不住早退分支」。补救可选：`weights={"control": 0.0}` 的单变体在**有**早退时返回 `control`、**无**早退时 `_normalized_weights` 抛「权重之和必须为正数」—— 可用这个区分，但属刁钻输入，你定 |
| E8 | 决策 | `weights` 只查 `missing`（缺变体），**不查多余键**（静默忽略）；也**不查负权重**（只查总和 > 0，`{-1.0, 1.0}` 会因和=0 被拦，但 `{-0.5, 1.0}` 和=0.5 会放行并产生诡异累积区间）。spec §4.4 只写「`weights` 覆盖全部变体」，没说这两条 | 定下来并实现：建议**拒负权重**（`ConfigError`）、**拒多余键**（与未知覆盖字段同款报错）。若你要放宽，说明理由 |
| E9 | 约定 | 多校验共享约束处按新写入 plan「约定」的规则用 `match=` | `test_apply_reruns_config_validation` 等裸 `raises(ConfigError)` 处，能用 `match=` 钉住具体分支的就用 |

**强烈建议补的一条（能一次钉住 E3+E6+算法本身）**：
按 spec §4.4 的文档公式**精确比对** —— 自己算
`sha256(f"{seed}:{spec.name}:{unit_id}")` → `bucket` → 累积区间 → 期望变体名，
断言 `assigner.assign(...) == 期望值`。零随机、零带宽、能同时钉住 seed 参与、
权重归一化、键插入序、bucket 公式。E6 的分布带宽就降级为粗粒度健全性检查。

**关于 `return names[-1]` 兜底（不必测）**：**已实测** `(2**64-1)/2**64 == 1.0`
（float64 舍入），所以 bucket 理论上能取到 1.0，累积到 1.0 时 `bucket < 1.0` 为假，
必须靠 `return names[-1]` 兜住。200k 抽样最大值 0.99999912…，实际打不到 ——
这个兜底**正确且必要**，但不可测，报告里说明即可。

## Task 7 计划测试块的预检（Task 5 实施期间并行做完，已实测）

10 个新用例。**F1 是硬阻塞（lint 门槛过不去）**，已直接修进 plan。
F2–F4 是混杂断言 / 越界行为，必修。F5–F9 是覆盖缺口或决策项。

| # | 级别 | 问题 | 处置 |
|---|---|---|---|
| F1 | **硬阻塞·已修 plan** | `_count_by_source(packets: list[ContextPacket])` 用到 `ContextPacket`，但 Task 7 的导入区**故意**只导 `(_SOURCE_TYPES, ContextConfig)`，把它推到 Task 8。**已实测** ruff 的 `F821` 会打在注解里这个未定义名字上 —— `from __future__ import annotations` 也挡不住。结果是 Task 7 过不了本计划自己定的「触碰文件 lint 清零」门槛 | **已修**：plan 的 Task 7 导入区改为 `(_SOURCE_TYPES, ContextConfig, ContextPacket)`，并把 Task 8 的导入增量里 `ContextPacket` 那条删掉（改为「已在 Task 7 导入」），附上理由 |
| F2 | **必修·混杂** | `test_parse_timestamp_accepts_iso_and_datetime` 的 `assert parsed.year == 2026`。把 `fromisoformat` 整个删掉、一律退回 `datetime.now(tz=UTC)`，断言**照样过** —— 今天正是 2026 年。**已实测** | 改成精确比对：`_parse_timestamp("2026-09-23T10:00:00")` 必须等于 `datetime(2026, 9, 23, 10, 0, 0)`（注意 `fromisoformat` 给的是 **naive**，比较时别带 tzinfo） |
| F3 | **必修·混杂 + 时区契约未钉** | 同用例的 `isinstance(_parse_timestamp(None), datetime)` 与 `isinstance(_parse_timestamp("not-a-date"), datetime)` 只断类型。退回值是不是「当前时刻」、是不是 **tz-aware UTC**，全没钉 —— 而 naive `datetime.now()` 是 DTZ005，本计划明令禁止 | 补两条：① 退回值与 `datetime.now(tz=UTC)` 相差在秒级内；② `result.tzinfo is UTC`（或至少 `tzinfo is not None`） |
| F3b | **决策·时区归一化该在哪一层** | 计划的 `_parse_timestamp` 对 ISO 串**原样返回 naive**（`fromisoformat` 不带 tz）。Task 9 的 `_calculate_recency` 又做一次 `if timestamp.tzinfo is None: replace(tzinfo=UTC)`。两处都在管 tz。spec 的既定策略写的是「naive 解析结果用 `timestamp.replace(tzinfo=UTC)` 归一」，读起来是**在解析处归一** | 定一个并统一：建议 **`_parse_timestamp` 出口一律 tz-aware UTC**（解析成功也 `replace(tzinfo=UTC)`），`_calculate_recency` 的归一作为纵深防御保留但不再依赖。两条路选一条，报告说明 |
| F4 | **必修·越界行为未定义** | `_count_by_source` 遇到 `metadata={"type": "bogus"}` 会**多出第 6 个键**。**已实测**：`counts` 变成 6 项（多出 `'bogus': 1`），与 spec「五种来源恒出现在结果中」冲突。计划的 `test_count_by_source_always_lists_all_five_types` 用的是合法 type，**测不到** | 定下来并测：未知 `type` 应归入 `custom`（`_SOURCE_TYPES` 里没有的键一律落到 `custom`），**不要**让第 6 个键泄漏进 `BuildStats.candidates_by_source`。补一条未知 type 的用例 |
| F5 | **必修·公式低端未钉** | `_compute_budget` 的缩放公式 `scaled = int(max_tokens * (min + span * complexity))`，只有 complexity=1.0 那一端被 `test_budget_uses_injected_policy` 的 `scaled == 1000` 钉住。**已实测** complexity=0.0 → 500、0.5 → 750，**低端 500 无任何断言**。若公式被改成 `int(max_tokens * max_ratio * complexity)` 之类，1.0 端仍是 1000 | 补 `_FixedPolicy(0.0)` 一端，断言 `scaled == 500`（`max_tokens=1000, min=0.5, max=1.0`）。最好再补 0.5 一端（750） |
| F6 | **必修·混杂** | `test_cache_counters_include_scorer_cache` 只断 `hits + misses > 0`。它能杀死「不合并打分器缓存」（那时两侧都是 0），但**完全没测 builder 自己那个 `self.cache` 的计数** —— 若 `_cache_counters` 只返回打分器缓存、丢掉 `self.cache`，该用例照样绿 | 补一条：显式往 `builder.cache.put(...)` / `get(...)`，断言 hits/misses 里含 builder 自己的贡献 |
| F7 | 覆盖缺口 | `_compute_budget` 的钳制 `complexity = max(0.0, min(1.0, complexity))` 无测试。`_FixedPolicy(1.0)` 只测了上边界值本身，没测**越界输入被钳** | 补 `_FixedPolicy(2.0)` / `_FixedPolicy(-1.0)`，断言 `info.complexity` 落在 `[0,1]` 且 scaled 跟钳后值走 |
| F8 | 覆盖缺口 | 两处未测分支：① `ContextBuilder.__init__` 里 tiktoken 的 `except Exception → raise ConfigError`（monkeypatch `tiktoken.get_encoding` 可测）② `_cache_counters` 的 `isinstance(scorer_cache, TTLCache)` 为假时的跳过路径（用 `KeywordOverlapScorer`，它没有 `cache` 属性） | 补测，或报告里说明「测不了/不测」并给理由 |
| F9 | 说明·可接受 | `test_count_tokens_uses_tiktoken` 的 `_count_tokens(text) == len(encoder.encode(text))` 有同义反复成分（对照物就是实现本身）。但它**确实能杀死 B10 回归**（把 `_count_tokens` 换成 `len(text)` 字符启发式即失败） | **保留**。可选加强：对固定串断言 tiktoken 的**硬编码已知 token 数**，作为独立预言机 |
| F10 | 说明·契约 | `test_budget_scales_with_complexity` 依赖 `HeuristicBudgetPolicy` 真的给出不同复杂度（用长串驱动到 1.0）。它钉的是**集成**不是公式。公式本身由 F5 补的 `_FixedPolicy` 用例钉 | 保留分工，在报告里说明两层各钉什么 |

`return max(1, scaled)` 兜底已被 `test_budget_scaled_max_tokens_is_at_least_one` 钉住
（`max_tokens=1, min=max=0.1` → `int(0.1)=0` → 钳到 1），**已实测**，不缺。

## 待办：Task 5 实施时要带上的一条补充断言

Task 3 评审建议（Minor，转交 Task 5，因为 Task 5 会重写 `tests/test_context_scoring.py`）：
`test_cosine_similarity_is_exported_from_memory_package` 只验证了运行时可导入，没验证
`__all__` 成员资格。若将来有人只删掉 `__all__` 里的名字而保留 import，该用例仍会通过，
而 `from hello_agents.memory import *` 会静默少一个名字。
建议 Task 5 在该文件里补 `assert "cosine_similarity" in hello_agents.memory.__all__`。
同类：`ConfigError` 也可在 Task 4 或 Task 5 的测试里补一条 `__all__` 断言。

## ⚠️ 标准执行要求（适用于所有后续任务）

**测试必须做变异验证（mutation check）**：对每个断言目标，临时禁用它所针对的代码
路径，确认该用例**失败**。只断言 `a > b` 而没有长度/量级对齐的用例，很容易被
无关的大权重项满足 —— Task 2 评审就抓到 4 个这样的用例（含 3 个来自计划原文），
它们在目标路径被完全删除后依然通过。

后续任务（尤其 Task 5 打分器、Task 9 选择、Task 10 压缩）的实现者，报告里必须
带上「真实值 vs 变异后值」的对照，不能只说「测试通过」。

## 已记录的偏差 / 决策

### D1 — ruff 有效规则集比默认严，泛型改用 PEP 695
`requires-python = ">=3.13"` → ruff 推出 `target-version = py313`，`class TTLCache(Generic[K, V])`
触发 UP046。已同步修改：
- `hello_agents/context/cache.py`（Task 1 修复提交）
- 计划 line 144、Spec line 123（去掉 `Generic`/`TypeVar`）
- 计划 line 38「约定」新增「泛型一律 PEP 695」
- Spec §7.2 新增同款约定

### D2 — lint 门槛按文件范围而非全仓
`uv run ruff check .` = 71；`uv run ruff check hello_agents tests examples` = 39；
其余 32 个在根目录遗留脚本（`Plan_and_solve.py` 15、`Reflection.py` 9、`tools.py` 6、
`ReAct.py` 1、`llm_client.py` 1），不在库范围内。
39 个中：25 在 `context/base.py`（本次重写清零）、1 在 `context/cache.py`（Task 1 修复）、
13 在本次不涉及的其他模块（记录在案不修）。

### D3 — 既有失败用例改为「随本计划修复」
`tests/test_embedding.py::test_factory_explicit_backend_raises_when_missing` 基线即失败。
根因：c0e26ff 把 `dashscope` 提为核心依赖，使原本依赖 `ImportError` 的路径失效；
而 `create_embedding` 的 key 校验被 `and backend == "auto"` 限死，`DashScopeEmbedding.__init__`
也不校验 key → 显式 dashscope + 无 key 时静默返回坏对象，与 docstring 矛盾。
决策：**修**（一行：去掉 `and backend == "auto"`）。理由：Spec §7.4 验收标准 1 要求
全绿，且该修复严格改善行为（`auto` 路径不变）。
已写入：计划 Task 16 新增 **Step 0**、Spec §7.4 标准 1、Spec §9 新增 Step 0。
⚠️ 最终报告必须明确说明这是**计划外的一行修复**，超出「仅 ContextBuilder」的原始范围。

### D4 — Task 1 的 `_purge_expired` 分支缺测试
代码质量评审发现唯一实质覆盖缺口，已要求补
`test_put_purges_expired_before_evicting`（断言 `size == 1` 且 `evictions == 0`），
顺带钉死「`evictions` 只计容量淘汰，TTL 过期不计数」这一语义。

### D5 — 提交策略
Task 1 的修复走**新提交**（不 amend），保留「评审 → 修复」的审计痕迹。

### D6 — 待办：同步计划里 Task 1 的逐字代码块（低优先级，收尾时一次性做）
计划 Task 1 的 `cache.py` 代码块缺新的 docstring 统计口径说明，测试块缺
`test_put_purges_expired_before_evicting`。属文档记账漂移，不影响下游任务
（下游要抄的 PEP 695 类头已正确）。计划 line 38「约定」已同步。

1. Task 1 的 `class TTLCache(Generic[K, V])` 被 ruff 报 UP046。计划要求
   「ruff 默认配置」但本环境有效规则集更严。决策：**以本环境 ruff 为准**，
   改为 PEP 695 `class TTLCache[K, V]:`，并同步更新后续任务引用。
2. 每个任务收尾必须对**本任务触碰的文件**跑 `uv run ruff check`，
   清零该文件的告警（既有其他文件的 13 个告警不在本次范围）。
3. Task 16 的 lint 门槛调整为：`context/` 全部文件 + 本次新增 tests/examples
   零告警；既有 13 个告警记录在案不修（除非落在 Task 12 会改的 rag_tool.py）。

---

## Task 5 — 相关性打分（scoring.py）

**提交**：`8f93d8d` `feat: add pluggable relevance scorers with embedding cache`
（仅 `hello_agents/context/scoring.py` + `tests/test_context_scoring.py`，无越界）

**独立复核（我方，非实施者自报）**：
- `uv run pytest tests/test_context_scoring.py -q` → **21 passed, 1 warning**
  （PydanticDeprecatedSince20 来自 `core/message.py`，既有告警）
- `uv run ruff check` 两文件 → **All checks passed!**
- `uv run ruff format --check` 两文件 → **2 files already formatted**
- 工作区干净，无残留变异文件

**实施者自报的偏离 D1–D3**（待规格符合性评审判定）：
- **D1**：C7 修法 —— `_embed_many` 遇 `None` 向量抛 `RuntimeError`（消息含「向量数量不足」），
  绝不 filter。选 `RuntimeError` 而非 `ConfigError`：后端运行时契约被破坏，非用户配置错误；
  静默错位（分数张冠李戴）比抛异常更糟。
  实证（旧 filter 逻辑）：部分缓存 + 短批次 → 向量个数 2（应 3），`vectors[0]` 实为内容乙的向量，
  `score_many` 返回 `[1.0]`，调用方把 1.0 安到内容甲头上 → **真·张冠李戴**，非仅长度截断。
- **D2**：`cache` 标注 `TTLCache[str, list[float]]`（计划为裸 `TTLCache`）—— PEP 695 泛型精度。
- **D3**：docstring 示例 import 改为 `from hello_agents.context.scoring import ...` ——
  本任务禁改 `context/__init__.py`，顶层尚未导出。

**新增计划外用例**（正当理由待评审确认）：
- `test_embedding_scorer_clamps_negative_cosine_to_zero` —— 规格 4.3 明文
  `score = max(0, min(1, cosine))`。删 `_clamp01` 后原 20 用例全绿（TF-IDF 余弦天然 ≥0），
  此用例用反方向向量把 M14 从 SURVIVED 变 KILLED。

**主动降级为「契约文档」的 2 条用例**（在注释里写明钉不住目标分支）：
- `test_keyword_scorer_returns_zero_for_empty_query` —— 空 query 下早退分支与 Jaccard 分母
  路径都返回 0.0，删掉早退分支结果不变，钉不住早退分支本身。
- `test_score_many_matches_individual_scores` —— 钉「score_many 等价逐条 score」契约，
  与是否批量无关（批量由 `…batches_into_single_call` 钉）。

**实施者自报变异对照表 18 条全部 KILLED**（M1–M18，含 M9b）。M14 首跑 SURVIVED，补用例后 KILLED。
C1–C7 均自报已修。**待两阶段评审核实。**

**C1–C7 落地形态（我方代码抽查）**：
- C1/C2：`embedding.calls == 0` 已断言（tests L182 / L170）
- C3：`same == approx(1.0)` + 等长 8 字 CJK 对照 `"服务器磁盘告警了"` + 差值钉扎（tests L153–158）
- C4：等长 22 字对照 + Jaccard 真值 `1/3` 与 `0.0` + 差值（tests L104–108）
- C5：`isinstance(scorer, EmbeddingSimilarityScorer)`（tests L241）
- C6：monkeypatch 构造抛 `RuntimeError` → 断言 `KeywordOverlapScorer` + caplog「降级」（tests L245–261）
- C7：`_embed_many` 末段逐元素检查，`None` 抛 `RuntimeError`（scoring.py L108–116），
  配两条用例：`_ShortBatchEmbedding`（少返回一个）与 `_EmptyBatchEmbedding` + 部分缓存
- carried-forward：`cosine_similarity` / `ConfigError` 的 `__all__` 断言（tests L67–76）

**评审状态**：规格符合性评审进行中（两阶段第 1 阶段）。
**第 1 阶段结论（`8f93d8d`）：APPROVED** —— C1–C7 全部「已落地」（逐条给了 file:line），
carried-forward 的 `__all__` 断言已补（tests L69 / L75），5 个偏离全部「合理」，Spec §4.3
九款契约无遗漏无违背，交付物齐全。无必须修条目。
第 1 阶段留的一条**非问题备注**：删 `_clamp01` 的 `min(1.0, …)` 半边单独变异会 SURVIVE
—— 数学上余弦 ≤ 1，`[2.0, 0.0]` 恰好余弦 = 1.0；`min(1)` 是浮点溢出保险丝，不是可观测契约。
**已转交第 2 阶段判定是否可接受。**
**第 2 阶段（代码质量 + 独立变异复核）已派发。**

**第 2 阶段结论（`8f93d8d`）：Ready to merge: No** —— 独立变异 25 条 = **21 KILLED + 4 SURVIVED**。
与实施者自报「18/18 KILLED」不一致：自报集合里若含「只删 `min(1.0)`」则偏乐观。

SURVIVED 四条的判定：
| 变异 | 判定 |
|---|---|
| M4 只删 `_clamp01` 的 `min(1.0,…)` | **真覆盖缺口（必须修）**。不是保险丝 —— 实测 `cos([1,1,1],[1,1,1]) == 1.0000000000000002`，去掉 `min` 后返回值溢出 `[0,1]` 契约。杀法：三维全 1 自比 + **严格** `== 1.0`（不能用 `approx(1.0)`，默认 abs=1e-12 吞 2ulp） |
| M20 删 keyword 的 `if not query_tokens` | 不可观测保险丝（可接受） |
| M21 删 `if not union` | 不可达死代码（可接受） |
| M14 `_cache_key` 用原文当 key | 等价变异（可接受） |

代码质量：**无 Critical**。1 条 Important（`_embed_many` 的 `zip` 对**超额**向量静默截断，
与 C7 不对称 —— 多余向量前置时会错位且无声）+ 4 条 Minor（死代码、`_clamp01` 三处重复、
keyword 重复 tokenize、`cosine_similarity` 维度静默截断）。

**已转回实施者修复**（R1 必修 = M4 杀法；R2 = 超额对称；R3 = `score("", "")`）。
明确**不要做**：抽共享 `_clamp01`、keyword tokenize 复用、改 `memory/embedding.py`、钉 M14。

**修复轮已回报（提交 `32227c5` `fix: harden scorer clamp and embed batch size contract`）**：
- R1 仅补测试 `test_embedding_scorer_clamps_unity_cosine_rounding_overflow`（三维全 1 自比，
  严格 `== 1.0`）。自报变异：删 `min(1.0, …)` → `score == 1.0000000000000002` → **KILLED**
- R2 `_embed_many` 加对称数量契约（不足/超额各自 `RuntimeError`，消息可 `match=` 区分）+
  `zip(..., strict=True)` 二道防线 + `_OverBatchEmbedding` 用例。自报 R2b 演示了错位张冠李戴
  （真值 `[0.732, 0.732]` → 变异后 `[0.0, 0.0]`，全程无异常）→ **KILLED**
- R3 双空用例 `score("", "") == 0.0`；`if not union` 注释为死代码兜底。R3a/R3b 单删任一防护
  仍 SURVIVED（与 M20/M21 同源等价，预期内）；R3c 两道全删 → `ZeroDivisionError` → **KILLED**
- 24 passed（21+3）；全量 `1 failed, 171 passed, 1 skipped`（唯一失败仍是既有 embedding 用例）
- **复审已派发**（只针对修复面，不重跑 25 条全表）。

**修复复审结论（`32227c5`）：Ready to merge: Yes —— Task 5 关闭。**
复审独立验证：R1 删 `min(1.0)` → `1.0000000000000002 != 1.0` → KILLED；
R2 三条变异（R2x/R2y/R2z）全 KILLED，且 `match=` 交叉验证矩阵证明「不足/超额」两条消息
互不误匹配（含末段 None 兜底消息）；R3 如实未夸大（docstring 明写单删任一防护不可观测）。
无变异残留（`sha256sum -c` OK、`git diff HEAD` 空）。

复审留 2 条 Minor（**不阻塞，记录不改**）：
1. `scoring.py:70` 死代码注释措辞（「两道防护全被拆掉时的最后防线」逻辑上不成立，
   准确说法是「若 `query_tokens` 防护被拆，本行是防除零的最后防线」）
2. `scoring.py:123-127` 末段 `None` 兜底仍报「向量数量不足」，在「条数正确但元素为 None」
   时文案有偏差（该兜底反而是 R2z 下短批用例的双保险，不是缺口）

**Task 5 最终交付**：`8f93d8d`（初版 21 用例）+ `32227c5`（评审修复 +3 用例 = 24）。
两阶段：spec APPROVED / quality No→修复→**Yes**。

---

## Task 8 预检（G 系列）—— Gather 用例块

对象：计划 L1894–2217 的测试块（14 个用例）+ Step 3 实现块。

**已实测排除（不是问题）**：
- **G1 中置 import 会触发 E402？否。** `uv run ruff check --isolated` 对「函数定义后跟 import」的探针文件
  报 **All checks passed!**。E402 不在本环境有效规则集内。Task 8 测试块里放在文件中段的
  `from hello_agents.core import Message, MessageRole` / `from hello_agents.tools.response import ...`
  **不会**卡 lint 门槛（与 Task 7 的 F1 `F821` 不同，F821 是真报）。
- **G2 `f"{message.role}: ..."` 会泄漏枚举 repr？否。** 实测 `MessageRole` 的 f-string 渲染是 `user`
  （不是 `MessageRole.USER`），正文即 `user: 第7轮`，spec §5.2 的 `"{msg.role}: {msg.content}"` 可用。
- **G3 `_memory_hits` 形状是否忠于真实契约？是。** `MemoryTool.recall` 返回
  `item.to_dict() | {"score": item.score}` = `{id, content, memory_type, metadata, created_at, expires_at, score}`
  ——与计划的 `_memory_hits` 逐键相同。`RAGResult.chunks: list[MemoryItem]`（`memory/rag/pipeline.py:25`）
  故 RAG chunks 同形状，测试替身用 `_memory_hits` 是对的。
- **G4 `ContextConfig(min_importance=…)` / `(min_source_score=…)` 是否存在？是。** Task 4 的 18 字段里确有
  `min_importance` / `min_source_score`（默认 0.0），Task 8 的 kwargs 不会 TypeError。
- **G5 `ToolResponse` / `ToolStatus` 契约是否匹配？是。** `ToolStatus` 有 SUCCESS/PARTIAL/ERROR；
  `ToolResponse(status=…, text=…, data=…)` 构造可用，`getattr(response, "status"/"data"/"text")` 与
  实现块的防御式取值一致。

### 必修

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **G-a** | `test_gather_skips_tool_error_response` **结构性钉不住** ERROR 检查分支 | `_FakeTool(status=ERROR)` 的 `data` 是 `{}`。删掉 `if status == ERROR: return []` 后，`hits = {}.get("hits") or []` 仍得 `[]`，断言照样过（实测两种情形都返回 `[]`）。要钉住必须让 ERROR 响应**带非空 hits** | 改成 `_FakeTool(data={"hits": _memory_hits("x")}, status=ToolStatus.ERROR)`；无检查时会得到 1 个包 → KILLED |
| **G-b** | RAG 侧的 ERROR / 异常两条路径**零测试** | 现有用例只对 `memory_tool` 注 ERROR 与 `raises`。删掉 `_rag_packets` 的 try/except 或 ERROR 检查，全部用例仍绿 | 补两条孪生用例：`…skips_rag_tool_error_response`（带非空 chunks + ERROR）、`…skips_rag_tool_exception` |
| **G-c** | `raw_score is None` 分支**零覆盖**，而它正是**当下 RAG 的在线路径** | `MemoryItem.to_dict()` **不含 `score`**（`memory/base.py:60-67`）；`rag_tool.py:125` 现在是 `chunk.to_dict()`（无 `score`）——B15 要到 Task 12 才修。所以真实 RAG 命中走 `hit.get("score") → None → score=0.0, relevance_score=None`。`_memory_hits` 恒写 `"score": score`，该分支不可达 | 补用例：`del hits[0]["score"]` 后断言 `relevance_score is None`（交 Task 9 计算）+ `metadata["source_score"] == 0.0` |
| **G-d** | 缺 score 的命中会被 `min_source_score > 0` **整体丢光**，且边界未钉 | spec L398 明文「score（缺失时按 0.0 处理）低于 min_source_score 的丢弃」→ 实现合规。但 Task 12 之前所有 RAG 命中都缺 score，用户一旦设 `min_source_score=0.1` 就静默丢掉全部 RAG 命中（产品陷阱）。另外 `score == min_source_score` 是**保留**（`<` 而非 `<=`），把比较改成 `<=` 时现有用例全绿（0.05 vs 0.5、0.8 vs 0.0 都碰不到边界） | 补两条：① 缺 score + `min_source_score=0.1` → `== []`（钉 spec 语义）② `score=0.5` + `min_source_score=0.5` → 保留 1 个（钉 `<` 边界）。`importance == min_importance` 同理补一条 |
| **G-e** | `position` 语义在 `len(history) < window` 时**钉不住** | `test_gather_marks_history_position_and_type` 用 3 条消息 + 默认 window=5 → 断言 `[0,1,2]`，这与「全历史下标」**无法区分**（两种实现都得 `[0,1,2]`）。`test_gather_trims_history_to_window` 用 10 条/window=3 但**从不断言 position**。spec L402 明文「0 = 最旧，n-1 = 最新」是**窗口相对**；若实现误写成全局下标（`[7,8,9]`），Task 9 的「按 position 线性映射到 [0.5,1.0]」会全错 | 在 `test_gather_trims_history_to_window` 补 `assert [p.metadata["position"] for p in packets] == [0, 1, 2]` |
| **G-f** | 显式非零 `token_count` 的保留**未钉** | `_gather` 是 `if packet.token_count == 0: packet.token_count = …`。`test_gather_fills_token_count_for_custom_packets` 用的是默认 0；改成「无条件重算」后该用例仍绿（重算结果相同）。**没有**任何用例传入 `token_count=7` 并断言仍为 7 | 补用例：`ContextPacket(..., token_count=7)` → 断言 `packets[0].token_count == 7` |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|----|------|------|
| G-g | `test_gather_keeps_explicit_relevance_score_on_custom_packets` 是**弱钉**：`_gather` 根本不碰自定义包的 `relevance_score`，任何不碰它的变异都存活。B8 真正的修复面在 Task 9 的 `_select` | 保留（它是「gather 不得踩坏预置分」的回归护栏），但在注释里写明「钉的是 absence-of-mangling，真正的 B8 钉扎在 Task 9」 |
| G-h | `test_gather_calls_rag_tool_with_real_contract` 不断言 `relevance_score`（memory 侧断言了 `== 0.8`） | 补 `assert packets[0].relevance_score == 0.9`，两侧对称 |
| G-i | 无用例钉 `_gather` 的**来源顺序**（system → memory → rag → history → custom）。Task 9 的贪心填充对顺序敏感 | 补一条五来源齐全的用例，断言 `[p.metadata["type"] for p in packets] == ["system_instruction","memory","rag","history","custom"]` |
| G-j | 计划的期望计数已漂移：Task 7 写「22 passed」，Task 8 加 14 个用例应为 36，计划却写「37 passed」 | 不改计划（属 D6 同类记账漂移）；实施者报告**实际**计数即可 |
| G-k | `system_instructions=""`（falsy）路径未测；`test_gather_calls_memory_tool_with_real_contract` 只看 `tool.calls[0]`，调两次抓不到 | 低优先级，实施者酌情补 |

### 派发 Task 8 时必带
G-a / G-b / G-c / G-d / G-e / G-f 六条必修 + 变异对照表（每条必修都要有真实 vs 变异值）。
**G-c / G-d 要在派发词里点明「这是 RAG 的当前在线路径，不是边角」**，否则实施者会当成防御式编程省略掉。

**已派发**（Task 8 实施者 `task8-gather`）。派发词含：六条必修全文（含修法与实测证据）、
G-h…G-k 建议、变异对照表要求（必须实际注入再跑）、BLE001 边界（三个 noqa **保留**，
是吞异常式、不是 RUF100）、E402 已排除（中段 import 可照抄）、测试计数「报实际值别硬凑 37」、
提交纪律（显式路径 / 不 amend / 仅 ContextBuilder 范围）。

**Task 8 实施已回报（提交 `27ed0a0` `feat: implement gather stage with real tool contracts`）**：
`builder.py` +129/-0（纯追加 6 方法 + 2 行导入）、`test_context_builder.py` +258/-0。
**65 passed**（42 基线 + 计划 14 + 必修/建议 9）；5 文件回归 **154 passed**（scoring 24 / cache 7 /
budget 10 / experiment 48 / builder 65）。Step 2 红实测：23 failed，全为 `AttributeError: '_gather'`。

**变异对照表 9 条注入 9 条全 KILLED，零 SURVIVED**（脚本 `mutate_gather.py` 可独立重跑，
结束断言 `restore byte-identical = True`，`builder.py` sha256 前缀 `28644e9426e9122f` 与开工前一致）：
G-a 删 memory ERROR 检查 / G-b 删 rag ERROR 检查 / G-b2 删 rag try-except /
G-c 抹 `raw_score is None` 分支 / G-d① 缺分绕过过滤 / G-d② `<`→`<=` / G-d③ importance `<`→`<=` /
G-e position 全局下标 / G-f token_count 无条件重算。

两条**实测副产物击杀**（不是凑数，记下来）：
1. **G-d② 连带杀掉 `test_gather_leaves_scoreless_hits_unscored`** —— 该用例走默认
   `min_source_score=0.0`，缺分命中算 0.0：真实式 `0.0 < 0.0` 为假（保留），变异式
   `0.0 <= 0.0` 为真（丢弃）。**这钉死了默认阈值必须是严格 `<`**，否则零分命中在默认配置下被静默清光。
2. **G-e 的变异刻意做成「3 条消息时退化为 [0,1,2]」**（`start=max(0, len(history)-window)`）。
   旧用例 `test_gather_marks_history_position_and_type`（3 条/window=5）在该变异下**依然通过**，
   只有新加在 10 条/window=3 里的 `assert positions == [0,1,2]` 能杀掉。
   **复核时若拿掉这条断言，G-e 会立刻 SURVIVED** —— 复审员请重点验这个。

**实施者自报偏离 9 条**（2–9 属合理/记账，见下）；#1 需我裁决：

### D8 裁决：全仓 lint 门槛与范围约束互斥（偏离 #1）—— **判定：实施者选择正确，派发词写错了**

派发词写的是 `uv run ruff check hello_agents tests` 与 `format --check` **均须清零**，
但基线就有 **13 个 check 错误 + 2 个待 format 文件**，全部在 `agents/`、`core/`、`memory/`、`tools/`
里 —— 而范围约束是「除 `builder.py` + `test_context_builder.py` 外不改任何文件」。**两条要求互斥。**

实施者选择遵守范围约束、只保证自己两个文件清零并上报。**这是对的**：
- Task 16 Step 2 的真实门槛是「**触碰过的文件**零告警」，不是全仓清零（P-1 已按此预置）。
- 全仓 13 处 BLE001 是**历史遗留**，与 ContextBuilder 无关；硬清会碰 `agents/`（明确禁令）。
- `memory_tool.py:55` / `rag_tool.py:51` 确是**吞异常式** BLE001（注释写明「不应击穿 Agent 循环」），
  需 `# noqa: BLE001`，但那要改禁改文件 —— **P-1 已把 `rag_tool.py:51` 排进 Task 12**
  （Task 12 会触碰该文件），到时一并加。`memory_tool.py` 本计划永不触碰，**不动**。

**决定**：
- 本任务验收门槛改为「**触碰文件零告警**」（已清零 ✅）。
- 全仓 13+2 记为**计划外历史遗留**，**Task 16 Step 2 按触碰文件口径执行**，
  并在最终报告里单列「全仓 lint 基线」一节（不是本次引入）。
- 后续任务派发词一律写「触碰文件零告警」，不再写全仓。

偏离 #2–#9 判定：全部合理。要点：
- #2 导入区半空操作 —— Task 7 已扩好 `ContextPacket`，计划块滞后（D6 同类）。
- #3 尾注形式 `# noqa: BLE001 - 检索失败一律降级` 保留 —— 实测能抑制 BLE001 且**不**触发 RUF100。
  与我在 Task 7 探针的结论一致（吞异常式 noqa 必要）。
- #4/#5 把 G-b 拆 G-b+G-b2、G-d 拆 G-d①②③ —— **比派发词强**，单一变异钉不住多主张。
- #6 G-i 用 `metadata.get("type", "custom")` 而非 `["type"]` —— 对，自定义包默认 `metadata == {}`，
  且口径与 `_count_by_source` 一致。
- #8 G-a 改写既有用例而非新增 —— 对，原形是结构性钉不住。
- #9 G-h/G-i/G-g/G-k 全做 —— 好。

### 实测语义事实（留给 Task 9/12/16）
1. **Task 12 之前所有真实 RAG 命中都缺 `score`**（`rag_tool.py:125` 是 `chunk.to_dict()`，
   `MemoryItem.to_dict()` 不含 `score`）→ 恒走 `raw_score is None` → `source_score=0.0`、
   `relevance_score=None`。**G-c 按主路钉住**。
2. **产品陷阱（spec 语义如此，实现合规）**：用户设 `min_source_score>0` 时，Task 12 之前
   **全部 RAG 命中被静默丢光**。G-d① 已钉。**Task 12 若改语义须连这条用例一起改**。
3. **默认阈值必须是严格 `<`**（见上副产物 #1）。
4. **`position` 是窗口相对下标**（0=最旧），不是全局下标。Task 9「按 position 线性映射到
   [0.5, 1.0]」依赖此语义，G-e 已钉。

**第 1 阶段（规格符合性）已派发。**

**第 1 阶段结论（`27ed0a0`）：Ready to proceed to Stage 2: Yes** —— 无 blocking issues。
G-a…G-k 十条全过（含 G-c 的 RAG 在线主路缺分语义、G-e 的 10/3 窗口相对下标两处关键钉扎）；
合同细节 1–10 全过；九条已批准偏离逐条核实属实；触碰文件 65 绿 + lint 清零。
实跑 `65 passed`（1 warning 是 `core/message.py:20` 的 Pydantic V2 弃用告警，与本提交无关）。

### 第 1 阶段附注的独立复核（我实测，附注 1/4 **推翻**）

评审员的 OBS-1 / OBS-4 是**探针打偏**，不是真问题。实测对照：

| 探针目标 | `f-string` | `type` | 结论 |
|---|---|---|---|
| `MessageRole.USER`（**类成员本身**） | `'MessageRole.USER'` | `MessageRole` | 评审员测的是这个 |
| `message.role`（**pydantic 构造后**） | `'user'` | **裸 `str`** | **gather 走的是这个** |

机理：pydantic V2 构造 `Message(role=MessageRole.USER, …)` 时把枚举**抹成裸 `str`**
（`type(m.role) is str`、`m.role is MessageRole.USER → False`、`m.role == MessageRole.USER → True`、
`__mro__` 只剩 `(str, object)`）。故：
- `f"{message.role}: {message.content}"` → **`'user: 第0轮'`** ✅ 正文干净
- `metadata["role"]` → **裸 `'user'`**，`json.dumps(metadata)` **成功** ✅ OBS-4 同样不成立

**结论：无需任何后续任务跟进这两条。** 已把反证写进第 2 阶段派发词，防止重踩。
（旁证：`class MessageRole(str, Enum)` 的 `f"{成员}"` 在本 Python 确实是 `'MessageRole.USER'`
—— 但这不是 gather 拿到的对象。探针目标差一层，结论就翻。）

其余附注（OBS-2 `float()` 强转在 try 外 / OBS-3 就地改写 custom 包 / OBS-5 `rag_tool.py` 既有
lint 错误 / OBS-6 中段 import）已交第 2 阶段独立判定。我的初判：OBS-2 是真隐患但护栏该在
Task 12 的 `build_result()` 接线处；OBS-3 与计划一致、严重度低；OBS-5 是 **P-1 已排定**；OBS-6 纯风格。

**第 2 阶段（代码质量 + 独立变异复核）已派发。** 重点请验两条「额外击杀」主张：
G-d② 连带杀 `test_gather_leaves_scoreless_hits_unscored`、
G-e 的脆弱性（拿掉 10 条/window=3 的 position 断言会立刻 SURVIVED）。

**第 2 阶段结论（`27ed0a0`）：Ready to merge: No** —— 但**实施者零夸大**。
独立重跑 9 条目标变异 **9/9 KILLED**；两个「额外击杀」主张**全部实证成立**：
- G-d② 连带杀 `test_gather_leaves_scoreless_hits_unscored`：`assert 0 == 1` 实录在案。
  **反向钉扎成立** —— 默认阈值比较必须严格 `<`。
- G-e 两个子实验：(a) 单跑 `test_gather_marks_history_position_and_type` → **passed**
  （3 条/window=5 时 `start=max(0,3-5)=0`，与真实式同像，旧用例确实杀不掉）
  (b) 注释掉 10 条/window=3 的 position 断言后注入 G-e → **SURVIVED，65 passed 全绿**。
  **这行是唯一杀手**，整个 G-e 变异面只靠 1 行守着。

No 的唯一理由：复审员另打 12 + 10 条探针，扎出 **6 条真覆盖缺口**（全在 Task 8 自己地界）。
**全是加测试，实现逐条验过是对的**（limit/top_k 接线对、缓存按指令哈希对、window 早退对、零分记 0.0 对）。

| ID | 缺口 | 变异 | SURVIVED | 修法 |
|---|---|---|---|---|
| **B1** | 契约测试 limit/top_k 是**假钉扎** | `"limit": 10` / `"top_k": 5` | 双是 | 用非默认 `ContextConfig(memory_limit=3, rag_limit=2)`，断言字面量 `== 3` / `== 2`（不要自指 `== builder.config.xxx`） |
| **B2** | `score=0.0` 被静默抬成满分 | `float(raw_score or 1.0)` | 是 | 加 `score=0.0` 用例：`len==1` + `relevance_score==0.0` + `source_score==0.0` |
| **B3** | `history_window=0` 语义倒置成全量 | 删 `window <= 0` | 是 | 加 `history_window=0` + 3 条历史 → `== []` |
| **B4** | 系统指令缓存键可退化常量键（**串包**） | `sha256(...)` → `"const-key"` | 是 | 两条**不同**指令：`is not` + content 各自正确 |
| **B5** | 命中包/历史包 `token_count` **零断言** | `_count_tokens` → `len()` | 双是 | 补两处断言 + **钉字面量**（把击杀从巧合变结构） |
| **B6** | 包形状三处未钉：role 前缀 / content 缺省 / priority | P3 / N4a,N4b / N3 | 全是 | 钉完整串 `=="user: 第0轮"`、缺 content 键→`==""`、metadata 整字典相等 |

**B5 等长对照（Important #1，已并入 B5）**：P1a/P1d 的击杀**纯属巧合** ——
断言写 `== builder._count_tokens(...)` 是自指同口径，只因所选串恰好 `tokens ≠ chars`
（`"你是助手"` 5/4、`"自定义信息"` 3/5、`"第7轮"` 4/3）才把 `len()` 干掉。
换一个 token==char 的测试串，`len()` 假实现就在**有断言**的两处也存活。
**修法首选钉字面量**（Task 7 的 `== 13` 先例）。

**Important #2（随批带）**：`_memory_packets` / `_rag_packets` 无 docstring ——
同文件其余 9 个方法全有中文 docstring，唯独这两个最关键、且唯一带 `# noqa: BLE001` 的没有。
顺带把行尾注释收紧为「**工具调用失败**一律降级」（原「检索失败」会被误读为含结果解析失败）。

**定性为等价变异/保险丝（不计入缺口）**：N1b 缓存键加常量盐（仍是确定函数）、
N10 metadata 深拷贝（局部只读 + 包侧新建字典）、N9 `response.data` 直取（真实 `data` 恒 dict）、
P4 `get("hits")` 去兜底（缺键才炸）。

**已裁决不动**：
- **OBS-2 `float()` 强转在 try 外** → **记给 Task 12 的护栏**（spec §6 只承诺 `build()`/`build_result()`
  不外泄，那要 Task 12 才接线；`score: float|None` 本是工具契约）。Task 12 派发词要带这条。
  复审员实测复现：`score='abc'` → `ValueError: could not convert string to float: 'abc'` 穿出 `_gather`。
  建议护栏：`_hits_to_packets` 把转换失败并入「缺分」路径（`raw_score=None`），与现有缺分语义对齐。
- **OBS-3 `_gather` 就地改写 custom 包**（别名共享 `out[0] is c`）→ 记给 **Task 9/10**：
  若选择/组织阶段就地改 `relevance_score`/`metadata`，会污染调用方留存的包对象
  （恰好撞上 B8 语义）。Task 8 的 `_gather` 确实不碰 `relevance_score`（已钉）。
  派发词带一句：「就地改包请拷贝 custom 包，或文档写明『包对象共享』」。
- **OBS-5 `rag_tool.py` 既有 lint 错误** → 维持 **P-1**（Task 12 触碰时加 `# noqa: BLE001`）。
- **OBS-6 中段 import 上提** / **Minor 1 抽 `_tool_packets`**（会合并 G-a/G-b 变异面，**不要抽**）。

### 环境坑（复审员踩到，记下来给后续复审复用）
1. **`tests/test_context_builder.py` 是 CRLF，`builder.py` 是 LF** —— 用 `\n` 找测试文件里的串会 `count=0`。
2. **pytest 单测选择器只传节点 ID**（`tests/test_x.py::test_y`）。同时传文件路径 + 节点 ID 会**并集收集**、跑满全量。
3. **实施者脚本口径偏松**：`mutate_gather.py` 的 `run_pytest` 只看「killer 名是否出现在 tail」算
   `killed_by_killer`，且该变量**不参与 verdict**（verdict 只看 returncode）。结论碰巧全对，
   但后续脚本应按**失败测试 ID 集合**核对。

**已转回实施者修复（B1–B6 全是加测试，实现零改动 + 2 条 docstring）。修完派修复面复审。**

**修复轮已回报（提交 `f826035` `test: pin tool payload wiring, zero score, empty window, cache isolation, token counts`）**：
**实现逻辑零改动** —— 新测试首轮即 68 passed 绿，与复审员「实现是对的、6 条全是测试缺口」吻合。
`builder.py` 仅 4 行（2 行 docstring + 2 行行尾注释措辞）；`test_context_builder.py` 改 6 既有 + 新增 3。
**68 passed**（65+3）；5 文件回归 **157 passed**（scoring 24 / cache 7 / budget 10 / experiment 48 / builder 68）。
**变异 12 注入 12 KILLED**，每条实测失败用例与声称击杀者逐条吻合（B5 整体 `len()` 假实现被 5 条连带击杀）。

**B5 等长对照选了 ①（钉字面量）+ 外加 `!= len(content)` 不变量**，落地到**全部 4 个** `token_count` 赋值点。
实测字面量（cl100k_base）：`"你是助手"` 5/4、`"用户喜欢爬山"` 8/6、`"user: 第0轮"` 6/9、`"自定义信息"` 3/5。
断言三元组形态：`== _count_tokens(串)`（自指一致性）+ `== N`（字面量）+ `!= len(content)`（等长对照不变量）。

### 修复轮偏离裁决（**全部接受，不回退**）

| # | 偏离 | 裁决 |
|---|---|---|
| 1 | B5 落地到全部 4 处（不止 hit+history） | **接受**。派发词原话「**全套**无等长对照对」，且举例 `== 5` / `== 3` 正是 system/custom 两处实测值 —— 选项 ① 本就指向这 4 处 |
| 2 | 外加 `assert token_count != len(content)` | **接受（好加固）**。把「tokens≠chars」这个**击杀前提本身**钉成不变量，防的是我原方案防不住的路（换成 token==char 的串 + 重新量成同一个数 → `len()` 复活）。换等长串会在此直接变红。是「等长对照」四字的可执行编码 |
| 3 | B4 扩写既有用例、不新建不改名 | **接受**。保「review → fix」审计痕迹 |
| 4 | 三条新用例按主题插入而非追加末尾 | **接受**。永久回归钉，与同类相邻更可读 |
| 5 | `builder.py` 改 4 行而非 2 行（含 2 行行尾注释措辞） | **不是超范围** —— 行尾注释收紧是我在派发词里明写的 Important #2 附带项。**不用回退** |
| 6 | 未动任何已裁决项（等价变异/保险丝/Task 12 护栏/别名共享/不抽公共方法/中段 import/CRLF） | **接受**。逐条遵命 |
| 7 | TDD 的「红」用变异实证而非先写失败测试 | **接受**。零实现改动下字面「先红后绿」不可能；12/12 KILLED 就是逐条验红。实施者主动问「要另一种红请指明」—— 不用，当前口径对 |
| 8 | 全仓 lint 13+2 未动 | **接受**（D8：门槛是触碰文件零告警） |

**重要旁证**：实施者用变异脚本的 `count==1` 断言做 needle 消歧 —— B5a/B5b 的
`token_count=self._count_tokens(content),` 在文件中出现两次（hits / history），故用前缀
`timestamp=_parse_timestamp(hit.get("created_at")),` / `timestamp=now,` 消歧。
**独立重跑时若 needle 漂移会直接 FATAL 而不是跑出假绿** —— 这是好设计，复审员会用到。

**修复面复审已派发**（范围只针对 B1–B6 + docstring 改动面，不重跑上轮 21 条全表）。
重点请验：四个字面量独立复量、`!= len(content)` 的强度（有无反例）、4 处全落地的范围判定、
实现改动是否只有 4 行。

**修复面复审结论（`f826035`）：Ready to merge: Yes —— Task 8 关闭。**
自写独立脚本 `mutate_fixround_indep.py`（锚点与失败集采集独立于实施者脚本）：
- **12/12 KILLED，无 SURVIVE、无连带误伤**（每条失败集都是单点；B5 恰为声称的 5 条连带）
- **四个字面量独立复量零偏差**：5/4、8/6、6/9、3/5，且四串全部 `tokens ≠ chars`
- **`!= len(content)` 判定达成设计意图，无反例**：
  - 方向 A（换 token==char 的串）→ 该行必红 ✅
  - 方向 B（tokens≠chars 但 count==len）→ **矛盾式，空集**（`len(enc.encode(s)) ≠ len(s)) ∧ (== len(s))`）
  - **真正承重场景**（换串 + `_count_tokens` 假成 `len()` + 按假实现重测字面量）→
    `== 字面量` 绿、自指式绿，**只剩 `!= len` 承重** → 红，击杀 ✅
  - 残余边界备案：单看是弱断言，强度来自与 `== <字面量>` **配对**；4 处全配对到位
- **4 处全落地 = 真覆盖不是装饰**：复审员**加验** B5c/B5d（system 点 / custom 填充点单点变异）
  均 **KILLED**（单点失败集）→ 4 处全部单点可击杀
- **实现恰好 4 行、零逻辑改动、无第 5 行**（numstat `4 insertions, 2 deletions`）；
  两条 `# noqa: BLE001` 仍在（count=2），ruff 清零旁证无 RUF100
- sha256 逐字节与自报一致：`builder.py` 10982 / `f8a91068…`、测试 32531 / `f2a18b25…`
- **残留扫描 4 处「命中」定性为良性**：`占位` 等变异构造串只出现在**新用例 docstring** 里
  （击杀意图文档），`builder.py` 侧全部 0

备案 2 条（不判 No）：① B2 docstring 的 score 取值清单略欠精确（漏了 `score=0.5`，
但 0.5 是 truthy、变异下不受影响，实质判断正确）② `!= len` 的使用约定：改夹具串必须
同时复量字面量并保留该行。

---

# 🔴🔴 任务暂停（2026-09-23，用户指令）

**用户指令原文**：「本次task执行结束后暂停」

**已执行**：Task 8 走完（实施者 → 两阶段评审 → 修复轮 → 修复面复审 **Yes**）→ **已暂停**。
**未派 Task 9**。之前的「继续推进task」授权**到此为止**，等用户指示。

## 暂停时的全线状态

| Task | 内容 | 状态 | 提交 | 测试 |
|---|---|---|---|---|
| 1 | TTLCache | ✅ | `268098c` + `3ae007c` | 7 |
| 2 | 动态 token 预算 | ✅ | `76827f8` + `dcc5e4e` | 10 |
| 3 | 前置导出补齐 | ✅ | `7d2ca1f` | — |
| 4 | 核心数据类型 | ✅ | `c30d2b9` + `0cc2d5e` + `3771327` | 21 |
| 5 | 相关性打分器 | ✅ | `8f93d8d` + `32227c5` | 24 |
| 6 | A/B 实验 | ✅ | `d8e6597` + `399c998` + `1950fe2` | 48 |
| 7 | ContextBuilder 骨架 | ✅ | `b548ac7` + `6feeef6` | 42→并入 |
| 8 | **Gather 汇集** | ✅ **本次** | `27ed0a0` + `f826035` | **68** |
| 9 | Select 选择 | ⏸ 未派 | — | — |
| 10 | Structure/Compress | ⏸ | — | — |
| 11 | 全链路 build_result | ⏸ | — | — |
| 12 | RAG score 修复（B15） | ⏸ | — | — |
| 13 | context/__init__ 导出 | ⏸ | — | — |
| 14 | examples 离线示例 | ⏸ | — | — |
| 15 | README | ⏸ | — | — |
| 16 | 收尾验证 + 规格同步 | ⏸ | — | — |

**5 文件回归 157 passed**（scoring 24 / cache 7 / budget 10 / experiment 48 / builder 68）。
分支 `feat/context-management`，工作区干净（仅 `?? .claude/`，并行会话的 local settings，未动）。

## 暂停汇报必带的三件事（用户尚未被告知）

### 1. 🔴 并发会话碰撞（自 segment 2 起持续）
另一个 Claude 会话在同一仓库、同一分支 `feat/context-management` 上做
`structured note tool`。证据：提交 `8230825` / `97e542d` / `9d907bd` / `1fa7f59` /
`3d55625` 与我方提交交错。**无数据丢失**。已采取的对策：**所有提交只用显式文件路径**，
禁 `git add docs/` / `-A` / `.`。**决定不重写历史**（会砸掉对方的工作）。
本次又见对方留下的 `.claude/settings.local.json` + `.claude/skills/handoff/SKILL.md`
（未追踪，我没动）。

### 2. 🔴 Task 16 Step 0 的计划外扩权
计划给 Task 16 排了 Step 0：修 `hello_agents/memory/embedding.py:170` 的
`create_embedding` 一字 bug —— 把 `if not cfg.dashscope_api_key and backend == "auto":`
里的 `and backend == "auto"` 删掉。
**这超出了「仅 ContextBuilder，不改动 agents/」的范围约束**（虽然不动 `agents/`，
但动了 `memory/`）。必须作为**单独的「计划外的一行修复」**向用户披露并征得同意，
不能夹带进 Task 16。

### 3. D8 全仓 lint 基线（计划外历史遗留，非本次引入）
`uv run ruff check hello_agents tests` → **13 errors**（12×BLE001 + 1×F401）：
- BLE001：`agents/react_agent.py:113`、`agents/simple_agent.py:183`、`core/llm.py:72/109/251`、
  `memory/storage/neo4j_store.py:88`、`tools/builtin/calculator.py:62`、
  `tools/builtin/memory_tool.py:55`、`tools/builtin/rag_tool.py:51`、
  `tools/builtin/search.py:131/142`、`tools/chain.py:66`
- F401：`tools/builtin/search.py:56`（`from serpapi import Client`）
- `ruff format --check` → 2 files would be reformatted：`core/exceptions.py`、`core/llm.py`

**D8 裁决**：验收门槛是「**触碰文件零告警**」，不是全仓清零。
其中 `rag_tool.py:51` 已由 **P-1** 排进 Task 12（触碰时加 `# noqa: BLE001`）；
`memory_tool.py:55` 同型但本计划永不触碰，**不动**。

---

## Task 9 预检（H 系列）—— Select 用例块

对象：计划 L2221–2491 的测试块（8 个用例）+ Step 3 实现块。
**与 G-e 强耦合**：Task 9 的 `_recency_of` 直接消费 Task 8 写入的 `metadata["position"]`。

### 必修（全部实测确认）

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **H-a** | `test_select_always_keeps_system_instructions` **钉不住**「system 不受相关性阈值淘汰」 | 用例给 system 包 `relevance_score=1.0`、`min_relevance=0.99`。1.0 ≥ 0.99 —— 即便**完全删掉** system/other 拆分，它照样通过阈值过滤、照样进 `selected`，断言全绿（实测 `1.0 >= 0.99 = True`） | 把 system 包的 `relevance_score` 改成 **0.0**（低于阈值）。有拆分 → 恒保留；无拆分 → 被淘汰 → `selected == []` → KILLED |
| **H-b** | `test_select_skips_scoring_when_system_instructions_exceed_budget` **钉不住**早退分支 | 用例传 `available_tokens=0`。就算不早退，`0 + 1 <= 0` 也是 False，other 必然落选，`selected==[system]` / `dropped_by_budget==1` 照样成立（实测确认）。而且用例没用 spy scorer，「跳过打分」根本不可观测 | `available_tokens` 改成 **100**（无早退时 other 会入选）+ 接 `_SpyScorer` 断言 `scorer.scored == []` |
| **H-c** | 衰减**速率**钉不住 | 实测 rate ∈ {0.1, 0.001, 0.5} 三种取值下，`fresh > stale` 与 `0.1 <= stale <= 1.0` **全部成立**。规格 L410 明文 `clamp(0.1, 1.0, exp(-0.1 × age_hours / 24))`，但没有任何断言钉住 `-0.1` 这个系数 | 补精确值锚点：age=24h 时 `== pytest.approx(math.exp(-0.1))`（= 0.904837…）；age=0 时 `== approx(1.0)` |
| **H-d** | naive 时间戳归一分支**零覆盖**，而它正是在线路径 | 实测 `datetime.now(tz=UTC) - naive_ts` 抛 `TypeError: can't subtract offset-naive and offset-aware datetimes`。Task 7 的 `_parse_timestamp` 对无 tz 的 ISO 串**原样返回 naive**（F3 实测），进 `_calculate_recency` 就炸。用例 `test_recency_handles_timezone_aware_timestamp` **只传 aware**，删掉 `if timestamp.tzinfo is None` 整条分支照样全绿 | 补用例：传 naive `datetime(...)` 与等值 aware `datetime(..., tzinfo=UTC)`，断言两者 recency **相等**。并与 F3b 一并决定归一位置（见下） |
| **H-e** | **加权和公式零覆盖** —— `relevance_weight * relevance + recency_weight * recency` | 实测存在排序交叉：A(rel=1.0, rec=0.1) vs B(rel=0.5, rec=0.5)，w=0.5/0.5 时加权和 A=0.550 > B=0.500，乘积 A=0.100 < B=0.250。但现有 8 个用例**没有一个**同时让两个权重非零且两个分量有差异——`_select_config` 恒配 `relevance_weight=1.0, recency_weight=0.0`，唯一的例外 `test_select_ranks_history_by_position` 是 `0.0/1.0` 且 relevance 全是 1.0（乘积 `1.0*rec = rec` 与加权和同值）。改写成 `rel * rec` 或 `max(rel, rec)` 全部存活 | 补一条交叉用例：`relevance_weight=0.5, recency_weight=0.5`，两包分别取上表的 (1.0, 0.1) 与 (0.5, 0.5)，断言顺序 `["A", "B"]`（加权和）而非 `["B", "A"]`（乘积） |
| **H-f** | history 新近性映射**端点钉不住** | `span = max(history_count - 1, 1)` → position=n-1 时 recency=1.0；若误写 `span = history_count` → 0.75。现有用例只断言顺序「新 > 旧」，两种 span 都满足（实测 1.000 vs 0.750） | 在 `test_select_ranks_history_by_position` 补 `assert builder._recency_of(newer, 2) == pytest.approx(1.0)` 与 `assert builder._recency_of(older, 2) == pytest.approx(0.5)` |
| **H-g** | `_select` 对 `score_many` **无异常防护**，会把 Task 5 的 `RuntimeError` 直接穿透出 `build()` | Task 5 的 C7 修法是「向量数不足抛 RuntimeError」。规格 §6 L470：`build()`/`build_result()` 除配置错误外**不向调用方抛异常**。现在 `_select` 里 `self.relevance_scorer.score_many(...)` 裸调，抛了就出去 | `_select` 包 `try/except Exception`，`logger.warning` 后该批包按 `relevance_score = 0.0` 处理（或整体跳过打分），并计入 `BuildStats`。补用例：`_RaisingScorer` → 断言 `_select` 不抛、返回值可用。**这是 Task 5 评审员提的同一隐患，两处合并处理** |

### 与 F3b 的合并决策（必须在派发 Task 9 前定）

Task 7 的 F3 实测过 `datetime.fromisoformat("2026-09-23T10:00:00")` 返回 **naive**，而 spec L398 写的是「经 `datetime.fromisoformat` 还原」、L410 写「`age_hours` 为信息距构建时刻的小时数」，**没写归一位置**。Task 9 的实现块把归一放在 `_calculate_recency`（`if timestamp.tzinfo is None: replace(tzinfo=UTC)`）。

两个方案：
- **(i) 解析处归一**（改 `_parse_timestamp`）：符合「一处归一」原则，下游不再防御。但 `_parse_timestamp` 是 Task 7 的产物，Task 9 要回改 Task 7 的代码 + 补 Task 7 的用例。
- **(ii) 用前归一**（保 `_calculate_recency` 的防御，F3b 保持现状）：不动 Task 7，但 naive 值会在中间态存留，任何新的下游消费者都会踩同一个坑。

**建议 (i)**，并在 Task 9 派发词里带上「回改 `_parse_timestamp` + 补归一断言」。H-d 的等值用例两条路径都要写。

### 建议（不阻塞）

| ID | 问题 | 修法 |
|----|------|------|
| H-h | `relevance = packet.relevance_score or 0.0`（L2456）—— 用 falsy 判断而非 `is not None`。0.0 时结果碰巧相同，但是代码坏味道 | 改 `0.0 if packet.relevance_score is None else packet.relevance_score` |
| H-i | `zip(unscored, scores)` 若 scorer 返回短列表会**静默截断**（与 C7 同型）。Task 5 已在 scorer 内层抛错，这里是第二道防线 | 与 H-g 的 try/except 一并做：长度不等就 `raise`/降级 |
| H-j | 无用例钉 `score_many` 只对 `relevance_score is None` 的包**批量**调用（现在是逐个进 `unscored` 列表再一次 `score_many`，SpyScorer 的 `scored` 可验） | `test_select_does_not_rescore_explicit_half_score` 已间接钉住（`scored == ["unscored"]`），够用 |
| H-k | `_calculate_recency` 里 `max(0.0, ...)` 的下限与外层 `clamp(0.1, 1.0, ...)` 的 0.1 下限并存，未来时间戳（age<0）会先被 `max(0.0)` 置 0 再得 1.0 | 可接受；补一条「未来时间戳 → 1.0」用例即可 |

### 派发 Task 9 时必带
H-a / H-b / H-c / H-d / H-e / H-f / H-g 七条必修 + 变异对照表 + F3b 归一位置决策。
**H-e 要把交叉数值表贴进派发词**（A(1.0, 0.1) / B(0.5, 0.5) / w=0.5·0.5 → 加权和 A>B，乘积 B>A），否则实施者容易随手取两个同向样本，写出来仍然钉不住。

---

## Task 10 预检（I 系列）—— Structure / Compress 用例块

对象：计划 L2495–2817 的测试块（12 个用例）+ Step 3 实现块。

### 🔴 硬阻塞

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **I-1** | **计划自相矛盾**：`test_structure_keeps_template_order` 按计划原文写出来**直接红** | 用例输入 `[_packet("记忆","memory"), _packet("知识","rag")]` **没有** system_instruction 包。计划的 `_structure` 是 `if policies: sections.append(Role & Policies)`（L2697）——`policies` 为空则不加该段。实测实际产出 `['Task','Evidence','Context','Output']`，用例期望 `['Role & Policies','Task','Evidence','Context','Output']` → **不相等**。旁证：`test_structure_always_includes_task_and_output` 明确断言 `assert "Role & Policies" not in by_title`（空输入时），说明「有条件添加」是本意。这不是覆盖缺口，是**计划测试块与计划实现块互相打脸**，照抄必然卡在 Step 4 | **推荐**：输入改成乱序三包 `[_packet("记忆","memory"), _packet("知识","rag"), _packet("你是助手","system_instruction")]`，期望保持 5 段 —— 既修好矛盾，又顺带把「不许按输入顺序吐出」钉死（现输入 `[memory→Context, rag→Evidence]` 只能验出 Evidence/Context 两点序）。备选：期望列表删掉 `Role & Policies`（较弱） |

### 必修

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **I-2** | `test_truncate_text_is_exact_in_tokens` 名为 exact，断言却是 `<= 10` —— **偷懒实现可活** | 实测：只返回 1 个 token 的实现 `count=1 <= 10` → **被放行**（SURVIVED）。另外实测 `encode 截 10 → decode → 再 encode = 10`，**本串无 BPE 边界回涨**，所以 `== 10` 可安全断言 | 三处改：① `assert builder._count_tokens(truncated) == 10`（本串已实测安全）② `_truncate_text("短文本", 100) == "短文本"` 保留 ③ `_truncate_text("任意", 0) == ""` 保留。另补 `len(encoder.encode(truncated)) == 10` 的同义断言无必要，留 `==` 即可 |
| **I-3** | `test_compress_never_truncates_task_or_output` **只看标题不看 body** | 用例断言 `[section.title for section in kept] == ["Task","Output"]`。变异「把 Task 也截断」→ 标题不变 → **SURVIVED**。而 `Task` body 是 `"任务"*300`（600 字符），截不截一眼可辨 | 补 `assert kept[0].body == "任务" * 300` 与 `assert kept[1].body == "回答"`，并断言 body **不含** `内容已压缩` 标记 |
| **I-4** | `_compress` 的 `break` 分支**零覆盖**（首个放不下的弹性段截断后终止，后续弹性段跳过） | 现有两个弹性用例（`…truncates_oversized_elastic_section` / `…drops_elastic_section_when_no_room`）**弹性段都只有 Context**，没有 Evidence+Context 同时存在且 Evidence 先放不下的组合。删掉 L2803 的 `break`，现有用例全绿 | 补用例：Evidence 大 + Context 小，预算只够 Evidence 截断 → 断言 `Context` **不在** kept 标题里（被 break 跳过），且 Evidence 带压缩标记 |
| **I-5** | `test_compress_drops_elastic_section_when_no_room` 未断言 `compressed` 标志 | 只断言 titles。若 `_compress` 误返回 `sections, False`（自认为没超限），titles 恰好也对（因为 Context 本来就要丢）……不，等等：若没走压缩路径会 `return sections, False` 且 kept==原三段 → titles 有 Context → 会红。但仍应显式钉 `compressed is True`，否则「丢段但没记压缩」这类变异存活 | 补 `assert compressed is True` |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|----|------|------|
| I-6 | `_order()` 无直接用例，只经 `_compress` 间接覆盖。丢段/重排类变异能被 titles 断言抓到，但 `_order` 本身若改成 `list(sections.values())`（按 dict 插入序）在 kept 只有 Task/Output 时存活 | 补一条直接用例：传乱序 dict，断言返回按 `_TEMPLATE_ORDER` |
| I-7 | `_structure` 的多段拼接分隔符未钉：policies 用 `"\n".join`、evidence 用 `"\n---\n".join`、context 用 `"\n".join` | 补一条多命中用例，断言 body 精确等于拼接结果 |
| I-8 | `_SEPARATOR_MARGIN = 4` 的余量未被任何断言触及；`_truncate_section` 的 `budget <= 50` 整段丢弃阈值只被 `…drops_elastic_section_when_no_room` 间接钉住（max_tokens=50 恰好卡在阈值上） | 阈值改 40 后该用例仍绿（remaining≈36 仍 ≤40？需实测）。建议把 `max_tokens` 调到 30 让 remaining 明确低于阈值，或直接断言「budget=50 时返回 None」的单元行为 |
| I-9 | `test_compress_skips_when_under_budget` 的 `kept == sections` 用 dataclass 列表相等，很好；但没断言「未调用截断」这一副作用 | 可接受（`kept == sections` 已足够强） |

### 派发 Task 10 时必带
**I-1 必须写进派发词的最前面**（计划原文照抄必红，实施者会浪费一轮），并给出推荐修法的完整输入/期望。
I-2 / I-3 / I-4 / I-5 四条必修 + 变异对照表。
**I-3 要点明「标题断言对『恒不截断』这一契约是零信息量的」**，否则实施者容易照抄计划原文。

---

## Task 11 预检（J 系列）—— build_result / build 用例块

对象：计划 L2821–3094 的测试块（11 个用例）+ Step 3 实现块。这是四阶段的收口，统计字段几乎全部在这里首次被消费。

### 必修

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **J-a** | `test_build_result_records_experiment_variant` 的 `variant in {"control","variant_a"}` **近乎同义反复**，分流本身钉不住 | 实测：`apply()` 恒返回 `'control'`、恒返回 `'variant_a'`、或只看 dict 插入序，**三种实现全部满足**该断言。实验分流是实践 5 的核心，现在零覆盖 | 改成：① 同一 `session_id` 连调两次断言 `variant` 相等（稳定性）② ≥32 个 `session_id` 的分流向量里两种变体**都出现**（非退化）。可再补一条与 Task 6 的 `assign()` 结果一致 |
| **J-b** | `apply()` 的**覆盖是否真的生效**零断言 | 用例只看 `stats.variant`。若 `apply()` 返回原 config 但把 variant 名写上，断言照样过 —— A/B 实验变成纯装饰 | 补断言：分流后 `config.relevance_weight` / `recency_weight` **等于该变体的覆盖值**（如 `variant_a` → 0.5/0.5，而非 control 的 0.7/0.3）。可用 spy 包装 `apply` 或直接断言 `stats` 之外的行为差异 |
| **J-c** | `duration_ms >= 0.0` **零信息量** | 实测 `0.0 >= 0.0` 为 True → 硬编码 `duration_ms=0.0` **SURVIVED** | 改 `assert stats.duration_ms > 0.0`（真跑过活的耗时必 > 0）；更稳的是 monkeypatch `perf_counter` 给固定差值后断言 `== 差值*1000` |
| **J-d** | `compression_ratio` 在整个 Task 11 **零断言** | 实测把 0.8 取倒数得 1.25，现有断言集对此完全无感知。Task 4 的 `to_dict` 测试钉的是**舍入**，不是语义方向 | 补：未压缩时 `compression_ratio == 1.0`；压缩后 `compression_ratio == pytest.approx(final_tokens / structured_tokens)` 且 `<= 1.0` |
| **J-e** | 无 `build()` 与 `build_result().context` 的**等价断言** | 计划的 `build()` 是委托，但若有人改写成独立实现（或忘了传 `session_id`），分歧钉不住。`test_build_returns_string_with_task_section` 只看 `isinstance(str)` + 含 `[Task]` | 补：同一 builder 同一入参，`build(...) == build_result(...).context`；且 `build(..., session_id="s")` 与 `build_result(..., session_id="s")` 一致 |
| **J-f** | 缓存计数**只钉单侧**（`second.stats.cache_hits > 0`），首次 build 的计数零断言 | 若 `_cache_counters()` 返回非零常量，`after - before = 0` 会让本用例红 —— 这点是好的。但「首次 build 应记 1 次 miss、0 次 hit」未钉，计数器把 miss 记成 hit、或把一次 put 记成 miss+hit，都存活 | 补 `first.stats.cache_misses == 1` 且 `first.stats.cache_hits == 0`；`second` 侧改 `cache_hits == 1` 且 `cache_misses == 0`（精确值而非 `> 0`） |
| **J-g** | `token_utilization <= 1.0` 是个**潜伏会被顶破的约束** | `final_tokens` 含恒不截断的 Task/Output（I-3），一旦这两段本身就超 `scaled_max_tokens`，`_compress` 无力回天，utilization 会 > 1.0。短查询下不触发，所以现在是绿的 | 要么把断言改成 `0.0 < token_utilization`（去掉上界，上界由压缩契约保证），要么补一条「超大 Task 段 + 小预算」用例钉住边界行为（配合 I-3 的 body 完整性断言） |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|----|------|------|
| J-h | `next(iter(config.experiment.variants))` 在 **空 variants** 时抛 `StopIteration`（裸在 `build_result` 里，不是 `RuntimeError`）。依赖 Task 6 的 E8 校验兜底 | Task 6 派发词里点明「`variants` 非空」是硬校验 |
| J-i | `test_tool_failure_does_not_break_build` 只测 `memory_tool` 抛异常；`rag_tool` 抛异常在 Task 8 的 G-b 覆盖，这里是收口层，孪生用例可省 | 可省 |
| J-j | `_count_by_source` 遇未知 `type` 多出第 6 个键（F4，Task 7 预检已记）。Task 11 的用例都用合法 type，收口层仍钉不住 | 归到 F4 的修法（在 Task 7 或 Task 11 一并修） |
| J-k | `import logging` / `from ...experiment import ExperimentSpec` 放在文件中段。已实测 **E402 不在有效规则集**，不卡门槛 | 不改；但 isort 若被启用会报 I001，实施者自行留意 |
| J-l | `candidates_total > 0` 较弱；与 `candidates_by_source` 的精确值并用可接受 | 可接受 |

### 派发 Task 11 时必带
J-a / J-b / J-c / J-d / J-e / J-f / J-g 七条必修 + 变异对照表。
**J-a / J-b 要写明「A/B 实验的产出物是 metrics，若不验证覆盖生效，实验就是纯装饰」** —— 这是用户 5 条实践里的第 5 条。

---

## Task 12 预检（K 系列）—— RAGTool 丢 score（B15）

对象：计划 L3098–3153（1 条新用例 + 1 行修复）。实测确认 B15 属实，但**计划的用例按原文写出来直接红**。

### 🔴 硬阻塞

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **K-1** | 计划用例的 ingest 调用**写错了**，会因检索不到任何 chunk 而红，跟 score 无关 | 计划写 `tool.run({"action": "ingest", "content": "向量库使用 Qdrant 存储", "source": "demo"})`。`rag_tool.py` 的分派是 `if source: ingest_file(source) / elif content: ingest_text(content)` —— **source 优先**。传 `"demo"` 被当成**文件路径**走 `ingest_file`，文件不存在 → 实测 `ingest -> ToolStatus.ERROR`、`query chunks=0`。于是 `assert resp.data["chunks"]` 直接红。三组对照实测：<br>A（计划原文 content+source 同传）→ ERROR，chunks=0<br>B（只传 content）→ SUCCESS，chunks=1，score=0.695<br>C（只传 content + 既有语料）→ SUCCESS，chunks=1，score=0.627 | 删掉 `"source"`，只传 `content`。语料用纯中文串即可（实测 B 的「向量库使用 Qdrant 存储」/「向量库用什么存储」是通的） |
| **K-1b** | 计划写的 `RAGTool(manager=manager)` **构造参数名也是错的** | 实测 `RAGTool.__init__(self, pipeline=None, llm=None)` —— 是 `pipeline` 不是 `manager`。`tests/test_rag_tool.py` 既有夹具是 `tool(manager)` → `RAGPipeline(memory_manager=manager, chunk_size=200, chunk_overlap=20)` → `RAGTool(pipeline=pipeline)` | 新用例**直接用既有的 `tool` 夹具**，不要自己构造 |

### 必修

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **K-2** | `all("score" in chunk …)` 只钉**键存在**，不钉值 | 变异 `chunk.to_dict() \| {"score": 0.0}`（或 `{"score": None}`）→ 键仍在 → **SURVIVED**。而 `score` 本来是有真值的：实测 `MemoryItem.score = 0.6267…` / `0.6950…` | 加值断言。**最强钉法**：再调一次 `tool.pipeline.query(question)`，断言 `resp.data["chunks"][i]["score"] == pytest.approx(result.chunks[i].score)` —— 精确到来源同一。退而求其次：`isinstance(chunk["score"], float)` 且 `chunk["score"] > 0.0` |
| **K-3** | `\|` 的左右顺序决定谁覆盖谁，现有断言钉不住 | `to_dict() \| {"score": …}` 让 score 赢；`{"score": …} \| to_dict()` 让 to_dict 赢。当前 `to_dict()` 的键是 `['content','created_at','expires_at','id','memory_type','metadata']`（**实测无 score**），两种写法今日等价 → 顺序错误会存活 | 保持计划的 `to_dict() \| {"score": chunk.score}`（score 赢，未来 `to_dict()` 若自带 score 也能被检索分覆盖）；K-2 的值断言顺带钉住顺序 |

### 实测确认的好消息（B15 属实、修法有效）
- `RAGResult.chunks` 是 `list[MemoryItem]`，查询路径**确实**填充了 `MemoryItem.score`（实测 0.6267…）
- `MemoryItem.to_dict()` 的 6 个键里**确实没有** `score`，与 `MemoryTool.recall` 的 `item.to_dict() \| {"score": item.score}` 不对称
- 一行修法 `chunk.to_dict() | {"score": chunk.score}` 就能对齐
- 既有 8 个 `tests/test_rag_tool.py` 用例全绿，改动面极小

### 建议
| ID | 问题 | 修法 |
|----|------|------|
| K-4 | 只测了「有 score」，没测「score 参与下游」 | 不在本任务范围（G-c / Task 8 覆盖取回后的消费）。可省 |

### 派发 Task 12 时必带
**K-1 / K-1b 必须放在派发词最前面**（照抄计划原文必红，且红的原因与要修的 bug 无关，会误导排查）。
给出实测的三组对照表 + 正确的 `tool` 夹具用法。K-2 必修 + 变异对照表。
**另加 P-1**（Task 16 预检转交）：同一提交里给 `rag_tool.py:51` 的 `except Exception` 补
`# noqa: BLE001` —— 否则 Task 16 Step 2「触碰文件零告警」门槛自相矛盾（该文件有既有 BLE001，
行尾注释「解析/检索异常不应击穿 Agent 循环」是正当理由，与 `scoring.py:152` 同款做法）。
**不要**改捕获语义。

---

## Task 13 预检（L 系列）—— 公开导出

对象：计划 L3157–3277（2 条用例 + `__init__.py` 重写）。

### 必修

| ID | 问题 | 实测证据 | 修法 |
|----|------|----------|------|
| **L-a** | `test_public_api_is_importable_from_package_root` 只查「期望名都在 `__all__`」，**不查 `__all__` 里没有多余名** | 实测：把 `__all__` 加一个 `"InternalHelper"` 后，for-循环断言**仍全绿**。内部符号一旦被误导出就钉不住 | 补 `assert set(context.__all__) == {16 个期望名的集合}`（或 `assert context.__all__ == sorted([...])` 一次钉死内容+顺序） |
| **L-b** | for-循环里首个失败即中断，后续名字的缺失**不会被报告** | pytest 只报第一个 | 改 `@pytest.mark.parametrize("name", [...])`，每个名字独立用例。顺带 L-a 的集合断言只留一条 |

### 建议
| ID | 问题 | 修法 |
|----|------|------|
| L-c | `test_public_api_all_is_sorted` 很好（`__all__ == sorted(__all__)`），已实测 16 名列表确实是 ASCII 序（`TTLCache` 在 `create_relevance_scorer` 前，大写在小写前） | 保留 |
| L-d | 未测 docstring 里的「典型用法」示例能跑通 | 可补一条 `from hello_agents.context import ContextBuilder, ContextConfig` + 构造成功的用例；Task 14 的示例脚本会覆盖，可省 |
| L-e | `__init__.py` 的 docstring 写「配置 dashscope 等后端后自动升级为向量相关性打分」—— 这是 `create_relevance_scorer("auto")` 的行为，不是 `ContextBuilder` 的默认行为。默认打分器是 keyword | 收尾时核对 docstring 别夸大（D6 同类记账） |

### 派发 Task 13 时必带
L-a（集合等价）+ L-b（parametrize）+ 变异对照表。
**L-a 要点明「Task 3 评审当初就是从 `__all__` 成员资格缺口提出来的，这里是同一缺口的收口」**。

---

## Task 6 — 轻量 A/B（experiment.py）

**提交**：`d8e6597` `feat: add lightweight A/B experiment assigner with config validation`
（`experiment.py` +134 / `base.py` +6 / `test_context_experiment.py` +240，仅 3 文件）

**状态**：实施 DONE，**第 1 阶段（规格符合性）评审已派发**，第 2 阶段待第 1 阶段通过后派。

**E1–E9 处置（实施者自报）**：
| ID | 处置 | 落地形态 |
|---|---|---|
| E1 | 已补 | `test_spec_rejects_empty_name`，`match="name 不能为空"` |
| E2 | 已补（一处被 E8 取代） | `{0.0, 0.0}` @assign 抛「正数」钉 `total <= 0`；另补 `{0.0, 1.0}` 合法正例 |
| E3 | 已修 | 32-unit 向量级断言 `seq_a != seq_b` 且差异位数 `>= 5`（实测 20/32）；公式用例再钉 `u3` 双 seed 分叉 |
| E4 | 已补 | `test_config_accepts_valid_experiment_spec`，断言 `config.experiment is spec` |
| E5 | 已补（选方案 ①） | 构造后就地塞非法字段 → `apply` 抛 `ConfigError`，`match="字段覆盖非法"`；`except TypeError` 保留 |
| E6 | 已锚定 | 带宽保留 + 基线记入注释（n=2000→0.7570、n=5000→0.7554）+ 公式精确比对用例 |
| E7 | 已补 | `weights={"control": 0.0}` 单变体钉早退；原用例保留作契约文档 |
| E8 | 已补 | 负权重 `match="不能为负"`、多余键 `match="多余变体"` |
| E9 | 已落实 | 全部共享 `ConfigError` 的校验一律 `match=`，模式避开相邻消息公共子串 |

**E2 清单判断被 E8 取代（实施者有实测，待评审确认）**：`{-1.0, 1.0}` 现在**构造期**即拒
（`weights 不能为负`），到不了 `assign()` 的 `total <= 0`。该分支改由「非负且全零」的
`{0.0, 0.0}` 钉住 —— 这是 E8 负权重禁令下唯一能抵达该分支的输入形态。实质诉求（零覆盖
分支被钉死）已满足。

**实施者自报变异对照表 13/13 KILLED**（E1/E2/E3/E4/E5/E6/E7/E8a/E8b/E9a/端序/插入序/和校验）。
SURVIVED 清单空。**待第 2 阶段独立复核。**

**第 1 阶段结论（`d8e6597`）：APPROVED** —— E1–E9 全部满足实质诉求（E2 的 `{-1.0, 1.0}`
被 E8 取代判为**合理**，实测确认该输入构造期即拒）；公式精确比对用例**独立复算 8/8 一致**
（10 个 bucket 值散布 0.02–0.96，seed/归一化/插入序/bucket 公式四件事一次钉住）；
12 字段白名单与 Spec §4.4 **完全一致**（frozenset len==12）；稳定分流公式逐项吻合；
六个偏离全部「合理」；`base.py` +6 行只做 isinstance 校验且无模块级循环导入。
无 Critical / Important。

第 1 阶段留两条给第 2 阶段：
- **Minor**：三处 `match=` 模式偏短，跨模块命中同形消息（`不能为负`↔`memory_limit 不能为负数`、
  `正数`↔`cache_ttl_seconds 必须为正数`、`ExperimentSpec`↔`ExperimentSpec.name 不能为空`）。
  按 E9 字面（相邻消息）已落地；建议收紧为 `weights 不能为负` / `权重之和必须为正数` /
  `必须是 ExperimentSpec`。**由第 2 阶段判定是否必须收紧。**
- **说明项**：`return names[-1]` 兜底不可测（float64 边界），已记录而不测。

**第 2 阶段（代码质量 + 独立变异复核）已派发。**

**第 2 阶段结论（`d8e6597`）：Ready to merge: No** —— 独立变异 **39 条 = 29 KILLED + 10 SURVIVED**。
与自报「13/13」不矛盾，但自报未覆盖的变异里挖出真缺口。

SURVIVED 10 条 = **5 条不可观测保险丝/等价变异（接受）** + **5 条真缺口（必须修）**：
| 判定 | 条目 |
|---|---|
| 接受 | `return names[-1]` 兜底（200k 抽样最大 bucket 0.999987…，打不到 1.0）；`bucket < cumulative` 边界（概率 ~2⁻⁶⁴）；`_normalized_weights` 侧 `sorted`（dict 键序不影响查找）；`base.py` 局部导入改模块级**不炸**（experiment 仅 `TYPE_CHECKING` 导 base） |
| **真缺口** | **M15 插入序未钉住**（夹具键名恰好字母序 == 插入序，`sorted()` 变异全绿）；**M21 白名单成员/排除未钉住**（12 收只测过 3 个、6 拒零反例） |

代码质量 **无 Critical**，2 条 Important：
1. **E5 防御不完整** —— `apply()` 不复验 `_OVERRIDABLE_FIELDS`。实测构造后就地写
   `cache_ttl_seconds` / `budget_policy` / `min_importance` **静默生效**。`except TypeError`
   只堵「不存在的字段名」，堵不住「存在但不可覆盖的字段」。
2. 插入序 + 白名单两个测试缺口（同 M15/M21）。

Minor 4 条：三处 `match=` 偏短（判定**当前零误报**，可不改）、`variants` 值非 dict 时抛
`TypeError` 而非 `ConfigError`、NaN/inf 权重静默偏斜、`base.py:145` 注释措辞不准确。

**已转回实施者修复**：S1 插入序钉用例 / S2 白名单 12 收 + 6 拒 / S3 `apply()` 复验白名单 /
S4 `variants` 值类型 / S5 有限数校验 + 一行注释。
明确**不要做**：`match=` 收紧（可选）、`frozen=True`（范围失控）、兜底与边界类保险丝。

**J-h 事实更正**（评审实测）：空 `variants` **不会**抛 `StopIteration` —— 落到
`_normalized_weights` 的 `total <= 0` 抛 `ConfigError`。全库无 `next(iter(` 消费 `spec.variants`。
J-h 的 StopIteration 描述对当前代码不成立；`if not self.variants` 硬校验仍正确（早失败更好）。

**S1–S5 修复轮已回报（提交 `399c998` `fix: pin insertion order, whitelist membership, and weight finite checks`）**：
- S1 非字母序键用例 `{"zzz": {}, "aaa": {}}`，硬编码期望 `u0→zzz u1→aaa u2→zzz u3→aaa session-42→aaa`；
  变异 `sorted()` → 五点全对调 → **KILLED**
- S2 参数化 12 收 + 6 拒（值取 `ContextConfig()` 默认）；变异删 `min_relevance` / 加 `budget_policy` 各杀一行
- S3 `apply()` 在 `replace` **前**复验 `set(overrides) - _OVERRIDABLE_FIELDS`，`match="超出白名单"`；
  补 3 条注入用例（`cache_ttl_seconds` / `budget_policy` / `min_importance`）
- S4 `isinstance(overrides, dict)` 否则 `ConfigError`，`match="必须是 dict"`
- S5 逐项 `isinstance(int|float) and isfinite` → `含非有限数值`；`isfinite(sum)` → `超出有限范围`
  （求和溢出修在**构造期**，与 NaN/inf 一致，早失败）
- 测试 21 → **48**（含参数化展开）；回归 5 文件 `129 passed`；自报本轮变异 **7/7 KILLED**
- **1 处主动偏离**：E5 用例的 `match=` 由 `字段覆盖非法` 改为 `超出白名单` —— S3 复验在
  `replace` 之前，`not_a_field` 会先被白名单分支拦下。三条消息互不包含：
  `超出白名单`（S3）/ `字段覆盖非法`（TypeError 残余）/ `不可覆盖`（构造期）。
  **取舍优先可区分性**，已请复审判定。
- 另两条自报顾虑转复审判定：`except TypeError` 现为不可观测保险丝；`bool` 权重会通过
  isfinite 检查（`isinstance(True, int|float)` 为真）等价于 `1.0`。
- **复审已派发**（只针对修复面，不重跑 39 条全表）。

**修复复审结论（`399c998`）：Ready to merge: Yes —— Task 6 关闭。**
复审独立验证：
- **S1**：5 个硬编码期望经**纯公式复算全部一致**（非 `assign()` 自比）；`sorted()` 变异 KILLED，5 点全对调
- **S2**：18 字段逐一对账 `allowed(12) ∪ forbidden(6) == all_fields`、无交集、无遗漏、无多余；
  删 `min_relevance` / 删 `log_stats` / 加 `budget_policy` / 加 `cache_ttl_seconds` 四种变异全 KILLED
  （加字段时 S2 反例 + S3 注入用例**双重击杀**）
- **S3**：代码序实测确认复验在 `replace` **之前**；删复验 4 条红；**顺序反转**（挪到 replace 后）也被杀
- **match= 交叉验证**：8×8 子矩阵对角线外全空，`超出白名单` / `字段覆盖非法` / `不可覆盖` 两两互不包含
- **match 偏离取舍：合理**。复审还做了 ALT 探针（S3 消息嵌入「字段覆盖非法」子串）→ SURVIVED 48 全绿
  —— 证明备选方案既无收益又牺牲可区分性，当前三分法是最优解
- **bool 权重：不必须拒**（`True`/`False` ≡ 1.0/0.0 在 A/B 权重语义下数学自洽）
- 无变异残留（`sha256sum -c` / `git diff HEAD` / `git status --porcelain` 全清）

复审的**一处事实性纠正**（Minor，不阻塞）：实施者称 `except TypeError` 是「构造不到触发面的
不可观测保险丝」—— **不准确，残余分支仍可达且可测**：
| 路径 | 结果 |
|---|---|
| post-ctor `variants["control"] = ["max_tokens"]`（list，元素是白名单字段名） | `set()` 通过 → `replace(**list)` TypeError → 残余分支转 `ConfigError("字段覆盖非法")` |
| 白名单字段 + 错值类型 `{"max_tokens": "not-int"}` | `replace` 成功 → `__post_init__` 的 `<=` 比较 TypeError → 同样被残余分支转 `ConfigError` |

第二条是**真实的数据错误形态**（阈值被配成字符串），残余分支把它从裸 TypeError 救成 ConfigError
—— 分支保留是对的。建议（非必须）补 1 条 `max_tokens="not-int"` 用例，让 `字段覆盖非法` 这个
match= 有归属。**记为可选后续，不动。**

**Task 6 最终交付**：`d8e6597`（初版 21 用例）+ `399c998`（评审修复 +27 = 48）。
两阶段：spec APPROVED / quality No→修复→**Yes**。

---

## Task 7 — ContextBuilder 骨架（builder.py）

**提交**：`b548ac7` `feat: add ContextBuilder skeleton with tiktoken counting and budget scaling`
（`builder.py` +150 / `test_context_builder.py` +241，仅 2 文件）

**状态**：实施 DONE（40 passed / 99 回归 / lint 与 format 清零），**第 1 阶段评审已派发**。

**F1–F10 + F3b 处置（实施者自报）**：全部已落地。要点：
- F3b 采用**解析处归一**（`_parse_timestamp` 出口一律 tz-aware UTC），并补 naive↔aware 等值用例
- F4 未知 `type` 归 `custom`，五键恒在
- F5 补 `_FixedPolicy(0.0)→500` / `(0.5)→750` 两端
- F6 补 builder 自己 `cache.put/get` 的**精确值** `(hits, misses) == (1, 1)`
- F8② 追加 `_DictCacheScorer`（`cache` 是 dict）加强用例 —— 单靠 `KeywordOverlapScorer`（无 `cache` 属性）杀不掉 `isinstance → is not None`（见变异 M9）
- F9 加硬编码 `== 13`（cl100k_base 已知值）

**自报变异 22 条，3 条 SURVIVED 均有说明**（M5 高端 / M9 KeywordOverlap / M14 显式优先）：
M5 低端已杀、M9 由加强用例已杀、M14 由 M14b（忽略显式入参）已杀。**待第 2 阶段独立复核。**

**实施者自报偏离 D1–D14**，其中 **D2 是对我方环境事实的修正**：
计划的 `# noqa: BLE001` 是**死注解** —— `except Exception as exc: raise ConfigError(...) from exc`
是转抛式，本环境 **不报** BLE001，加了反被 `RUF100` 判 unused。
**我方已独立实测证实**（见「环境事实」的 BLE001 边界条），D2 判定为合理。

其余偏离：D1 isort 分节、D3–D10 = F 系列必修、D11 F9 加强、D12 naive 构造改
`fromisoformat`（裸构造触发 `DTZ001`）、D13 计数 40（计划写 22，记账漂移）、D14 format 换行。

**第 1 阶段结论（`b548ac7`）：APPROVED** —— F1–F10 + F3b 全部「已落地」（逐条 file:line），
D1–D14 全部「合理」（D2 已被评审独立 ruff 复核证实：转抛式不报 BLE001，加 noqa 反报 RUF100），
Spec 时间戳/五键/预算公式/错误表无违背，交付物齐全，无 Critical / Important。

两条 Minor（**不要求改**，转第 2 阶段判定）：
1. `test_parse_timestamp_fallback_is_tz_aware_now` 的容差 `delta < 5` 秒偏宽
2. F5 公式定点用例未同时断言 `available == scaled - reserved`（已由 B11 用例覆盖）

**第 2 阶段（代码质量 + 独立变异复核）已派发。**

**第 2 阶段结论（`b548ac7`）：Ready to merge: No** —— 独立变异 **35 条 = 30 KILLED + 5 SURVIVED**。
5 条 SURVIVED = 3 条等价变异/保险丝（I7 policy 名回退、C4 getattr 默认 `TTLCache()`、S4 缺失
type 默认改 `""` 被 fold 兜住）+ **2 条真缺口**：

| ID | 缺口 | 修法 |
|---|---|---|
| **I5** | `cache_max_size` / `cache_ttl_seconds` → `TTLCache` 的**接线零覆盖**。改成 `TTLCache()`（吃默认 256/3600.0）40 例全绿 | 一行构造 + 两字段断言 |
| **P7/P8** | 带时区偏移的 ISO 串（`+02:00`）无钉扎。若被无条件 `replace(tzinfo=UTC)`，壁钟不变、**绝对时刻被改写**，现有测试全绿 | 一行等值 + `utcoffset()` 断言 |

代码质量 **无 Critical**。边界探针全过（空串查询 / `max_tokens=1` × `reserve_ratio`∈{0,1} /
`min==max` / 空包列表 / 无 name 策略 / 无可变默认值泄漏）。

**🔴 给 Task 9 的红线（必须写进 H 系列派发词）**：
`_calculate_recency` 应**直接消费 `_parse_timestamp` 的输出**，
**不得再次 `replace(tzinfo=UTC)`** —— 否则触发 P6/P8 同类**时刻改写**。
这修正了我方 H-d / F3b 里「纵深防御保留归一」的旧表述：解析处归一之后，
下游再 replace 就是 bug 不是防御。

**已转回实施者修复**：T1 cache 接线 / T2 aware ISO 串 / T3 容差收紧 `delta < 1` /
T4 F5 定点补不变式。全部是**测试侧一行断言，实现代码不动**。
修完再派复审。

**T1–T4 修复轮已回报（提交 `6feeef6` `test: pin cache wiring, offset-aware ISO, fallback tolerance, budget split`）**：
仅 `tests/test_context_builder.py` +23/-1，`builder.py` 零触碰。
- T1 新增 `test_init_reads_cache_settings_from_config`（`cache_max_size=7` / `cache_ttl_seconds=12.5`）
- T2 新增 `test_parse_timestamp_keeps_offset_aware_iso_string`（`+02:00` 等值 + `utcoffset()` 双保险）；
  `timedelta`/`timezone` **函数内导入**（不碰顶部导入块，零 I001 风险）
- T3 `delta < 5` → `delta < 1`。自报对照：`now - 3s` 变异在旧断言下**会漏网**（`3.000018 < 5` 通过）
- T4 **比派发原文强**：不变式 `reserved + available == 500` **加** 具体值 `reserved == 100` / `available == 400`。
  理由（正确）：不变式在 `available = scaled - reserved` 写法下对 `reserved = 0` 变异**恒真**
  （实测 `0+500==500` 通过），只加不变式杀不掉 MT4b。具体值断言才杀得掉。
- 自报本轮 5 条目标变异全 **KILLED**，无 SURVIVED；`builder.py` 哈希 `d7898202…` 与开工前一致
- 42 passed（40+2）；回归 5 文件 `131 passed`（含 Task 5/6 修复轮的用例）

**修复复审结论（`6feeef6`）：Ready to merge: Yes —— Task 7 关闭。**
独立重跑 5 条目标变异 **5/5 KILLED**，与自报一致；`builder.py` 哈希前后一致（零触碰）；
`git status --porcelain` / `git diff HEAD` 双空；`ruff check` + `ruff format --check` 双清零。
两个委托判定：
1. **T4 双断言 = 合理（保留现状）**。复审员做了我方要求的退路验证：临时只留不变式
   `reserved + available == 500` 再注入 `reserved = 0` → **SURVIVED**（`0+500==500` 恒真）。
   实施者「只加不变式杀不掉 MT4b」**属实**，具体值两行是有效载荷不是装饰。
   也拒绝改成 `reserved == int(500 * 0.2)`（用公式验公式，对实现自证）。
2. **T2 函数内 import = 可接受**。文件内已有先例（`test_init_raises_config_error_when_tiktoken_unavailable`
   就是函数内 `import tiktoken`），风格一致、零 I001 风险。后续提到顶部纯属美化，不作要求。
   另记：T2 第二行 `utcoffset() == timedelta(hours=2)` 比单纯 `== aware_dt` 更强——
   Python 的 aware `==` 比绝对时刻，若被改成 `astimezone(UTC)`（12:00+02:00 → 10:00+00:00）
   绝对时刻仍相等、第一行会放过，第二行补杀。**这行必要，保留**。

备案两条（不阻塞）：T3 `delta < 1` 极慢 CI 或 flaky，若报警改前后夹逼两次 `now`，**不要**放宽回 5；
HEAD 前进到 `399c998` 属并行修复合入，本提交严格单文件。

**实施者自报偏离（待评审判定）**：
1. `experiment.py` 加 `from __future__ import annotations` —— 计划代码块漏了它，
   `apply()` 的 `config: ContextConfig` 注解在无 future import 时定义期求值 → `NameError`
   （`ContextConfig` 只在 `TYPE_CHECKING` 下导入）。**建议回改计划 Task 6 代码块**（D6 同类）。
2. `base.py` 局部导入的行尾注释挪到上一行 —— ruff I001 把行尾注释判为 un-sorted import。
3. E8 的构造期校验优先于计划原文。
4. 测试 12 → 21 条。
5. `ruff format` 重排两处换行（纯空白）。

**待收口的 3 条（实施者提出，记入后续）**：
- Spec §4.4 写 `bucket ∈ [0, 1)`，但 float64 下 `(2**64-1)/2**64 == 1.0`（实测 True），
  区间实为 `[0, 1]`。建议规格措辞改「落入 [0, 1]，取前 8 字节 / 2**64」。
  → 归入 Task 16 收尾时的规格措辞修订（与 L-e 同批）。
- `ExperimentSpec` 可变导致构造后可就地塞非法字段（E5 路径）。长期可改 `frozen=True`，
  当前按要求保留 `except TypeError` 并已钉测。**不动**。
- `context/__init__.py` 未导出 `ExperimentSpec`/`ExperimentAssigner`（按禁令未动）。
  计划 docstring 示例写的 `from hello_agents.context import ...` 与现实不符 ——
  **Task 13 会补齐**，不是遗漏。

---

## Task 14 预检（N 系列）—— 离线示例脚本

对象：计划 L3281–3430（`examples/context_builder_demo.py`）。

**已实测排除（不是问题）**：
- **N1 `Message(role=…, content=…)` 可用。** `Message` 是 pydantic 模型，
  `Message(role=MessageRole.USER, content="第1轮对话")` 实测 OK。
- **N2 Step 2 的「复杂查询 scaled 大于简单查询」成立。** 实测
  `HeuristicBudgetPolicy.estimate`：`'你好'` → complexity=0.0040 → scaled@4000=2008；
  长查询 + 10 条历史 → complexity=0.9200 → scaled=3840。**已实测**，不是估计。
- **N3 打分器构造签名匹配。** `EmbeddingSimilarityScorer(embedding=TFIDFEmbedding(dim=64))`
  与 `scoring.py` 一致；`scorer.name` 两实现都有（`"keyword"` / `"embedding"`）。
- **N4 `ExperimentSpec(name=…, variants=…)` 不传 `weights` 合法**（默认均匀）。
- **N5 时间戳全 tz-aware UTC**（`datetime.now(tz=UTC)`），符合 DTZ005 政策。

### 依赖（不是缺陷，但派发顺序必须保证）

| ID | 依赖 | 说明 |
|---|---|---|
| **N-a** | **硬依赖 Task 13** | 脚本 L3301 `from hello_agents.context import (…, EmbeddingSimilarityScorer, ExperimentAssigner, ExperimentSpec, KeywordOverlapScorer)`。实测当前 `context.__all__ == ['ContextConfig', 'ContextPacket']`，这 4 个名字 + `ContextBuilder` **全部未导出**。Task 13 的 16 名 `__all__` 含它们 —— 必须 Task 13 先落地，否则 Task 14 第一步就 ImportError |

### 必修

| ID | 级别 | 问题 | 修法 |
|---|---|---|---|
| **N-b** | 必修·验收不可判 | Step 2 的三条期望（复杂 scaled 更大、二次 `cache_hits > 0`、`session-42` 两次同变体）**靠人眼看输出**，脚本自身无断言。跑了不等于验了 | 二选一：① 脚本末尾加 `assert` 自检（复杂 scaled > 简单 scaled；`second.stats.cache_hits > 0`；两次 `assign(spec, "session-42")` 相等），任何一条不满足即 `SystemExit(1)` ② 在计划 Step 2 写明「实施者必须贴出三行输出作为证据」。推荐 ①（示例也能当烟雾测试） |
| **N-c** | 必修·A/B 演示弱钉 | `demo_experiment` 只打印 `session-42` 两次 + `session-7` 一次，稳定性靠肉眼看两行是否相同 | 改成 `assigner.assign(spec, "session-42")` 调两次并 `assert` 相等；再用 ≥8 个 session 断言两种变体**都出现**（非退化） |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|---|---|---|
| N-d | `_knowledge_packets()` 的 `metadata={"type": "rag"}` / `{"type": "memory"}` 只影响 `_structure` 路由，不进缓存。演示里看不出缓存对知识内容的作用（实践 3 的第二个用法） | 可接受（缓存演示由 `demo_cache` 的系统指令包承担）；若要加强，两次 `build_result` 喂同一批自定义包并对比 `cache_*` |
| N-e | 脚本无 `if __name__` 之外的可测面，`examples/` 进 lint 门槛（Task 16 Step 2 含 `examples`） | 无额外要求，`ruff check examples` 清零即可 |

### 派发 Task 14 时必带
**N-a 必须放在最前面**（Task 13 未落地则 ImportError）。N-b / N-c 必修 + 跑脚本的三行输出证据。
**N-b 要点明「Step 2 写的是 Expected，但示例脚本不是 pytest 用例，没有 harness 帮你判 —— 不加自检就等于没验」**。

---

## Task 15 预检（O 系列）—— README

对象：计划 L3434–3611（`README.md` 正文）。

### 🔴 硬阻塞

| ID | 问题 | 实测证据 | 修法 |
|---|---|---|---|
| **O-1** | README「接入检索工具」代码块的 `RAGTool(manager=manager)` **照抄会 TypeError** | 实测 `RAGTool.__init__(self, pipeline=None, llm=None)` —— 是 `pipeline` 不是 `manager`。`RAGTool(manager=None)` → `TypeError: RAGTool.__init__() got an unexpected keyword argument 'manager'`。**与 K-1b 同一个 bug**，K-1b 只修了计划 Task 12 的测试块，README 正文是同一错误的第二处 | 改成真实契约。最小修：`rag_tool=RAGTool(pipeline=RAGPipeline(memory_manager=manager))`（需补 `from hello_agents.memory.rag import RAGPipeline`），或简化为 `rag_tool=RAGTool()` 并注明参数见 `RAGPipeline`。**已实测 `RAGTool(pipeline=RAGPipeline(memory_manager=manager))` 构造 OK** |

### 已实测排除（不是问题）
- **O2 `MemoryManager()` 无参构造 OK**（实测通过，全部参数有默认值）
- **O3 `MemoryTool(manager=manager)` OK**（签名 `(self, manager: MemoryManager | None = None)`）
- **O4 `from hello_agents.tools.builtin import MemoryTool, RAGTool` OK**
- **O5 配置项表格的 18 个默认值与 `ContextConfig` 逐字段实测一致**：
  `max_tokens=3000 / reserve_ratio=0.2 / min_relevance=0.1 / enable_compression=True /
  recency_weight=0.3 / relevance_weight=0.7 / min_budget_ratio=0.5 / max_budget_ratio=1.0 /
  budget_policy=None / memory_limit=10 / rag_limit=5 / min_importance=0.0 /
  min_source_score=0.0 / history_window=5 / cache_max_size=256 / cache_ttl_seconds=3600.0 /
  log_stats=True / experiment=None`
- **O6 `stats.summary()` 示例输出格式对得上**（实测形如
  `candidates=3 selected=1 tokens=80/1500 utilization=0.05 complexity=0.50 compressed=True cache=1/3 duration=1.5ms`，
  README 用 `...` 省略尾部，可接受）

### 必修

| ID | 级别 | 问题 | 修法 |
|---|---|---|---|
| **O-2** | 必修·依赖 | 「快速开始」/「自定义相关性打分」/「A/B 测试」三处都 `from hello_agents.context import …`，其中 `EmbeddingSimilarityScorer` / `ExperimentSpec` / `ContextBuilder` 当前**未导出** | 与 N-a 同源：**Task 13 必须先落地**。README 不改写法，靠 Task 13 收口 |
| **O-3** | 必修·措辞夸大 | L3539「默认使用零依赖的 `KeywordOverlapScorer`；**配置 embedding 后端后升级为向量相似度**」—— 后半句是 `create_relevance_scorer("auto")` 的行为，**不是** `ContextBuilder` 的默认行为。`ContextBuilder` 缺省打分器恒为 keyword，向量打分必须显式注入（下一段示例正是显式注入） | 改为「默认使用零依赖的 `KeywordOverlapScorer`；需要向量相似度时显式注入 `EmbeddingSimilarityScorer`。`create_relevance_scorer("auto")` 会在向量后端不可用时自动降级为关键词重叠」。与 L-e 是同一缺口 |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|---|---|---|
| O-4 | 「快速开始」代码块用了未定义的 `history` | 文档片段常见写法，可接受；要严谨可加一行 `history = []` |
| O-5 | 模块概览表描述 `hello_agents.agents` 等本次未触碰的模块 | 出范围，不核；Task 15 只对 `context` 相关段落负责 |
| O-6 | `test` 段的三条命令与 Task 16 一致，但 `examples/` 当时才存在 | 顺序保证（Task 14 先于 15/16）即可 |

### 派发 Task 15 时必带
**O-1 必须放在最前面**（照抄即 TypeError，且与 K-1b 同源 —— 点明「这是同一个错误的第二处，K-1b 只修了测试块」）。O-2 依赖说明 + O-3 措辞修正。
**O-1 要把实测的签名贴进派发词**：`RAGTool.__init__(self, pipeline=None, llm=None)`、
`RAGPipeline.__init__(self, memory_manager=None, llm=None, chunk_size=512, chunk_overlap=64, top_k=5)`。

---

## Task 16 预检（P 系列）—— 全量验证

对象：计划 L3614–3702（Step 0–6）。

### 🔴 硬阻塞

| ID | 问题 | 实测证据 | 修法 |
|---|---|---|---|
| **P-1** | **门槛自相冲突**：Step 2 要求 `rag_tool.py` 零告警，但该文件**既有** 1 个 BLE001，而 Task 12 会触碰该文件 | 实测 `uv run ruff check hello_agents/tools/builtin/rag_tool.py` → `BLE001` at **L51**（`except Exception as error:`，行尾注释「解析/检索异常不应击穿 Agent 循环」—— 是**有意**的宽捕获）。D2 已写「既有 13 个告警记录在案不修（**除非落在 Task 12 会改的 rag_tool.py**）」。Step 2 又把 `rag_tool.py` 列进「必须全部零告警」名单 → 自相矛盾。Task 12 只改 L125（chunks 那行），不动 L51，但文件被触碰就吃这个门槛 | **归到 Task 12 的必修**：改 chunks 的同一提交里，给 L51 的 `except Exception` 加 `# noqa: BLE001`（与 `scoring.py:152` 同款做法，行尾注释已给出正当理由）。**不要**改捕获语义。这样 Task 16 Step 2 才能自洽 |

### 已实测排除（不是问题）
- **P2 剩余 13 个告警的分布已核实**，全部是 BLE001（12 个）+ `search.py:56` F401（1 个）：
  `react_agent.py` / `simple_agent.py` / `core/llm.py`×3 / `neo4j_store.py` /
  `calculator.py` / `memory_tool.py` / `rag_tool.py` / `search.py`×3 / `chain.py`。
  **`hello_agents/context/` 与 `tests/test_context_*.py` 当前已零告警**（Task 1–6 后实测）。
- **P3 Step 0 的一行修复描述准确**（`embedding.py:170` 的 `and backend == "auto"`），
  与我方 D3 一致。auto 路径行为不变（raise 在 try 内，被 `except Exception` 捕获后继续降级）。
- **P4 Step 5 的 7 条验收标准映射到具体测试名**，这些测试名在 Task 7–11 的计划块里都存在。
  但**收尾时要逐一确认它们真的存在且通过**（计划名与最终实现名可能漂移，G-j 已见过计数漂移）。

### 必修

| ID | 级别 | 问题 | 修法 |
|---|---|---|---|
| **P-2** | 必修·验收核对 | Step 5 逐条「由 test_X 覆盖」是**计划期的映射**，实施后测试名可能变（G-j 已记录过计数漂移：Task 7 写 22 passed、Task 8 应 36、计划却写 37） | Step 5 改为**运行时核对**：对 7 条标准逐条 `uv run pytest -k <test_name> -v` 并贴出结果；找不到同名用例就报 ISSUES，不要假定计划名还在 |
| **P-3** | 必修·范围声明 | Step 0 修的是 `hello_agents/memory/embedding.py` —— **超出「仅 ContextBuilder」原始范围** | 已在计划与 D3 显式声明。**最终报告必须单列「计划外的一行修复」**，与并发会话冲突并列作为两条范围外披露 |

### 建议（不阻塞）

| ID | 问题 | 修法 |
|---|---|---|
| P-4 | Step 6 的 `git commit --allow-empty` 只是记账 | 可接受；若 Step 0–5 全无代码改动，这条空提交是验收痕迹 |
| P-5 | 未要求 `uv run pytest tests/ -q` 的**失败数不增加**判据（我方测试基线段落里有） | Step 1 的 Expected 写的是「`0 failed`」——Step 0 修完后应为真。保持 |
| P-6 | Spec §4.4 的 `bucket ∈ [0, 1)` 措辞修订、L-e/O-3 的 docstring 措辞、D6 的 Task 1 代码块同步 | 归入 Task 16 的**收尾文档修订批**，一次性做，不单开任务 |

### 派发 Task 16 时必带
**P-1 必须放在 Step 0 的旁边**（或直接并进 Task 12 派发词，推荐后者 —— 一次提交内改完）。
P-2 的运行时核对要求 + P-3 的最终报告披露要求。
**P-1 要点明「这是让 Step 2 门槛自洽的必要条件，不是顺手改风格」**。
