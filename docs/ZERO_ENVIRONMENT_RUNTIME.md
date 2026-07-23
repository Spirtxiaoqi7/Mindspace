# 零环境运行时

Mindspace 0.4.0 正式支持 Windows 10/11 x64 当前用户安装。Launcher 不查询或调用系统 Python、pip、Git、uv 和 PowerShell 7；这些组件由签名清单固定版本并安装到 `%LOCALAPPDATA%\Mindspace\environment`。

安装顺序为 PowerShell 7、MinGit、uv、Python 3.11、核心 venv/pip、中文向量模型。前三个工具与 Python 预置在安装包中；归档缺失时才按清单 URL 下载并做 SHA-256 校验。核心依赖使用 `uv.lock --frozen`，优先阿里云 PyPI，失败回退官方 PyPI。

每个组件写入 `environment/state/components/<id>.json`，版本目录另有 `current.json`。下载使用 `.partial`，部署使用 `.staging-*`；探针成功后才原子改名并写入凭证。升级保留当前版本和最近上一版本，旧凭证仍可作为失败回退。

服务进程只收到应用私有 PATH，并通过 `MINDSPACE_HOME`、`MINDSPACE_RUNTIME_DIR`、`MINDSPACE_MODEL_ROOT`、`MINDSPACE_PWSH`、`MINDSPACE_UV` 和 `MINDSPACE_CORE_PYTHON` 获取绝对路径。NVIDIA 驱动是唯一不能私有部署的系统组件；缺少时只禁用本地语音。
