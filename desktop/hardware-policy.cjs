const GIB = 1024 ** 3;
const MIB_PER_GIB = 1024;

const SERVICE_REQUIREMENTS = {
  asr: { ramGiB: 8, vramGiB: 6, label: "实时聆听" },
  cosyvoice: { ramGiB: 16, vramGiB: 6, label: "CosyVoice" },
  "gpt-sovits": { ramGiB: 16, vramGiB: 6, label: "GPT-SoVITS" },
  qwen3: { ramGiB: 32, vramGiB: 16, label: "Qwen3-TTS" },
};

function requirementKey(id = "") {
  const value = String(id).toLowerCase();
  if (value === "qwentts" || value.includes("qwen3")) return "qwen3";
  if (value === "tts" || value.startsWith("tts-") || value.includes("cosyvoice")) return "cosyvoice";
  if (value.includes("gpt-sovits")) return "gpt-sovits";
  if (value === "asr" || value.startsWith("asr-") || value === "vad" || value === "punc") return "asr";
  return "";
}

function evaluateHardwareAvailability(id, system = {}) {
  const key = requirementKey(id);
  if (!key) return { eligible: true, code: "NO_LOCAL_GPU_REQUIREMENT", message: "" };
  const requirement = SERVICE_REQUIREMENTS[key];
  if (!system.nvidia) {
    return {
      eligible: false,
      code: "NVIDIA_REQUIRED",
      message: `${requirement.label}需要兼容的 NVIDIA 显卡；文字聊天和云端声音仍可使用。`,
      requirement,
    };
  }
  const totalRam = Number(system.memoryTotalBytes || 0);
  // Windows and firmware reserve a small part of marketed RAM/VRAM. Use a
  // narrow tolerance so a real 16 GB card is not misclassified as 15.9 GB.
  if (totalRam > 0 && totalRam < requirement.ramGiB * GIB * 0.95) {
    return {
      eligible: false,
      code: "RAM_INSUFFICIENT",
      message: `${requirement.label}至少需要 ${requirement.ramGiB} GB 内存；本机约 ${Math.round(totalRam / GIB)} GB，因此不会开放安装或启动。`,
      requirement,
    };
  }
  const totalVram = Number(system.vramTotalMiB || 0);
  if (totalVram > 0 && totalVram < requirement.vramGiB * MIB_PER_GIB * 0.95) {
    return {
      eligible: false,
      code: "VRAM_INSUFFICIENT",
      message: `${requirement.label}至少需要 ${requirement.vramGiB} GB 显存；本机约 ${Math.round(totalVram / MIB_PER_GIB)} GB，因此不会开放安装或启动。`,
      requirement,
    };
  }
  return {
    eligible: true,
    code: "HARDWARE_READY",
    message: `${requirement.label}硬件门槛检查通过。`,
    requirement,
  };
}

module.exports = {
  GIB,
  SERVICE_REQUIREMENTS,
  evaluateHardwareAvailability,
  requirementKey,
};
