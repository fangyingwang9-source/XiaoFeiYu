/**
 * console-web host plugin: same-origin route /api/dsh/xfy.
 * Proxies to bridge-core (local HTTP in the WeChat bridge process) and
 * controls the bridge process itself (start/stop/restart) via keepalive.
 */
const fs = require("node:fs");
const path = require("node:path");
const { spawn } = require("node:child_process");

const name = "console-web";
const inject = ["webServer"];

const ROUTE = "/api/dsh/xfy";
const WORKSPACE = process.env.XFY_WORKSPACE || "E:\\useer\\deepseek-harness\\default-workspace";
const BRIDGE_DIR = path.join(WORKSPACE, "wechat-bridge");
const CORE = process.env.XFY_BRIDGE_CORE || "http://127.0.0.1:8787";
const PYTHON = process.env.XFY_PYTHON || "C:\\Users\\one\\.dsh\\dsh-runtimes\\dsh-primary-runtime\\dependencies\\python\\python.exe";
const PYTHONW = path.join(path.dirname(PYTHON), "pythonw.exe");

function sameOrigin(req) {
  const origin = req.headers.origin;
  if (!origin) return true;
  try {
    return new URL(origin).host === (req.headers.host || "");
  } catch {
    return false;
  }
}

function json(res, code, payload) {
  res.writeHead(code, { "content-type": "application/json; charset=utf-8", "cache-control": "no-store" });
  res.end(JSON.stringify(payload));
}

async function readBody(req) {
  const chunks = [];
  for await (const chunk of req) chunks.push(chunk);
  const raw = Buffer.concat(chunks).toString("utf8");
  if (!raw) return {};
  try {
    return JSON.parse(raw);
  } catch {
    return {};
  }
}

async function coreCall(pathname, body) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 30000);
  try {
    const res = await fetch(CORE + pathname, {
      method: body === undefined ? "GET" : "POST",
      headers: body === undefined ? undefined : { "content-type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal: controller.signal
    });
    return await res.json();
  } finally {
    clearTimeout(timer);
  }
}

function readPid() {
  try {
    return Number(fs.readFileSync(path.join(BRIDGE_DIR, "bridge.pid"), "utf8").trim()) || null;
  } catch {
    return null;
  }
}

function alive(pid) {
  if (!pid) return false;
  try {
    process.kill(pid, 0);
    return true;
  } catch {
    return false;
  }
}

function logTail(lines = 8) {
  try {
    const raw = fs.readFileSync(path.join(BRIDGE_DIR, "console.log"), "utf8").trimEnd().split(/\r?\n/);
    return raw.slice(-lines);
  } catch {
    return [];
  }
}

async function bridgeStatus() {
  const pid = readPid();
  const up = alive(pid);
  let core = null;
  if (up) {
    try {
      core = await coreCall("/status");
    } catch (error) {
      core = { ok: false, error: "bridge-core-unreachable", detail: String(error && error.message) };
    }
  }
  return { ok: true, bridge: { pid, alive: up }, core, log: logTail() };
}

function bridgeStart() {
  const pid = readPid();
  if (alive(pid)) return { ok: true, started: false, pid, note: "already-running" };
  const child = spawn(PYTHONW, [path.join(BRIDGE_DIR, "keepalive_runner.py")], {
    cwd: WORKSPACE,
    detached: true,
    stdio: "ignore",
    windowsHide: true
  });
  child.unref();
  return { ok: true, started: true, launcher: "keepalive_runner.py" };
}

function bridgeStop() {
  const pid = readPid();
  if (!alive(pid)) return { ok: true, stopped: false, note: "not-running" };
  try {
    spawn("schtasks", ["/End", "/TN", "DSH-Bridge-Keepalive"], { windowsHide: true, stdio: "ignore" });
  } catch {
    /* keepalive task may be absent; killing the pid is authoritative */
  }
  try {
    process.kill(pid);
    return { ok: true, stopped: true, pid };
  } catch (error) {
    return { ok: false, error: "kill-failed", detail: String(error && error.message), pid };
  }
}

function control(action, body) {
  switch (action) {
    case "status":
      return bridgeStatus();
    case "bridge.start":
      return bridgeStart();
    case "bridge.stop":
      return bridgeStop();
    case "bridge.restart":
      bridgeStop();
      return bridgeStart();
    case "tts":
      return coreCall("/tts", body);
    case "asr":
      return coreCall("/asr", body);
    case "loopback":
      return coreCall("/voice/loopback", body);
    case "config":
      return coreCall("/config");
    case "config.save":
      return coreCall("/config", body);
    default:
      return Promise.reject(Object.assign(new Error("unknown_action"), { code: 400 }));
  }
}

function makeRoute() {
  return {
    kind: "exact",
    path: ROUTE,
    async handler(req, res) {
      if (!sameOrigin(req)) {
        json(res, 403, { ok: false, error: "cross-site-request-rejected" });
        return;
      }
      const url = new URL(req.url, "http://127.0.0.1");
      const body = req.method === "POST" ? await readBody(req) : {};
      const action = body.action || url.searchParams.get("action") || "status";
      try {
        json(res, 200, await control(action, body));
      } catch (error) {
        const unknown = error && error.code === 400;
        json(res, unknown ? 400 : 502, {
          ok: false,
          error: unknown ? "unknown_action" : "bridge-core-unreachable",
          detail: String((error && error.message) || error),
          action
        });
      }
    }
  };
}

async function apply(ctx) {
  const dispose = ctx.webServer.register(makeRoute());
  ctx.effect(() => () => {
    if (typeof dispose === "function") dispose();
    else if (dispose && typeof dispose.dispose === "function") dispose.dispose();
  }, "console-web: host route");
}

exports.name = name;
exports.inject = inject;
exports.apply = apply;
