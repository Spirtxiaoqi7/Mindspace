const MINIMUM_QWEN_VRAM_MIB = 14_000;

function evaluateQwenRuntimePreflight(input = {}) {
  const system = input.system || {};
  if (!system.nvidia) {
    return { eligible: false, code: "NVIDIA_REQUIRED", message: "Qwen3 实时语音需要兼容的 NVIDIA 显卡；未满足时不会提供安装或下载。" };
  }
  if (!input.wslAvailable) {
    return { eligible: false, code: "WSL2_REQUIRED", message: "未检测到可用 WSL2；不会自动安装系统组件或下载大模型。" };
  }
  if (!input.distroAvailable) {
    return { eligible: false, code: "WSL_DISTRO_REQUIRED", message: "未检测到受管 WSL 运行环境 MindspaceVLLM；请先按运行时说明完成 WSL2 环境。" };
  }
  if (!input.wslGpuAvailable) {
    return { eligible: false, code: "WSL_GPU_REQUIRED", message: "WSL2 无法访问 NVIDIA GPU；请更新驱动并确认 WSL GPU 透传可用。" };
  }
  const vramMiB = Number(input.vramMiB || 0);
  if (vramMiB < MINIMUM_QWEN_VRAM_MIB) {
    return {
      eligible: false,
      code: "VRAM_INSUFFICIENT",
      message: `Qwen3 实时语音至少需要 ${Math.ceil(MINIMUM_QWEN_VRAM_MIB / 1024)} GB 可用显存；检测到约 ${Math.round(vramMiB / 1024)} GB。为避免挤占 ASR/桌面显存，未开放安装。`,
    };
  }
  if (input.portConflict) {
    return { eligible: false, code: "QWEN_PORT_CONFLICT", message: "端口 8091 已被非 Qwen3 服务占用；请先释放端口后再启用。" };
  }
  return {
    eligible: true,
    code: input.modelReady ? "READY" : "RUNTIME_READY",
    message: input.modelReady ? "Qwen3 环境、显存与模型检查通过。" : "硬件与 WSL2 检查通过；仅允许接入已存在且完整的本地模型，不会自动下载。",
  };
}

module.exports = { MINIMUM_QWEN_VRAM_MIB, evaluateQwenRuntimePreflight };
