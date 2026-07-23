const fs = require("node:fs");
const path = require("node:path");

const BASE = "https://www.modelscope.cn/models/XXXXRT/GPT-SoVITS-Pretrained/resolve/master";
const BASE_OFFICIAL = "https://huggingface.co/lj1995/GPT-SoVITS/resolve/main";
const VOICES = "https://www.modelscope.cn/models/aihobbyist/GPT-SoVITS_Model_Collection/resolve";
const G2PW_OFFICIAL = "https://huggingface.co/XXXXRT/GPT-SoVITS-Pretrained/resolve/main/G2PWModel.zip";

function url(base, file) {
  return `${base}/${String(file).split("/").map(encodeURIComponent).join("/")}`;
}

function staticFile(path, size, sha256, source = BASE, remotePath = path, officialUrl = "") {
  const chinaUrl = url(source, remotePath);
  const officialPath = path.startsWith("pretrained_models/")
    ? path.slice("pretrained_models/".length)
    : path;
  const upstreamUrl = officialUrl || (source === BASE ? url(BASE_OFFICIAL, officialPath) : chinaUrl);
  return { path, size, sha256, url: chinaUrl, urls: { china: chinaUrl, official: upstreamUrl } };
}

function readVoiceCatalog() {
  const catalogPath = path.join(__dirname, "assets", "gpt-sovits-voices.json");
  const document = JSON.parse(fs.readFileSync(catalogPath, "utf8"));
  if (!Array.isArray(document.voices) || !document.voices.length) throw new Error("GPT-SoVITS 音色目录为空");
  return document.voices;
}

const GPT_SOVITS_VOICES = readVoiceCatalog().map((voice) => ({
  id: voice.id,
  label: voice.label,
  character: voice.character,
  franchise: voice.franchise,
  family: voice.family,
  releaseYear: voice.release_year,
  engine: `GPT-SoVITS ${voice.family === "v4" ? "V4" : "V2ProPlus"}`,
  componentId: voice.component_id,
  modelDirectory: `voices/${voice.id}`,
  estimatedBytes: voice.download.size,
  sourceUrl: voice.download.type === "lora-with-reference"
    ? "https://huggingface.co/AyerElysia/elysia-gpt-sovits-lora-v4"
    : `https://modelscope.cn/models/aihobbyist/GPT-SoVITS_Model_Collection/file/view/${voice.download.path}?status=1`,
  verified: true,
}));

function voiceArchiveFiles(voice) {
  const download = voice.download;
  if (download.type === "lora-with-reference") {
    const reference = download.reference;
    const referenceUrl = url(`${VOICES}/${reference.revision}`, reference.path);
    return [
      {
        path: `archives/${voice.id}-reference.zip`,
        size: reference.size,
        sha256: reference.sha256,
        url: referenceUrl,
        urls: { china: referenceUrl, official: referenceUrl },
      },
      {
        path: `archives/${voice.id}-lora.tar.gz`,
        size: download.lora.size,
        sha256: download.lora.sha256,
        url: download.lora.url,
        urls: { china: download.lora.url, official: download.lora.url },
      },
    ];
  }
  const archiveUrl = url(`${VOICES}/${download.revision}`, download.path);
  return [{
    path: `archives/${voice.id}.zip`,
    size: download.size,
    sha256: download.sha256,
    url: archiveUrl,
    urls: { china: archiveUrl, official: archiveUrl },
  }];
}

function voiceArchiveRules(voice) {
  const destination = `voices/${voice.id}`;
  const reference = {
    root: "reference_audios/中文/emotions",
    prefer: "【默认】",
    destination: "reference.wav",
  };
  if (voice.download.type === "lora-with-reference") {
    return [
      {
        source: `archives/${voice.id}-reference.zip`,
        root: voice.download.reference.archive_root,
        destination,
        encoding: "gbk",
        reference,
        remove: true,
      },
      {
        source: `archives/${voice.id}-lora.tar.gz`,
        root: ".",
        destination,
        type: "tar.gz",
        remove: true,
      },
    ];
  }
  return [{
    source: `archives/${voice.id}.zip`,
    root: voice.download.archive_root,
    destination,
    encoding: "gbk",
    reference,
    remove: true,
  }];
}

function voiceComponent(voice) {
  return {
    id: voice.component_id,
    name: voice.label,
    description: `${voice.franchise} · ${voice.character} · ${voice.release_year} 年归档核验`,
    provider: "static",
    category: "voice",
    target: "assets/models/tts/gpt-sovits/runtime",
    required: [
      voice.gpt_weight.startsWith("../")
        ? path.posix.normalize(`voices/${voice.id}/${voice.gpt_weight}`)
        : `voices/${voice.id}/${voice.gpt_weight}`,
      `voices/${voice.id}/${voice.sovits_weight}`,
      `voices/${voice.id}/reference.wav`,
    ],
    estimatedBytes: voice.download.size,
    dependencies: ["gpt-sovits-runtime"],
    optional: true,
    hardware: "nvidia",
    files: voiceArchiveFiles(voice),
    archives: voiceArchiveRules(voice),
  };
}

