const DEFAULT_ALLOWED_HOSTS = Object.freeze(new Set([
  "douyinqijun.cn",
  "www.douyinqijun.cn",
  "modelscope.cn",
  "www.modelscope.cn",
  "huggingface.co",
  "platform.deepseek.com",
  "api-docs.deepseek.com",
  "cloud.siliconflow.cn",
  "docs.siliconflow.cn",
]));

function classifyExternalUrl(rawUrl, allowedHosts = DEFAULT_ALLOWED_HOSTS) {
  let target;
  try { target = new URL(String(rawUrl || "")); } catch { return { action: "deny", reason: "invalid-url" }; }
  if (target.username || target.password) return { action: "deny", reason: "embedded-credentials" };
  if (target.protocol !== "https:") return { action: "deny", reason: "protocol" };
  target.hash = "";
  const url = target.toString();
  return allowedHosts.has(target.hostname.toLowerCase())
    ? { action: "allow", url, host: target.hostname }
    : { action: "confirm", url, host: target.hostname };
}

module.exports = { DEFAULT_ALLOWED_HOSTS, classifyExternalUrl };
