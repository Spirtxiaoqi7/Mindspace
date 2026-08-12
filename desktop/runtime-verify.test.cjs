const assert = require("node:assert/strict");
const fs = require("node:fs");
const path = require("node:path");
const test = require("node:test");

test("runtime verification does not require a plaintext LLM key in settings", () => {
  const script = fs.readFileSync(path.join(__dirname, "..", "scripts", "runtime-verify.ps1"), "utf8");
  assert.match(script, /\$env:MINDSPACE_LLM_API_KEY/);
  assert.match(script, /LLM_CREDENTIALS=injected-by-desktop/);
  assert.match(script, /跳过 API 凭据验证/);
  assert.doesNotMatch(script, /\$settings\.llm\.api_key/);
});