const GPT_SOVITS_COMPONENTS = [
  {
    id: "gpt-sovits-ffmpeg",
    name: "GPT-SoVITS 音频工具",
    description: "独立 FFmpeg 8.1.2；不读取或修改系统 PATH",
    provider: "static",
    category: "capability",
    target: ".tools/ffmpeg/8.1.2",
    required: ["ffmpeg.exe", "ffprobe.exe"],
    estimatedBytes: 109_728_040,
    optional: true,
    files: [{
      path: "archives/ffmpeg-8.1.2-essentials_build.zip",
      size: 109_728_040,
      sha256: "db580001caa24ac104c8cb856cd113a87b0a443f7bdf47d8c12b1d740584a2ec",
      url: "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip",
      urls: {
        china: "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip",
        official: "https://www.gyan.dev/ffmpeg/builds/packages/ffmpeg-8.1.2-essentials_build.zip",
      },
    }],
    archives: [{ source: "archives/ffmpeg-8.1.2-essentials_build.zip", root: "ffmpeg-8.1.2-essentials_build/bin", destination: ".", remove: true }],
  },
  {
    id: "gpt-sovits-v4-base",
    name: "GPT-SoVITS 基础模型",
    description: "V4/V2ProPlus 公共声学、文本与字音模型",
    provider: "static",
    category: "capability",
    target: "assets/models/tts/gpt-sovits/runtime/GPT_SoVITS",
    required: [
      "pretrained_models/chinese-hubert-base/config.json",
      "pretrained_models/chinese-hubert-base/preprocessor_config.json",
      "pretrained_models/chinese-hubert-base/pytorch_model.bin",
      "pretrained_models/chinese-roberta-wwm-ext-large/config.json",
      "pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin",
      "pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json",
      "pretrained_models/s1v3.ckpt",
      "pretrained_models/gsv-v4-pretrained/s2Gv4.pth",
      "pretrained_models/gsv-v4-pretrained/vocoder.pth",
      "pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt",
      "text/G2PWModel/g2pW.onnx",
    ],
    estimatedBytes: 2_518_784_989,
    optional: true,
    hardware: "nvidia",
    files: [
      staticFile("pretrained_models/chinese-hubert-base/config.json", 1_449, "c3e5060a1277e0f078cc6be9da4528a605dba6ece93018981fe2c820e5c7b103"),
      staticFile("pretrained_models/chinese-hubert-base/preprocessor_config.json", 212, "dcd684124d06722947939d41ea6ae58dbf10968c60a11a29f23ddc602c64a29b"),
      staticFile("pretrained_models/chinese-hubert-base/pytorch_model.bin", 188_811_417, "24164f129c66499d1346e2aa55f183250c223161ec2770c0da3d3b08cf432d3c"),
      staticFile("pretrained_models/chinese-roberta-wwm-ext-large/config.json", 963, "3d57de2fd7e80d0e5c8ff194f0bbb6baa10df7e43fc262a0cc71298a78b0a3e5"),
      staticFile("pretrained_models/chinese-roberta-wwm-ext-large/pytorch_model.bin", 651_225_145, "e53a693acc59ace251d143d068096ae0d7b79e4b1b503fa84c9dcf576448c1d8"),
      staticFile("pretrained_models/chinese-roberta-wwm-ext-large/tokenizer.json", 268_962, "173796956820ea27bd14f76bf28162607ff4254807e2948253eb5b46f5bb643b"),
      staticFile("pretrained_models/s1v3.ckpt", 155_284_856, "87133414860ea14ff6620c483a3db5ed07b44be42e2c3fcdad65523a729a745a"),
      staticFile("pretrained_models/gsv-v4-pretrained/s2Gv4.pth", 769_025_545, "906fe22f48c3e037a389df291d4d32a9414e15dbb8f9628643e83aaced109ea4"),
      staticFile("pretrained_models/gsv-v4-pretrained/vocoder.pth", 57_781_109, "4d611913df7b12d49e8976c944558d2d096816365edfc6c35a9e85b67dd14ed9"),
      staticFile("pretrained_models/sv/pretrained_eres2netv2w24s4ep4.ckpt", 107_528_697, "4f5a0bf73c61eb41b174e1bb54e7ee3c83233892be8e0af1f187024e8e581a35"),
      staticFile("archives/G2PWModel.zip", 588_856_634, "46292be0374a49308069233cd5c147ae4c41806558e4781a2467a31a4d8099da", BASE, "G2PWModel.zip", G2PW_OFFICIAL),
    ],
    archives: [{ source: "archives/G2PWModel.zip", root: "G2PWModel", destination: "text/G2PWModel", remove: true }],
  },
  {
    id: "gpt-sovits-runtime",
    name: "GPT-SoVITS CUDA 运行时",
    description: "独立推理环境；只复用已验证的 ASR CUDA Torch 文件",
    provider: "installer",
    category: "capability",
    target: ".venv-gpt-sovits",
    required: ["Scripts/python.exe", "ready.json"],
    estimatedBytes: 1_200_000_000,
    displayEstimatedBytes: false,
    installScript: "scripts/prepare-gpt-sovits.ps1",
    installArgs: [],
    dependencies: ["asr-runtime", "gpt-sovits-v4-base", "gpt-sovits-ffmpeg"],
    optional: true,
    hardware: "nvidia",
  },
  ...readVoiceCatalog().map(voiceComponent),
];

module.exports = { GPT_SOVITS_COMPONENTS, GPT_SOVITS_VOICES };
