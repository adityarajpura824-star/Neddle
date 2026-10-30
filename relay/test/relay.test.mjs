// End-to-end test: runs the relay under `wrangler dev` (local workerd) and plays both
// roles: "Claude" (HTTP MCP client) and "the phone" (WebSocket client).
import assert from "node:assert/strict";
import { spawn } from "node:child_process";
import { fileURLToPath } from "node:url";
import path from "node:path";
import WebSocket from "ws";

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");
const PORT = 8799;
const BASE = `http://127.0.0.1:${PORT}`;
const WS_BASE = `ws://127.0.0.1:${PORT}`;
const TOKEN = "test-secret-token-1234567890";

let server;
let failures = 0;

// Fail loudly instead of hanging forever if something stalls.
setTimeout(() => {
  console.log("\nFAIL watchdog: tests did not finish within 120s");
  stopServer();
  process.exit(2);
}, 120_000).unref();

async function startServer() {
  server = spawn(
    "npx",
    ["wrangler", "dev", "--local", "--port", String(PORT), "--var", `NEDDLE_TOKEN:${TOKEN}`, "--var", "CALL_TIMEOUT_MS:1500"],
    { cwd: ROOT, env: { ...process.env, CI: "1", WRANGLER_SEND_METRICS: "false" }, detached: true, stdio: ["ignore", "pipe", "pipe"] },
  );
  let log = "";
  server.stdout.on("data", (d) => (log += d));
  server.stderr.on("data", (d) => (log += d));
  for (let i = 0; i < 120; i++) {
    try {
      if ((await fetch(`${BASE}/health`)).ok) return;
    } catch {
      // not up yet
    }
    await new Promise((r) => setTimeout(r, 500));
  }
  throw new Error("wrangler dev did not start:\n" + log.slice(-2000));
}

function stopServer() {
  if (server?.pid) {
    try {
      process.kill(-server.pid, "SIGKILL");
    } catch {
      // already gone
    }
  }
}

const rpc = (body, headers = { authorization: `Bearer ${TOKEN}` }, url = `${BASE}/mcp`) =>
  fetch(url, { method: "POST", headers: { "content-type": "application/json", ...headers }, body: JSON.stringify(body) });

async function call(name, args, id = 1) {
  const res = await rpc({ jsonrpc: "2.0", id, method: "tools/call", params: { name, arguments: args } });
  assert.equal(res.status, 200);
  return res.json();
}

function connectPhone(token = TOKEN) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(`${WS_BASE}/phone`, { headers: { authorization: `Bearer ${token}` } });
    ws.once("open", () => resolve(ws));
    ws.once("unexpected-response", (_req, res) => reject(Object.assign(new Error("rejected"), { status: res.statusCode })));
    ws.once("error", reject);
  });
}

// A fake phone: answers calls using `handler(msg)`; return null to stay silent.
function answerCalls(ws, handler) {
  ws.on("message", (raw) => {
    const text = raw.toString();
    if (text === "pong") return;
    const msg = JSON.parse(text);
    if (msg.type !== "call") return;
    const reply = handler(msg);
    if (reply) ws.send(JSON.stringify({ type: "result", id: msg.id, ...reply }));
  });
}

const closed = (ws) => new Promise((resolve) => ws.once("close", (code) => resolve(code)));

async function test(name, fn) {
  try {
    await fn();
    console.log(`PASS ${name}`);
  } catch (err) {
    failures++;
    console.log(`FAIL ${name}\n     ${err.message.split("\n").join("\n     ")}`);
  }
}

