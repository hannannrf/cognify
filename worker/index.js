// Cognify AI proxy (Cloudflare Workers free tier). Keeps the Gemini key secret and caps each visitor's daily
// calls, so the hosted app works without a key and nobody can drain the shared free quota.
import { DurableObject } from "cloudflare:workers";

const MODEL = "gemini-flash-lite-latest";
const MAX_PROMPT_CHARS = 16000; // largest real prompt (8 sources + instructions) is about 10k

export default {
  async fetch(req, env) {
    const origin = req.headers.get("Origin") ?? "";
    const allowed = env.ALLOWED_ORIGINS.split(",").includes(origin);
    const cors = allowed
      ? { "Access-Control-Allow-Origin": origin, "Access-Control-Allow-Methods": "POST", "Access-Control-Allow-Headers": "Content-Type", Vary: "Origin" }
      : {};
    const reply = (status, body) => Response.json(body, { status, headers: cors });

    if (new URL(req.url).pathname !== "/api/ai") return reply(404, { error: "Not found" });
    if (req.method === "OPTIONS") return new Response(null, { status: allowed ? 204 : 403, headers: cors });
    if (!allowed) return reply(403, { error: "Origin not allowed" });
    if (req.method !== "POST") return reply(405, { error: "Use POST" });

    const body = await req.json().catch(() => ({}));
    if (typeof body.prompt !== "string" || !body.prompt.trim() || body.prompt.length > MAX_PROMPT_CHARS) {
      return reply(400, { error: "Bad prompt" });
    }

    // One counter per visitor IP; it is charged before the call so retries count too.
    const limit = Number(env.DAILY_LIMIT);
    const ip = req.headers.get("CF-Connecting-IP") ?? "unknown";
    const left = await env.QUOTA.get(env.QUOTA.idFromName(ip)).take(limit);
    if (left < 0) {
      return reply(429, { error: `Daily limit of ${limit} AI requests reached. Add your own free Gemini key in the sidebar, or come back tomorrow.` });
    }

    const gemini = await fetch(`https://generativelanguage.googleapis.com/v1beta/models/${MODEL}:generateContent`, {
      method: "POST",
      headers: { "Content-Type": "application/json", "x-goog-api-key": env.GEMINI_API_KEY },
      body: JSON.stringify({
        contents: [{ parts: [{ text: body.prompt }] }],
        ...(body.json === true && { generationConfig: { responseMimeType: "application/json" } }),
      }),
    });
    const data = await gemini.json().catch(() => ({}));
    if (gemini.status === 429) {
      return reply(429, { error: "The shared free AI quota is used up for today. Add your own free Gemini key in the sidebar." });
    }
    if (!gemini.ok) return reply(gemini.status >= 500 ? 503 : 502, { error: `Gemini returned HTTP ${gemini.status}` });
    const parts = data.candidates?.[0]?.content?.parts;
    if (!parts) return reply(502, { error: "Unexpected Gemini response (possibly blocked)." });
    return reply(200, { text: parts.map((p) => p.text ?? "").join(""), left });
  },
};

// Stores only {day, count} per visitor; the IP is just the lookup name, never saved.
export class Quota extends DurableObject {
  async take(limit) {
    const today = new Date().toISOString().slice(0, 10);
    const s = (await this.ctx.storage.get("s")) ?? {};
    const count = s.day === today ? s.count : 0;
    if (count >= limit) return -1;
    await this.ctx.storage.put("s", { day: today, count: count + 1 });
    return limit - count - 1;
  }
}
