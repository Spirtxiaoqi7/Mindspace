# 验证报告

验证日期：2026-07-17（Asia/Shanghai）

## 环境

| 项目 | 版本 |
|---|---|
| PowerShell | 7.6.3 |
| Python | 3.13.12 |
| uv | 0.11.26 |
| LangGraph | 1.2.9 |
| FastAPI | 0.139.2 |
| Pydantic | 2.13.4 |

## 自动化验证

执行：

```powershell
pwsh -File .\scripts\verify.ps1
```

结果：

- `ruff format --check .`：通过，27 个文件格式正确。
- `ruff check .`：通过，无静态检查错误。
- `pytest -q`：通过，13 个测试全部成功。
- `node --check src/mindspace_graph/web/app.js`：通过。
- `uv build --wheel`：通过。

测试覆盖：

1. 图成功路径、并行双源检索和会话持久化。
2. 四标签协议失败后有限修复，修复结果禁止长期写回。
3. 非法提交计划保留回复但阻断档案写回。
4. 关系状态高置信守卫与重新生成只读语义。
5. FastAPI 首屏、健康和脱敏配置。
6. SSE `accepted/progress/final` 完整事件链。
7. 知识写入后立即可检索。
8. mock TTS 生成合法 RIFF/WAV。
9. mock ASR 返回转写结果。
10. 模型返回后取消，确保会话、档案和关系状态均不落盘。
11. 档案 patch、revision 和写前备份。
12. 重新生成替换原轮次，不产生重复消息。
13. `.env` 加载以及公共配置密钥脱敏。

自动测试产生一条 Starlette TestClient 关于未来 `httpx2` 的弃用提醒，不影响当前功能或测试结果。

## API 与浏览器验收

本地服务：`http://127.0.0.1:8765`

验证项目：

- `GET /api/v1/health`：200，版本 `0.2.0`。
- 首屏加载：通过。
- 输入消息并按 Enter：通过。
- SSE 节点进度和最终回复：通过。
- 会话写入后出现在侧栏：通过。
- 刷新后自动恢复当前会话：通过。
- 服务状态面板打开/关闭：通过。
- 390×844 移动端布局：通过，侧栏收起、输入器可达。
- 浏览器控制台 error 日志：0 条。

浏览器验收期间发现并修复：首次随机 session ID 没有立即写入 `localStorage`，导致刷新后不自动恢复。修复后连续刷新验证成功。

截图：

- [产品首屏](assets/product-home.png)
- [持久化会话](assets/product-chat.png)
- [移动端 390×844](assets/product-mobile.png)

## 封装验证

### Wheel

产物：`dist/mindspace_langgraph-0.2.0-py3-none-any.whl`，47,521 bytes。

解包确认包含：

- `mindspace_graph/api.py`
- `mindspace_graph/web/index.html`
- `mindspace_graph/web/styles.css`
- `mindspace_graph/web/app.js`
- console entry points

在全新 `dist/smoke-venv` 中只安装 wheel 后启动 `mindspace-server`：

- `/api/v1/health`：200。
- `/`：200。
- 页面包含 `Mindspace Graph`：是。

### 便携 ZIP

产物：`dist/mindspace-graph-portable.zip`，48,868 bytes。

内容确认：wheel、`portable-start.ps1`、`.env.example`、README。

### Docker

- `docker compose config --quiet`：通过。
- Docker CLI：29.3.1。
- 镜像实际构建：未执行；本机 Docker Desktop Linux Engine 未启动。Dockerfile 与 Compose 配置已完成静态配置验证。

## 源项目只读校验

- 分析前后 `A:\Mindscape-app\Mindspace\backend` 32 个核心源文件逐个比较 SHA-256：变化 0。
- Launcher `resources/app.asar` SHA-256：`C714C153AE988836FA2F050974C7E08A8BC5E4DAFA0F6B8319F07D05BFB0B62C`，与分析前一致。
- 新增代码、数据、音频、日志和构建产物均位于 `A:\RAG\langgarph-rag`。

## 已知边界

1. 默认检索器已接入 BM25+、SentenceTransformer、RRF 与有界 Boost；cross-encoder 是可选离线组件，未安装时诊断会显示降级。
2. 同步模型 HTTP 调用已发出后无法立即终止底层 socket，但返回后会再次检查取消信号，并在所有写操作之前停止。
3. CosyVoice GPU 合成未在本轮实际执行，因为本轮没有启动 5055 Worker；其 `/health`、`/synthesize` 和共享输出路径合约已按现有 Worker 实现。
4. 浏览器 Speech Recognition 的可用性取决于浏览器；不可用时前端自动转为录音并调用后端 ASR。
5. Docker 镜像还需在 Docker Desktop 引擎启动后执行一次真实 build/run 验收。
