const fs = require("node:fs");
const path = require("node:path");

const SERVICE_META = Object.freeze({
  core: { environment: "MINDSPACE_PORT", healthPath: "/api/v1/health" },
  asr: { environment: "MINDSPACE_ASR_PORT", healthPath: "/health" },
  tts: { environment: "MINDSPACE_TTS_PORT", healthPath: "/health" },
  qwen: { environment: "MINDSPACE_QWEN3_PORT", healthPath: "/health" },
});

function validPort(value, label) {
  const port = Number(value);
  if (!Number.isInteger(port) || port < 1 || port > 65535) throw new Error(`Invalid ${label} port: ${value}`);
  return port;
}

function resolvePortConfigPath({ packaged = false, resourcesPath = "", dirname = __dirname } = {}) {
  return packaged
    ? path.join(resourcesPath, "config", "service-ports.json")
    : path.resolve(dirname, "..", "config", "service-ports.json");
}

function loadServicePorts({ configPath, environment = process.env } = {}) {
  if (!configPath || !fs.existsSync(configPath)) throw new Error(`Mindspace service port registry is missing: ${configPath}`);
  const document = JSON.parse(fs.readFileSync(configPath, "utf8"));
  if (document.schema_version !== "1.0.0" || document.host !== "127.0.0.1") {
    throw new Error("Mindspace service port registry schema or host is invalid");
  }
  const services = {};
  for (const [name, meta] of Object.entries(SERVICE_META)) {
    const source = environment[meta.environment] || document.services?.[name];
    const port = validPort(source, name);
    services[name] = Object.freeze({
      port,
      environment: meta.environment,
      origin: `http://127.0.0.1:${port}`,
      health: `http://127.0.0.1:${port}${meta.healthPath}`,
    });
  }
  if (new Set(Object.values(services).map((item) => item.port)).size !== Object.keys(services).length) {
    throw new Error("Mindspace service ports must be unique");
  }
  return Object.freeze({ configPath: path.resolve(configPath), host: document.host, services: Object.freeze(services) });
}

function environmentForPorts(registry) {
  return Object.fromEntries(Object.values(registry.services).map((service) => [service.environment, String(service.port)]));
}

module.exports = { environmentForPorts, loadServicePorts, resolvePortConfigPath, validPort };
