const DEFAULT_WIDTH = 336;
const DEFAULT_HEIGHT = 576;

function clamp(value, minimum, maximum, fallback) {
  const number = Number(value);
  return Number.isFinite(number) ? Math.max(minimum, Math.min(maximum, Math.round(number))) : fallback;
}

function normalizeCompanionConfig(value = {}) {
  return {
    enabled: value.enabled !== false,
    // v2 makes the companion draggable by default. Older persisted configs may
    // have click-through enabled from the prototype and must not keep the
    // window permanently unreachable.
    clickThrough: value.behaviorVersion === 2 && value.clickThrough === true,
    behaviorVersion: 2,
    width: clamp(value.width, 260, 720, DEFAULT_WIDTH),
    height: clamp(value.height, 360, 1000, DEFAULT_HEIGHT),
    x: Number.isFinite(Number(value.x)) ? Math.round(Number(value.x)) : null,
    y: Number.isFinite(Number(value.y)) ? Math.round(Number(value.y)) : null,
  };
}

function companionBoundsForDisplay(value, workArea) {
  const config = normalizeCompanionConfig(value);
  const width = Math.min(config.width, workArea.width);
  const height = Math.min(config.height, workArea.height);
  const defaultX = workArea.x + workArea.width - width - 24;
  const defaultY = workArea.y + workArea.height - height - 24;
  return {
    x: clamp(config.x, workArea.x, workArea.x + workArea.width - width, defaultX),
    y: clamp(config.y, workArea.y, workArea.y + workArea.height - height, defaultY),
    width,
    height,
  };
}

module.exports = { DEFAULT_WIDTH, DEFAULT_HEIGHT, normalizeCompanionConfig, companionBoundsForDisplay };
