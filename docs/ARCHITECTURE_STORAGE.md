# Mindspace 存储架构

本文档记录 Mindspace 当前已经实现的文件存储与仓储边界。本文描述的是现行生产结构，不是未来方案。

## 1. 架构目标

存储层遵循以下原则：

1. 业务代码依赖仓储能力或 Protocol，不依赖 JSON 写入细节。
2. 每个高内聚仓储拥有自己的物理模块。
3. JSON 读取、原子写、JSON Patch、路径和元数据能力只有一份实现。
4. 组合根直接导入物理仓储模块。
5. `adapters.file_storage` 只保留旧导入路径兼容，不承载业务实现。
6. 重构不得改变数据路径、文档格式、锁、事务、revision、备份、迁移或投影顺序。

## 2. 当前模块关系

```text
bootstrap.py
  |-- adapters.profile_repository.JsonProfileRepository
  |-- adapters.session_repository.JsonSessionRepository
  `-- adapters.local_retriever.LocalKnowledgeRetriever
        `-- ports.ChatCorpusPort
              `-- JsonSessionRepository 结构化满足

adapters.profile_repository
  |-- infrastructure.storage.json_io
  |-- infrastructure.storage.json_patch
  `-- infrastructure.storage.metadata

adapters.session_repository
  |-- infrastructure.storage.json_io
  |-- infrastructure.storage.paths
  `-- infrastructure.storage.metadata

adapters.file_storage
  `-- 兼容重导出 Profile、Session 及历史公共名称
```

`bootstrap.py` 是具体实现的组合根。应用服务、检索器和其他业务模块不应通过 `file_storage.py` 获取新的依赖。

## 3. Profile Repository

物理模块：

```text
src/mindspace_graph/adapters/profile_repository.py
```

权威实现：

```python
JsonProfileRepository
```

该仓储负责：

- 用户档案、AI 档案、角色记忆和运行状态文档。
- 默认文档 `DEFAULT_PROFILES`。
- 目标文件映射 `TARGET_FILES`。
- Profile Schema 补全和验证。
- revision 乐观并发检查。
- JSON Update 应用及写入回执。
- 历史版本备份和恢复。
- 角色仓储绑定。
- ProductDatabase 文档写入。
- 数据库提交后的延迟文件投影。

该模块不实现通用 JSON I/O 或 JSON Patch 算法，而是调用 `infrastructure.storage` 的公开能力。

## 4. Session Repository

物理模块：

```text
src/mindspace_graph/adapters/session_repository.py
```

权威实现：

```python
JsonSessionRepository
```

该仓储负责：

- 会话文档创建、读取和存在性判断。
- 最近消息与完整消息读取。
- 回合持久化。
- 消息、轮次、会话和全部会话删除。
- 删除事件和写入回执。
- 检索分块 `list_chunks()`。
- 历史 analysis 字段迁移。
- 旧会话文件导入和旧路径兼容。
- 成人模式及角色范围的检索过滤。
- ProductDatabase 事务和延迟文件投影。

会话仓储继续拥有自己的 `RLock`。路径 helper 不持有锁，也不决定事务边界。

## 5. ChatCorpusPort

定义位置：

```text
src/mindspace_graph/ports.py
```

`ChatCorpusPort` 是检索器使用的最小只读会话语料接口：

```python
class ChatCorpusPort(Protocol):
    def load_session(self, session_id: str) -> dict[str, Any]: ...

    def list_chunks(
        self,
        session_id: str | None = None,
    ) -> list[dict[str, Any]]: ...
```

`LocalKnowledgeRetriever` 只依赖该 Protocol，不导入 `JsonSessionRepository`。

`JsonSessionRepository` 通过 Python 结构化类型直接满足协议。禁止为了满足该接口增加无行为价值的包装 Adapter。

若检索器未来需要新的会话读取能力，应先确认它确实属于只读 Chat Corpus，再向 Protocol 增加最小方法。禁止把写入、删除、事务或数据库对象暴露给检索器。

## 6. infrastructure/storage

目录：

```text
src/mindspace_graph/infrastructure/storage/
```

这里保存与具体 Profile、Session 业务无关的底层存储能力。

### 6.1 json_io.py

公开接口：

```python
atomic_json_write(path, value)
read_json(path)
```

