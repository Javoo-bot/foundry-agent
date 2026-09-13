/**
 * Chat proxy for the published page.
 *
 * GitHub Pages is static and cannot hold a credential, so the key lives here
 * as a Worker secret and the page calls this instead. That is the whole
 * reason this exists.
 *
 * It is an open endpoint on the internet spending someone's Azure credit, so
 * it is capped in three independent ways: a global daily ceiling, a per-IP
 * hourly limit, and a hard cap on how much the model may generate. Any one of
 * them failing open still leaves the other two. The limits are published in
 * the response headers rather than hidden, because a visitor who hits one
 * should be able to tell it is a budget and not a bug.
 *
 * The model gets no tools. It answers questions about one person from one
 * profile, and it is told to decline anything the profile does not cover —
 * the same rule the fleet agent is held to, and the same thing the evaluation
 * suite measures it on.
 */

import PROFILE from "./profile.txt";

const DAILY_CEILING = 250;      // questions per day across everyone
const PER_IP_HOURLY = 8;        // questions per IP per hour
const MAX_QUESTION = 400;       // characters
const MAX_OUTPUT_TOKENS = 400;

const ALLOWED_ORIGINS = [
  "https://javoo-bot.github.io",
  "http://localhost:8000",
];


function cors(origin) {
  const allowed = ALLOWED_ORIGINS.includes(origin) ? origin : ALLOWED_ORIGINS[0];
  return {
    "Access-Control-Allow-Origin": allowed,
    "Access-Control-Allow-Methods": "POST, OPTIONS",
    "Access-Control-Allow-Headers": "Content-Type",
    "Vary": "Origin",
  };
}

const json = (body, status, headers) =>
  new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json", ...headers },
  });

/**
 * Increment a counter and return its new value.
 *
 * D1 rather than KV because the account token carries D1 and Workers scopes
 * and not KV. It suits the job better anyway: KV is eventually consistent, so
 * two requests arriving together can both read the same count and each write
 * back one more than it, which is exactly the race a spend limit must not
 * have. The upsert below is atomic.
 *
 * Rows carry their own expiry and are swept opportunistically, so the table
 * stays small without a scheduled job.
 */
async function bump(db, key, ttlSeconds) {
  const now = Math.floor(Date.now() / 1000);
  const row = await db
    .prepare(
      `INSERT INTO counters (key, n, expires_at) VALUES (?1, 1, ?2)
       ON CONFLICT(key) DO UPDATE SET
         n = CASE WHEN counters.expires_at < ?3 THEN 1 ELSE counters.n + 1 END,
         expires_at = CASE WHEN counters.expires_at < ?3 THEN ?2 ELSE counters.expires_at END
       RETURNING n`,
    )
    .bind(key, now + ttlSeconds, now)
    .first();
  return row?.n ?? 1;
}

async function sweep(db) {
  const now = Math.floor(Date.now() / 1000);
  await db.prepare("DELETE FROM counters WHERE expires_at < ?1").bind(now).run();
}

export default {
  async fetch(request, env) {
    const origin = request.headers.get("Origin") || "";
    const headers = cors(origin);

    if (request.method === "OPTIONS") return new Response(null, { status: 204, headers });
    if (request.method !== "POST") return json({ error: "POST a question" }, 405, headers);

    let question = "";
    try {
      ({ question } = await request.json());
    } catch {
      return json({ error: "expected JSON with a question field" }, 400, headers);
    }
    question = String(question || "").trim();
    if (!question) return json({ error: "the question is empty" }, 400, headers);
    if (question.length > MAX_QUESTION) {
      return json({ error: `questions are limited to ${MAX_QUESTION} characters` }, 400, headers);
    }

    // Budget, in three independent places.
    const day = new Date().toISOString().slice(0, 10);
    const hour = new Date().toISOString().slice(0, 13);
    const ip = request.headers.get("CF-Connecting-IP") || "unknown";

    const perIp = await bump(env.DB, `ip:${ip}:${hour}`, 3600);
    if (perIp > PER_IP_HOURLY) {
      return json(
        { error: `That is ${PER_IP_HOURLY} questions this hour, which is the limit per visitor. This runs on a personal Azure credit.` },
        429,
        { ...headers, "Retry-After": "3600" },
      );
    }

    const today = await bump(env.DB, `day:${day}`, 86400);
    if (Math.random() < 0.02) await sweep(env.DB);
    if (today > DAILY_CEILING) {
      return json(
        { error: "The daily budget for this demo is spent. It resets at midnight UTC." },
        429,
        { ...headers, "Retry-After": "3600" },
      );
    }

    const upstream = await fetch(`${env.FOUNDRY_ENDPOINT}/openai/v1/responses`, {
      method: "POST",
      headers: {
        Authorization: `Bearer ${env.AZURE_MODEL_TOKEN}`,
        "Content-Type": "application/json",
      },
      body: JSON.stringify({
        model: env.MODEL_DEPLOYMENT,
        instructions: PROFILE,
        input: question,
        max_output_tokens: MAX_OUTPUT_TOKENS,
      }),
    });

    if (!upstream.ok) {
      const detail = await upstream.text();
      // The platform content filter rejecting a prompt is not a server fault,
      // and saying so is more useful than a generic failure.
      const filtered = detail.includes("content_filter") || detail.includes("content management policy");
      return json(
        {
          error: filtered
            ? "Azure's content filter declined that one. Try asking about his work or this project."
            : "The model endpoint is unavailable. The Azure credit behind this demo may have expired — the evaluation results on this page are unaffected.",
        },
        filtered ? 400 : 502,
        headers,
      );
    }

    const data = await upstream.json();
    const answer =
      data.output_text ||
      (data.output || [])
        .flatMap((o) => o.content || [])
        .map((c) => c.text || "")
        .join("")
        .trim();

    return json(
      { answer: answer || "No answer came back. Try rephrasing." },
      200,
      {
        ...headers,
        "X-Budget-Remaining-Today": String(Math.max(0, DAILY_CEILING - today)),
        "X-Your-Questions-This-Hour": `${perIp}/${PER_IP_HOURLY}`,
      },
    );
  },
};
