# JSON 字段标签记忆

## 目标

记忆分类完全使用服务端已经提交成功的 JSON Patch，不要求 LLM 额外生成标签，也不使用自然语言标签聚类。元数据只参与存储、归并和排序，不会进入 Prompt。

## 存储结构

```text
episode（原文，只保存一次）
  ├─ active binding：user_profile + /stable_preferences/likes
  ├─ active binding：runtime_state + /user_state/current_topic
  └─ source message ids
```

- `episodes`：保存本轮用户输入与 AI 回复的原始文本。
- `active`：每条记录只绑定一个 JSON 叶子字段；多个字段共享同一个 `episode_id`，不复制原文。
- `untagged`：无 JSON Patch 文本的隔离池，不参与向量检索。
- `tombstones`：字段撤销的有界审计记录，不参与检索。

向量也缓存在 `episode` 上；同一段原文即使绑定多个 JSON 字段也只计算、保存一份向量。

标签格式：

```json
{
  "tag_id": "json:user_profile:/stable_preferences/likes",
  "target": "user_profile",
  "path": "/stable_preferences/likes",
  "polarity": "like"
}
```

标签、曝光次数和归并键不会传给 LLM。召回 Prompt 只得到命中的原始文本、来源、轮次和分数。

人物设定初始化产生的记忆仍使用相同 JSON 字段标签；其来源文本记录为用户设定或角色设定，而不是让模型额外生成分类描述。初始化窗口只存在于新会话前三轮，第四轮后与普通记忆写入完全一致。

## 字段注册表与归并器

所有可写 JSON 叶子字段集中定义在 `memory_registry.py`。注册项同时声明中文名、类别、值类型、作用域、生命周期、容量上限、归并器和对立字段族，校验、写回、分类、召回与前端展示共享这一份定义，避免各链路各自维护路径清单。

- `replace_one`：姓名、职业、当前任务等单值槽位，新值覆盖旧值。
- `unique_set`：兴趣、经历、边界等集合，按规范化值去重。
- `opposing_set`：喜欢/不喜欢等对立集合，共用实体键，新极性使旧极性失效。
- `bounded_event`：近期事件与线索，去重后按字段上限淘汰最旧项。
- `lifecycle`：待办、问题等有状态内容，完成或删除后进入墓碑记录。

失效项写入有界 `tombstones`，仅用于审计和恢复，不参与 Prompt 或向量召回。用户在记忆中心执行编辑、删除、恢复时，由服务端确定性归并器同步修改档案 JSON 和活动记忆，不要求 LLM 判断。

## 三种输入情况

### 无标签文本

- 不进入长期向量索引。
- 只进入隔离池，按完整内容哈希去重。
- 单会话最多 24 条，全局最多 128 条，默认 14 天过期。
- 重复出现只增加 `repeat_count`，不会因此晋升。

因此，无标签块不会无限堆积，也不会因为被反复召回而获得虚假的长期身份。

### 单标签文本

- 如果标签来自一次已校验、已提交的 JSON Patch，立即形成活动记忆。
- 不要求凑够两个标签。JSON 路径本身已经是精确分类边界。
- 标量字段按 `target + path` 覆盖；列表项按 `target + path + value fingerprint` 独立归并。

单标签不是弱证据。是否可信由 JSON 写入流程决定，标签数量不参与可信度判断。

### 多标签文本

- 原文仍只保存一份。
- 每个 JSON Patch 拆成一个独立 binding。
- 任何一个字段被替换或删除时，只更新对应 binding，不重写整段文本或其他字段。

## 对立字段自消除

`likes` 与 `dislikes` 使用同一偏好槽位族，并以规范化后的对象值生成相同 `memory_key`。例如“喜欢草莓”改为“不喜欢草莓”时，新 binding 原位替换旧 binding，不会同时留下两条冲突活动记忆。

其他对立字段应通过显式字段注册表增加，不能靠语义相似度猜测。

## 晋升与公平召回

系统不再使用“召回次数达到阈值自动晋升”：

- 唯一活动记忆入口是成功提交的 JSON Patch。
- `recall_count`、`selection_count`、`eligible_misses` 只记录曝光状态。
- 默认保留 20% 召回位置给已达到相似度阈值但长期未入选的结构化记忆。
- 同一个 JSON 字段族默认最多先占 2 个位置；有空位时再放宽，避免浪费上下文。
- 饥饿加分有上限，只改变合格候选之间的次序，不能绕过相似度阈值，也不能触发晋升。

这把两个问题彻底分开：JSON 写入决定“它是不是记忆”，公平重排决定“合格记忆何时有机会被看见”。

## 运行文件与检查接口

- 运行文件：`runtime/data/structured-memory.json`
- 检查接口：`GET /api/v1/memory/structured`
- 字段注册表：`GET /api/v1/memory/registry`
- 记忆中心列表：`GET /api/v1/memory/items?include_history=true`
- 编辑/删除：`PUT|DELETE /api/v1/memory/items/{memory_key}`
- 恢复：`POST /api/v1/memory/restore`
- 前端设置：`RAG 与分块` 中的“JSON 字段记忆”和“公平曝光保护”。

删除 AI 回复、整轮、会话或清空会话时，与被删消息关联的活动记忆会立即退出召回；权威 JSON 仍按删除事件规则在下一次正常对话中校正。