`atomic_json_write` 保证：

1. 创建目标父目录。
2. 在目标目录创建临时文件。
3. 使用 UTF-8 写入 JSON。
4. 使用 `ensure_ascii=False` 和 `indent=2`。
5. 刷新 Python stream。
6. 调用 `os.fsync()`。
7. 使用 `os.replace()` 原子替换目标文件。
8. 成功或失败后清理残留临时文件。
9. 不吞掉原生文件系统和 JSON 异常。

`read_json` 使用 UTF-8 和标准 `json.load()`，不包装或重写 `OSError`、`JSONDecodeError` 等异常。

### 6.2 json_patch.py

公开接口：

```python
json_pointer_tokens(path)
read_json_pointer(document, path)
apply_json_patch(document, operation)
```

该模块是 Profile 与角色仓储共用的唯一 JSON Pointer/Patch 实现。禁止在仓储内复制、轻微改写或维护第二份 Patch 逻辑。

Patch 的路径解析、深拷贝、非法路径和非法操作异常均属于兼容行为。修改它们必须按数据协议变更处理，而不是普通重构。

### 6.3 paths.py

公开接口：

```python
safe_json_stem(...)
hashed_json_document_path(...)
legacy_json_document_path(...)
```

当前会话路径规则：

1. 非 `[a-zA-Z0-9_.-]` 字符替换为 `-`。
2. 去除首尾 `.` 和 `-`。
3. 空结果回退为 `session`。
4. 当前路径可读前缀限制为 48 个字符。
5. 后接原始 session ID 的完整 SHA-256。
6. 扩展名固定为 `.json`。

旧路径不包含哈希，也不截断安全 stem，只用于兼容读取和迁移。禁止删除旧路径支持，除非存在独立、可恢复且经过版本控制的数据迁移。

### 6.4 metadata.py

公开接口：

```python
utc_now_iso()
```

返回格式保持为：

```python
datetime.now(UTC).isoformat()
```

Profile 与 Session 的更新时间、回执或迁移时间不得各自维护不同的 UTC 格式函数。

## 7. file_storage 兼容门面

兼容模块：

```text
src/mindspace_graph/adapters/file_storage.py
```

它不再包含仓储实现，仅兼容重导出：

```python
JsonProfileRepository
JsonSessionRepository
DEFAULT_PROFILES
TARGET_FILES
```

并为历史直接导入保留以下别名：

```python
_atomic_json
_apply_patch
_pointer_tokens
_read_pointer
```

这些名称分别指向 `infrastructure.storage` 的唯一真实实现，不是复制代码。

规则：

- 新生产代码禁止从 `adapters.file_storage` 导入任何名称。
- 组合根必须从物理仓储模块导入。
- 公共底层能力必须从 `infrastructure.storage` 导入。
- 旧门面仅用于外部兼容和未迁移测试。
- 禁止继续向旧门面增加新业务实现。

## 8. 存储不变量

以下内容是存储重构的硬性不变量。

### 8.1 路径不变量

- Profile 文件名由现有 `TARGET_FILES` 决定。
- Session 当前文件名继续使用安全前缀和完整 SHA-256。
- 旧 Session 路径继续执行归属校验后兼容读取。
- 运行目录和 `data` 根目录不能由仓储内部重新推断。
- 不得把用户数据移动到源码目录或安装包目录。

### 8.2 文档不变量

- JSON 编码保持 UTF-8。
- `ensure_ascii=False`。
- 缩进保持两个空格。
- Schema 字段、默认值和兼容字段不能在物理拆分中改变。
- 读取失败的异常类型和现有降级位置不能改变。

### 8.3 revision 不变量

- revision 检查必须在写入前完成。
- stale revision 必须继续拒绝。
- 成功保存时 revision 增长次数不变。
- 恢复历史版本也必须产生新 revision，不能倒退权威版本号。
- 不允许基础设施 helper 隐式修改 revision。

### 8.4 锁不变量

- 锁由具体仓储持有。
- helper 不新建第二把锁。
- 读改写操作的原有锁范围不得缩小。
- 不允许通过拆函数把原子业务操作拆出锁区。

### 8.5 事务与投影不变量

