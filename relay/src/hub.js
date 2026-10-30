import { DurableObject } from "cloudflare:workers";

const DEFAULT_CALL_TIMEOUT_MS = 40_000;

/**
 * One instance holds the phone's WebSocket. The phone connects outward to the
 * Worker, so it needs no public address. Claude's tool calls arrive over HTTP,
 * get forwarded down the socket, and the phone's answer resolves the request.
 *
 * Uses the WebSocket Hibernation API so an idle, connected phone costs nothing.
 */
export class PhoneHub extends DurableObject {
  constructor(ctx, env) {
    super(ctx, env);
    this.pending = new Map(); // call id -> { resolve, timer, connId }
    // The phone sends the text "ping" to keep the connection alive; answering it
    // here does not wake the object.
    this.ctx.setWebSocketAutoResponse(new WebSocketRequestResponsePair("ping", "pong"));
  }

  async fetch(request) {
    if (request.headers.get("Upgrade") === "websocket") return this.acceptPhone();

    const url = new URL(request.url);
    if (request.method === "POST" && url.pathname === "/call") {
      return this.relayCall(await request.json());
    }
    if (request.method === "GET" && url.pathname === "/status") {
      return Response.json({ phone_online: this.ctx.getWebSockets().length > 0 });
    }
    return new Response("not found", { status: 404 });
  }

  acceptPhone() {
    // Only one phone at a time: the newest connection wins.
    for (const old of this.ctx.getWebSockets()) {
      try {
        old.close(4000, "replaced by a newer connection");
      } catch {
        // already closing
      }
    }
    const { 0: client, 1: server } = new WebSocketPair();
    this.ctx.acceptWebSocket(server, [crypto.randomUUID()]);
    return new Response(null, { status: 101, webSocket: client });
  }

  async relayCall({ tool, args }) {
    const sockets = this.ctx.getWebSockets();
    const ws = sockets[sockets.length - 1];
    if (!ws) return Response.json({ status: "offline" });

    const id = crypto.randomUUID();
    const connId = this.ctx.getTags(ws)[0];
    const timeoutMs = Number(this.env.CALL_TIMEOUT_MS) || DEFAULT_CALL_TIMEOUT_MS;

    const outcome = new Promise((resolve) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        resolve({ status: "timeout" });
      }, timeoutMs);
      this.pending.set(id, { resolve, timer, connId });
    });

    try {
      ws.send(JSON.stringify({ type: "call", id, tool, args }));
    } catch {
      this.settle(id, { status: "offline" });
    }
    return Response.json(await outcome);
  }

  settle(id, result) {
    const entry = this.pending.get(id);
    if (!entry) return;
    clearTimeout(entry.timer);
    this.pending.delete(id);
    entry.resolve(result);
  }

  failPending(connId) {
    for (const [id, entry] of this.pending) {
      if (entry.connId === connId) this.settle(id, { status: "offline" });
    }
  }

  async webSocketMessage(ws, message) {
    if (typeof message !== "string") return;
    let msg;
    try {
      msg = JSON.parse(message);
    } catch {
      return;
    }
    if (msg?.type === "result" && typeof msg.id === "string") {
      this.settle(
        msg.id,
        msg.ok
          ? { status: "ok", content: msg.content }
          : { status: "error", message: typeof msg.error === "string" ? msg.error : "The phone reported an error." },
      );
    }
  }

  async webSocketClose(ws, code, reason) {
    this.failPending(this.ctx.getTags(ws)[0]);
    try {
      ws.close(code >= 1000 && code < 1005 ? code : 1000, reason);
    } catch {
      // already closed
    }
  }

  async webSocketError(ws) {
    this.failPending(this.ctx.getTags(ws)[0]);
  }
}
