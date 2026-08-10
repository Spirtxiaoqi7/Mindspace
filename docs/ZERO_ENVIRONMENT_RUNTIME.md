> 文档状态：prototype。内容尚未成为当前产品承诺；当前权威见 `docs/INDEX.md`。

# 零环境运行时

Mindspace 0.7.4 支持 Windows 10/11 x64 当前用户安装。Launcher 不查询或调用系统 Python、
pip、Git、uv 和 PowerShell 7；这些组件由签名清单固定版本并安装到统一的 Mindspace 数据根。
新安装默认使用安装目录旁的 `MindspaceData`，因此用户把应用装到 D 盘时，Core、私有环境、
模型、数据和下载缓存也位于 D 盘。Launcher 自身少量位置配置保留在 Windows 用户目录。

旧版 `%LOCALAPPDATA%\Mindspace` 存在有效数据时不会被静默切换。Launcher 先继续使用旧目录，
再显示安装盘建议目标；用户确认后采用 staging 复制、绝对路径改写、Core 校验、原子提升和
重启验证。只有新目录通过健康检查后才清理旧目录，失败时继续使用原位置。

安装顺序为 PowerShell 7、MinGit、uv、Python 3.11、核心 venv/pip、中文向量模型。前三个工具与 Python 预置在安装包中；归档缺失时才按清单 URL 下载并做 SHA-256 校验。核心依赖使用 `uv.lock --frozen`，优先阿里云 PyPI，失败回退官方 PyPI。

每个组件写入 `environment/state/components/<id>.json`，版本目录另有 `current.json`。下载使用 `.partial`，部署使用 `.staging-*`；探针成功后才原子改名并写入凭证。升级保留当前版本和最近上一版本，旧凭证仍可作为失败回退。

可选 ASR、TTS、人物音色和模型由组件包管理器维护。共享依赖仍被其他已就绪组件使用时禁止
卸载；人物音色只删除本人物的权重与参考音频。基础 Core、中文向量模型和用户数据不提供
“卸载组件”操作。Fun-ASR Nano 在早期版本中误落到 `assets/models` 时，0.7.4 会在服务启动前
自动认领到统一 `models` 目录，避免重复下载。

服务进程只收到应用私有 PATH，并通过 `MINDSPACE_HOME`、`MINDSPACE_RUNTIME_DIR`、`MINDSPACE_MODEL_ROOT`、`MINDSPACE_PWSH`、`MINDSPACE_UV` 和 `MINDSPACE_CORE_PYTHON` 获取绝对路径。NVIDIA 驱动是唯一不能私有部署的系统组件；缺少时只禁用本地语音。

Launcher 同时读取系统 RAM 和 NVIDIA 显存总量/空闲量。ASR、CosyVoice、GPT-SoVITS 和
Qwen3-TTS 分别按自身门槛判断；不满足时在下载和启动两处拒绝操作，并显示具体的内存或显存
原因。该判断不影响文字聊天、RAG、人物卡与云端 TTS。