- ProductDatabase 仍是启用数据库模式时的权威提交路径。
- 文件投影继续在数据库写入之后执行。
- 延迟投影的注册顺序不得改变。
- 不允许先写 JSON 文件、再提交数据库以模拟相同结果。
- 事务失败不得留下被误认为已提交的文件投影。

### 8.6 备份不变量

- 写入前备份的位置、命名和时机保持不变。
- 历史恢复前仍需备份当前版本。
- `shutil.copy2()` 的元数据保留行为不得无意替换。
- 备份失败的异常不得被静默吞掉。

### 8.7 迁移不变量

- Legacy 导入必须幂等。
- 已导入数据库的文档不能重复覆盖。
- Session analysis 清理仍需先创建迁移备份。
- 旧路径迁移不能读取或接管不属于当前 session ID 的文件。
- 迁移完成前不得删除原始兼容读取逻辑。

## 9. 新增仓储规范

新增仓储时必须按以下顺序执行。

### 9.1 确定聚合边界

仓储应围绕一个可独立维护的数据聚合建立，例如 Profile、Session。不要按“所有 JSON 文件”建立万能仓储，也不要仅为了目录整齐创建没有行为的 Repository。

### 9.2 定义最小能力

- 应用层需要替换实现时，在 `ports.py` 定义最小 Protocol。
- 只读消费者只获得只读方法。
- 不向 Protocol 暴露 `Path`、文件句柄、锁或 `ProductDatabase`。
- 现有具体仓储能结构化满足协议时，不增加包装 Adapter。

### 9.3 复用基础设施

- JSON 读写使用 `json_io.py`。
- JSON Pointer/Patch 使用 `json_patch.py`。
- 安全路径使用 `paths.py`。
- UTC ISO 时间使用 `metadata.py`。
- 不复制 helper，也不通过改名维护等价实现。

### 9.4 保持组合根明确

- 在 `bootstrap.py` 创建具体仓储。
- 从仓储的物理模块导入。
- 通过构造参数注入消费者。
- 仓储模块不得反向导入 `bootstrap.py`、API 或应用服务。

### 9.5 保持兼容边界

如果已有公开导入路径：

1. 先完整迁移实现。
2. 原模块改为重导出门面。
3. 生产代码切换到物理模块。
4. 外部兼容门面保留一个明确迁移周期。
5. 禁止在门面和物理模块保留双份实现。

## 10. 迁移仓储规范

物理迁移现有仓储时必须遵守：

1. 先确认类的完整源码边界和模块级依赖。
2. 必须整类迁移，禁止一半方法留在旧模块。
3. 共享 helper 要么随唯一使用者迁移，要么提升到 `infrastructure.storage`。
4. 禁止通过复制实现规避依赖整理。
5. 保持类名、构造参数顺序、默认值和公开方法不变。
6. 保持 `RLock` 创建位置和临界区不变。
7. 保持数据库写入、备份和投影的执行顺序不变。
8. 保持所有数据路径和旧路径回退逻辑不变。
9. 组合根切到物理模块。
10. 旧模块只重导出兼容名称。
11. 生产代码全部停止依赖旧门面。
12. 在独立验证阶段检查路径、revision、事务、迁移和异常兼容性。

如果不能证明上述条件，应停止迁移并记录具体依赖，不允许提交半拆分状态。

## 11. 禁止事项

- 禁止重新建立包含多个无关聚合的 `file_storage` 实现。
- 禁止 Repository 直接调用另一个具体 Repository，除非它代表明确聚合关系并经架构审查。
- 禁止 API 路由导入 JSON helper。
- 禁止检索器获得会话写权限。
- 禁止 helper 持有业务锁或数据库事务。
- 禁止在物理拆分中顺手更改数据格式。
- 禁止以“清理”为名删除 legacy 路径、备份或迁移逻辑。
- 禁止新生产代码使用 `adapters.file_storage` 兼容门面。

## 12. 当前剩余边界

- Profile 与 Session 已有独立物理仓储。
- `LocalKnowledgeRetriever` 已通过 `ChatCorpusPort` 与 Session 的具体实现解耦。
- JSON I/O、Patch、路径和时间元数据已有唯一公共实现。
- `file_storage.py` 仍需保留以兼容外部和旧测试导入。
- 其他 ProductDatabase 直连模块是否需要仓储化，应按独立聚合逐项评估，不能为追求形式统一一次性包装。
