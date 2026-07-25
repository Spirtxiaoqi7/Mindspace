const { spawn } = require("node:child_process");

function appendTail(current, chunk, limit = 4096) {
  const combined = `${current}${chunk}`;
  return combined.length > limit ? combined.slice(-limit) : combined;
}

/**
 * Run a dependency check without blocking Electron's main event loop.
 *
 * ASR imports CUDA libraries and may need tens of seconds on a cold start.
 * Using spawnSync for that work freezes every Launcher click and repaint.
 */
function runProcessCheck(command, args, options = {}) {
  const timeoutMs = Math.max(1, Number(options.timeoutMs || 45_000));
  return new Promise((resolve) => {
    let stdout = "";
    let stderr = "";
    let settled = false;
    let timedOut = false;
    let child;
    let timer;

    const finish = (status, error = null) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      resolve({ status, stdout, stderr, timedOut, error });
    };

    try {
      child = spawn(command, args, {
        env: options.env || process.env,
        windowsHide: true,
        stdio: ["ignore", "pipe", "pipe"],
      });
    } catch (error) {
      resolve({ status: null, stdout, stderr, timedOut, error });
      return;
    }

    child.stdout?.on("data", (chunk) => {
      stdout = appendTail(stdout, chunk.toString());
    });
    child.stderr?.on("data", (chunk) => {
      stderr = appendTail(stderr, chunk.toString());
    });
    child.once("error", (error) => finish(null, error));
    child.once("exit", (code) => finish(code));

    timer = setTimeout(() => {
      timedOut = true;
      child.kill();
      finish(null, new Error(`Process check timed out after ${timeoutMs} ms`));
    }, timeoutMs);
  });
}

module.exports = { appendTail, runProcessCheck };
