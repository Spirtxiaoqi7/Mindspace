const MINIMUM_QWEN_VRAM_MIB = 15_500;
const MINIMUM_QWEN_FREE_VRAM_MIB = 14_500;
const MINIMUM_QWEN_RAM_BYTES = 31 * 1024 ** 3;

function evaluateQwenRuntimePreflight(input = {}) {
  const system = input.system || {};
  if (!system.nvidia) {
    return { eligible: false, code: "NVIDIA_REQUIRED", message: "Qwen3 实时语音需要兼容的 NVIDIA 显卡；未满足时不会提供安装或下载。" };
  }
  const memoryTotalBytes = Number(system.memoryTotalBytes || 0);
  if (memoryTotalBytes > 0 && memoryTotalBytes < MINIMUM_QWEN_RAM_BYTES) {
    return {
      eligible: false,
      code: "RAM_INSUFFICIENT",
      message: `Qwen3 实时语音需要标称 32 GB 系统内存；检测到约 ${Math.round(memoryTotalBytes / 1024 ** 3)} GB。为避免系统失去响应，未开放安装。`,
    };
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
      message: `Qwen3 实时语音需要标称 16 GB 总显存；检测到约 ${Math.round(vramMiB / 1024)} GB。为避免挤占 ASR/桌面显存，未开放安装。`,
    };
  }
  const availableVramMiB = Number(input.availableVramMiB || 0);
  if (availableVramMiB > 0 && availableVramMiB < MINIMUM_QWEN_FREE_VRAM_MIB) {
    return {
      eligible: false,
      code: "VRAM_BUSY",
      message: `Qwen3 实时语音需要约 ${Math.round(MINIMUM_QWEN_FREE_VRAM_MIB / 1024)} GB 可用显存；当前仅约 ${Math.round(availableVramMiB / 1024)} GB。请先停止 ASR 或其他 GPU 程序后再启动。`,
    };
  }
  if (input.portConflict) {
    return { eligible: false, code: "QWEN_PORT_CONFLICT", message: `端口 ${input.port || "Qwen"} 已被非 Qwen3 服务占用；Mindspace 不会终止未知进程。` };
  }
  return {
    eligible: true,
    code: input.modelReady ? "READY" : "RUNTIME_READY",
    message: input.modelReady ? "Qwen3 环境、显存与模型检查通过。" : "硬件与 WSL2 检查通过；仅允许接入已存在且完整的本地模型，不会自动下载。",
  };
}

module.exports = { MINIMUM_QWEN_RAM_BYTES, MINIMUM_QWEN_VRAM_MIB, MINIMUM_QWEN_FREE_VRAM_MIB, evaluateQwenRuntimePreflight };
