import { TOOLS } from "./tools.js";
import { PhoneHub } from "./hub.js";

export { PhoneHub };

const PROTOCOL_VERSIONS = ["2025-06-18", "2025-03-26", "2024-11-05"];
const SERVER_INFO = { name: "neddle-relay", version: "1.0.0" };
const INSTRUCTIONS =
  "These tools control the user's Android phone through the Neddle companion app. " +
  "If a tool says the phone is offline, ask the user to open the Neddle app and tap Connect.";

const json = (body, status = 200, headers = {}) =>
  new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json", ...headers } });

const rpcResult = (id, result) => ({ jsonrpc: "2.0", id, result });
const rpcError = (id, code, message) => ({ jsonrpc: "2.0", id, error: { code, message } });
const toolError = (text) => ({ content: [{ type: "text", text }], isError: true });

// ---------- auth ----------

async function sha256(text) {
  return new Uint8Array(await crypto.subtle.digest("SHA-256", new TextEncoder().encode(text)));
}

// Hash both sides first so the comparison is constant-time and length-independent.
async function tokenMatches(candidate, secret) {
  if (!candidate || !secret) return false;
  const [a, b] = await Promise.all([sha256(candidate), sha256(secret)]);
  return crypto.subtle.timingSafeEqual(a, b);
}

// The token may come as "Authorization: Bearer <token>", as the last URL segment
// (/mcp/<token>, handy when the connector UI has no header field), or as ?token=.
function extractToken(request, url) {
  const bearer = (request.headers.get("authorization") || "").match(/^Bearer\s+(.+)$/i);
  if (bearer) return bearer[1].trim();
  const segment = url.pathname.match(/^\/(?:mcp|phone|status)\/([^/]+)$/);
  if (segment) {
    try {
      return decodeURIComponent(segment[1]);
    } catch {
      return null;
    }
  }
  return url.searchParams.get("token");
}

// ---------- MCP (stateless Streamable HTTP, JSON responses) ----------

function normalizeContent(content) {
  const items = Array.isArray(content) ? content : [];
  const clean = items.flatMap((c) => {
    if (c?.type === "text" && typeof c.text === "string") return [{ type: "text", text: c.text }];
    if (c?.type === "image" && typeof c.data === "string" && typeof c.mimeType === "string") {
      return [{ type: "image", data: c.data, mimeType: c.mimeType }];
    }
    return [];
  });
  return clean.length ? clean : [{ type: "text", text: "(no output)" }];
}

function toolResultFromHub(data) {
  switch (data?.status) {
    case "ok":
      return { content: normalizeContent(data.content), isError: false };
    case "error":
      return toolError(data.message);
    case "offline":
      return toolError("Your phone is offline. Open the Neddle app and tap Connect, then try again.");
    case "timeout":
      return toolError("The phone did not answer in time. Make sure the Neddle app is open and the screen is on.");
    default:
      return toolError("Unexpected response from the relay.");
  }
}

async function callTool(msg, hub) {
  const { name, arguments: args } = msg.params ?? {};
  const tool = TOOLS.find((t) => t.name === name);
  if (!tool) return rpcError(msg.id, -32602, `Unknown tool: ${name}`);

  const missing = (tool.inputSchema.required ?? []).filter((key) => args?.[key] === undefined);
  if (missing.length) return rpcResult(msg.id, toolError(`Missing required argument(s): ${missing.join(", ")}`));

  const response = await hub.fetch("https://hub.internal/call", {
    method: "POST",
    body: JSON.stringify({ tool: name, args: args ?? {} }),
  });
  return rpcResult(msg.id, toolResultFromHub(await response.json()));
}

async function handleMessage(msg, hub) {
  if (!msg || msg.jsonrpc !== "2.0" || typeof msg.method !== "string") {
    if (msg && ("result" in msg || "error" in msg)) return null; // a client response; nothing to do
    return rpcError(msg?.id ?? null, -32600, "Invalid Request");
  }
  switch (msg.method) {
    case "initialize": {
      const requested = msg.params?.protocolVersion;
      return rpcResult(msg.id, {
        protocolVersion: PROTOCOL_VERSIONS.includes(requested) ? requested : PROTOCOL_VERSIONS[0],
        capabilities: { tools: { listChanged: false } },
        serverInfo: SERVER_INFO,
        instructions: INSTRUCTIONS,
      });
    }
    case "ping":
      return rpcResult(msg.id, {});
    case "tools/list":
      return rpcResult(msg.id, { tools: TOOLS });
    case "tools/call":
      return callTool(msg, hub);
    default:
      if (msg.id === undefined) return null; // notification such as notifications/initialized
      return rpcError(msg.id, -32601, `Method not found: ${msg.method}`);
  }
}

async function handleMcp(request, hub) {
  if (request.method !== "POST") {
    // No server-initiated SSE stream and no sessions to terminate.
    return new Response(null, { status: 405, headers: { allow: "POST" } });
  }
  let body;
  try {
    body = await request.json();
  } catch {
    return json(rpcError(null, -32700, "Parse error"), 400);
  }
  const isBatch = Array.isArray(body);
  const replies = [];
  for (const msg of isBatch ? body : [body]) {
    const reply = await handleMessage(msg, hub);
    if (reply) replies.push(reply);
  }
  if (replies.length === 0) return new Response(null, { status: 202 });
  return json(isBatch ? replies : replies[0]);
}

// ---------- routing ----------

export default {
  async fetch(request, env) {
    const url = new URL(request.url);
    const path = url.pathname.replace(/\/+$/, "") || "/";

    if (path === "/" || path === "/health") return json({ ok: true, service: SERVER_INFO.name });

    const area = ["mcp", "phone", "status"].find((a) => path === `/${a}` || path.startsWith(`/${a}/`));
    if (!area) return json({ error: "not found" }, 404);

    if (!env.NEDDLE_TOKEN) return json({ error: "relay is not configured: the NEDDLE_TOKEN secret is missing" }, 500);
    if (!(await tokenMatches(extractToken(request, url), env.NEDDLE_TOKEN))) {
      return json({ error: "unauthorized" }, 401, { "www-authenticate": 'Bearer realm="neddle"' });
    }

    const hub = env.PHONE_HUB.get(env.PHONE_HUB.idFromName("phone"));
    if (area === "phone") return hub.fetch(request);
    if (area === "status") return hub.fetch("https://hub.internal/status");
    return handleMcp(request, hub);
  },
};