try {
  await startServer();

  await test("health endpoint is open", async () => {
    const res = await fetch(`${BASE}/health`);
    assert.deepEqual(await res.json(), { ok: true, service: "neddle-relay" });
  });

  await test("MCP rejects missing and wrong tokens", async () => {
    assert.equal((await rpc({ jsonrpc: "2.0", id: 1, method: "ping" }, {})).status, 401);
    assert.equal((await rpc({ jsonrpc: "2.0", id: 1, method: "ping" }, { authorization: "Bearer nope" })).status, 401);
    assert.equal((await fetch(`${BASE}/mcp/wrong-token`, { method: "POST", body: "{}" })).status, 401);
  });

  await test("initialize echoes a supported protocol version", async () => {
    const res = await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2025-03-26" } });
    const body = await res.json();
    assert.equal(body.result.protocolVersion, "2025-03-26");
    assert.equal(body.result.serverInfo.name, "neddle-relay");
    assert.ok(body.result.capabilities.tools);
  });

  await test("initialize falls back for an unknown protocol version", async () => {
    const body = await (await rpc({ jsonrpc: "2.0", id: 1, method: "initialize", params: { protocolVersion: "2099-01-01" } })).json();
    assert.equal(body.result.protocolVersion, "2025-06-18");
  });

  await test("notifications get 202 with no body", async () => {
    const res = await rpc({ jsonrpc: "2.0", method: "notifications/initialized" });
    assert.equal(res.status, 202);
    assert.equal(await res.text(), "");
  });

  await test("tools/list returns all tools, with bearer or path token", async () => {
    const want = ["get_device_status", "launch_app", "install_apk", "inspect_ui_state", "capture_screenshot", "check_app_logs"];
    for (const [headers, url] of [
      [{ authorization: `Bearer ${TOKEN}` }, `${BASE}/mcp`],
      [{}, `${BASE}/mcp/${TOKEN}`],
    ]) {
      const body = await (await rpc({ jsonrpc: "2.0", id: 2, method: "tools/list" }, headers, url)).json();
      assert.deepEqual(body.result.tools.map((t) => t.name), want);
      for (const t of body.result.tools) assert.equal(t.inputSchema.type, "object");
    }
  });

  await test("unknown method and GET /mcp", async () => {
    const body = await (await rpc({ jsonrpc: "2.0", id: 3, method: "nope/nope" })).json();
    assert.equal(body.error.code, -32601);
    const get = await fetch(`${BASE}/mcp`, { headers: { authorization: `Bearer ${TOKEN}` } });
    assert.equal(get.status, 405);
  });

  await test("malformed JSON gives a parse error", async () => {
    const res = await fetch(`${BASE}/mcp`, { method: "POST", headers: { authorization: `Bearer ${TOKEN}` }, body: "{not json" });
    assert.equal(res.status, 400);
    assert.equal((await res.json()).error.code, -32700);
  });

  await test("tool call while the phone is offline explains what to do", async () => {
    const body = await call("launch_app", { package_name: "com.example.app" });
    assert.equal(body.result.isError, true);
    assert.match(body.result.content[0].text, /offline/i);
    assert.match(body.result.content[0].text, /Neddle app/);
  });

  await test("status reports phone offline", async () => {
    const res = await fetch(`${BASE}/status/${TOKEN}`);
    assert.deepEqual(await res.json(), { phone_online: false });
  });

  await test("phone connection needs the token", async () => {
    await assert.rejects(connectPhone("wrong"), (e) => e.status === 401);
  });

  await test("validation: unknown tool and missing argument", async () => {
    const unknown = await call("format_disk", {});
    assert.equal(unknown.error.code, -32602);
    const missing = await call("launch_app", {});
    assert.equal(missing.result.isError, true);
    assert.match(missing.result.content[0].text, /package_name/);
  });

  await test("full round trip: Claude -> relay -> phone -> Claude", async () => {
    const phone = await connectPhone();
    let seen;
    answerCalls(phone, (msg) => {
      seen = msg;
      return { ok: true, content: [{ type: "text", text: "Launched com.example.app" }] };
    });
    const body = await call("launch_app", { package_name: "com.example.app" });
    assert.equal(seen.tool, "launch_app");
    assert.deepEqual(seen.args, { package_name: "com.example.app" });
    assert.equal(body.result.isError, false);
    assert.equal(body.result.content[0].text, "Launched com.example.app");
    const status = await (await fetch(`${BASE}/status`, { headers: { authorization: `Bearer ${TOKEN}` } })).json();
    assert.deepEqual(status, { phone_online: true });
    phone.close();
    await closed(phone);
  });

  await test("images pass through, junk content is dropped", async () => {
    const phone = await connectPhone();
    answerCalls(phone, () => ({
      ok: true,
      content: [{ type: "image", data: "aGVsbG8=", mimeType: "image/jpeg" }, { type: "script", text: "x" }, { type: "text", text: "1080x2400" }],
    }));
    const body = await call("capture_screenshot", {});
    assert.deepEqual(body.result.content, [
      { type: "image", data: "aGVsbG8=", mimeType: "image/jpeg" },
      { type: "text", text: "1080x2400" },
    ]);
    phone.close();
    await closed(phone);
  });

  await test("phone-side error becomes an MCP tool error", async () => {
    const phone = await connectPhone();
    answerCalls(phone, () => ({ ok: false, error: "Accessibility service is not enabled" }));
    const body = await call("inspect_ui_state", {});
    assert.equal(body.result.isError, true);
    assert.equal(body.result.content[0].text, "Accessibility service is not enabled");
    phone.close();
    await closed(phone);
  });

  await test("a phone that never answers times out", async () => {
    const phone = await connectPhone();
    answerCalls(phone, () => null);
    const started = Date.now();
    const body = await call("get_device_status", {});
    assert.equal(body.result.isError, true);
    assert.match(body.result.content[0].text, /did not answer/);
    assert.ok(Date.now() - started >= 1400, "should wait for the configured timeout");
    phone.close();
    await closed(phone);
  });

  await test("a phone that disconnects mid-call fails fast", async () => {
    const phone = await connectPhone();
    const gone = closed(phone); // register before the call so the event cannot be missed
    phone.on("message", () => phone.close());
    const started = Date.now();
    const body = await call("get_device_status", {});
    assert.equal(body.result.isError, true);
    assert.match(body.result.content[0].text, /offline/i);
    assert.ok(Date.now() - started < 1400, "should not wait for the timeout");
    await gone;
  });

  await test("a newer phone connection replaces the old one", async () => {
    const first = await connectPhone();
    const firstClosed = closed(first);
    const second = await connectPhone();
    assert.equal(await firstClosed, 4000);
    answerCalls(second, () => ({ ok: true, content: [{ type: "text", text: "from second" }] }));
    const body = await call("get_device_status", {});
    assert.equal(body.result.content[0].text, "from second");
    second.close();
    await closed(second);
  });

  await test("text ping gets pong (keep-alive)", async () => {
    const phone = await connectPhone();
    const reply = new Promise((resolve) => phone.once("message", (m) => resolve(m.toString())));
    phone.send("ping");
    assert.equal(await reply, "pong");
    phone.close();
    await closed(phone);
  });

  await test("batch requests are answered in one array", async () => {
    const res = await rpc([
      { jsonrpc: "2.0", id: 10, method: "ping" },
      { jsonrpc: "2.0", method: "notifications/initialized" },
      { jsonrpc: "2.0", id: 11, method: "ping" },
    ]);
    const body = await res.json();
    assert.deepEqual(body.map((r) => r.id), [10, 11]);
  });
} finally {
  stopServer();
}

console.log(failures ? `\n${failures} test(s) FAILED` : "\nAll relay tests passed");
process.exit(failures ? 1 : 0);
