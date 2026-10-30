# Neddle relay

A small Cloudflare Worker that gives Claude a public address for your phone.

```
Claude (cloud)  ──HTTPS──▶  Worker /mcp/<token>  ──WebSocket──▶  Neddle app on your phone
```

Claude connects to custom connectors from Anthropic's cloud, so it cannot reach a server running on your phone.
The phone connects **outward** to this Worker instead, and the Worker passes Claude's tool calls down that connection.

It runs on Cloudflare's free plan (100,000 requests/day for Workers and for Durable Objects).

## One-time setup

1. **Create a free Cloudflare account** at <https://dash.cloudflare.com/sign-up>.
2. **Pick your `workers.dev` name.** In the dashboard open *Workers & Pages* once. If it asks you to choose a subdomain, choose one.
   The deploy cannot do this for you.
3. **Create an API token.** *My Profile → API Tokens → Create Token →* template **Edit Cloudflare Workers**.
   Use your account under *Account Resources* and *All zones* under *Zone Resources*. Copy the token.
4. **Copy your Account ID** from the *Workers & Pages* overview page (right-hand side).
5. **Invent a secret token**: a long random string (40+ characters; a password manager can generate one).
6. **Add three GitHub secrets** in this repo: *Settings → Secrets and variables → Actions → New repository secret*:

   | Name | Value |
   | --- | --- |
   | `CLOUDFLARE_API_TOKEN` | the token from step 3 |
   | `CLOUDFLARE_ACCOUNT_ID` | the ID from step 4 |
   | `NEDDLE_TOKEN` | the secret from step 5 |

7. **Deploy:** *Actions → Deploy Neddle Relay → Run workflow.* When it finishes, the run summary shows your relay address,
   like `https://neddle-relay.<your-name>.workers.dev`.
8. **Add the connector in Claude** (from claude.ai in a browser): *Customize → Connectors → Add custom connector*, and use
   `https://neddle-relay.<your-name>.workers.dev/mcp/<your NEDDLE_TOKEN>`. No sign-in is needed.
   The free plan allows one custom connector.
9. **Put the same relay address and token into the Neddle app** on your phone and tap Connect.

The connector URL contains your token, so treat it like a password. To rotate it, change the `NEDDLE_TOKEN` secret,
re-run the deploy, and update the connector and the phone app.

## Checking it works

- `https://<relay address>/health` should answer `{"ok":true,...}`.
- `https://<relay address>/status/<token>` says whether your phone is connected.
- In Claude, ask it to run `get_device_status`.

## Developing

```bash
npm install
npm test        # starts the Worker locally and plays both Claude and the phone
npm run dev     # local server on http://localhost:8787
```

## Protocol between the Worker and the phone

The phone opens `wss://<relay>/phone` with `Authorization: Bearer <token>` and sends the text `ping` every ~25 s (the Worker answers `pong`).

- Worker → phone: `{"type":"call","id":"…","tool":"launch_app","args":{…}}`
- Phone → Worker: `{"type":"result","id":"…","ok":true,"content":[{"type":"text","text":"…"}]}`
  or `{"type":"result","id":"…","ok":false,"error":"…"}`.
  Content items may be `text` or `image` (`data` base64 + `mimeType`). Keep each message under about 1 MB (downscale screenshots).
