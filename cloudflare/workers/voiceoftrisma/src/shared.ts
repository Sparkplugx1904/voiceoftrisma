/* =========================================================
   VOICE OF TRISMA — SHARED HELPERS (worker gabungan)
   Helper lintas modul: Env, Route, JSON, CORS, kripto sesi,
   auth, dan akses KV generik.
   ========================================================= */

export interface Env {
	// D1 — penyimpanan data aplikasi (pengganti KV, migrasi 2026-08-02)
	DB: D1Database;
	// KV namespace LAMA — dipertahankan hanya sebagai cadangan rollback;
	// kode tidak lagi memakainya (semua akses lewat DB).
	VOT_ADMIN_STORE: KVNamespace;
	VOT_STREAM_STATS: KVNamespace;
	ARCHIVE_KV: KVNamespace;
	VOT_METRICS_STORE: KVNamespace;
	// Secrets (set via `npx wrangler secret put`)
	ADMIN_USERNAME: string;
	ADMIN_PASSWORD: string;
	SESSION_SECRET: string;
	GITHUB_TOKEN: string;
	// (opsional) tuning anti-DDoS layer-7 — nilai string; default tertanam di index.ts
	MAX_REQ_IP_10S?: string;
	MAX_REQ_GLOBAL_10S?: string;
	// Durable Object rate limiter (shared lintas-isolate). Opsional supaya
	// env test / dev tanpa binding tidak crash (fail-open).
	RATE_LIMITER?: DurableObjectNamespace;
}

export interface Route {
	method: string;
	pattern: string;
	handler: (request: Request, env: Env, ctx: ExecutionContext) => Response | Promise<Response>;
}

/* ---------------- Response & CORS ---------------- */

export function json(data: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			"content-type": "application/json; charset=utf-8",
			"cache-control": "no-store",
			...extraHeaders,
		},
	});
}

export const CORS_HEADERS: Record<string, string> = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
	"Access-Control-Allow-Headers": "Content-Type, Authorization",
	"Access-Control-Max-Age": "86400",
};

export function withCors(response: Response): Response {
	const headers = new Headers(response.headers);
	for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
	return new Response(response.body, { status: response.status, headers });
}

/* ---------------- Kripto pembanding ---------------- */

/* True jika semua secret wajib sudah terpasang (wrangler secret put).
   Tanpa guard ini, env yang undefined membuat secureEqual membandingkan
   string "undefined" → password "undefined" bisa lolos login. */
export function secretsReady(env: Env): boolean {
	return Boolean(env.ADMIN_USERNAME && env.ADMIN_PASSWORD && env.SESSION_SECRET);
}

/* Perbandingan string konstan-waktu (anti timing attack). */
export function timingSafeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	return diff === 0;
}

/* Hash kedua sisi dulu supaya panjang string tidak bocor. */
export async function secureEqual(a: string, b: string): Promise<boolean> {
	const hash = async (s: string) => {
		const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
		return b64url(String.fromCharCode(...new Uint8Array(digest)));
	};
	return timingSafeEqual(await hash(a), await hash(b));
}

/* ---------------- Base64 URL-safe ---------------- */

export function b64url(input: string): string {
	return btoa(input).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

export function b64urlDecode(input: string): string {
	const pad = input.length % 4 === 0 ? "" : "=".repeat(4 - (input.length % 4));
	return atob(input.replace(/-/g, "+").replace(/_/g, "/") + pad);
}

/* ---------------- Sesi token (HMAC-SHA256) ---------------- */

export async function hmac(secret: string, data: string): Promise<string> {
	const key = await crypto.subtle.importKey(
		"raw",
		new TextEncoder().encode(secret),
		{ name: "HMAC", hash: "SHA-256" },
		false,
		["sign"]
	);
	const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
	return b64url(String.fromCharCode(...new Uint8Array(sig)));
}

const TOKEN_TTL_SECONDS = 7 * 24 * 3600; // token berlaku 7 hari

export async function signToken(env: Env, username: string): Promise<string> {
	if (!env.SESSION_SECRET) throw new Error("SESSION_SECRET belum dikonfigurasi");
	const payload = b64url(
		JSON.stringify({ u: username, exp: Math.floor(Date.now() / 1000) + TOKEN_TTL_SECONDS })
	);
	const sig = await hmac(env.SESSION_SECRET, payload);
	return `${payload}.${sig}`;
}

export async function verifyToken(env: Env, token: string): Promise<{ u: string } | null> {
	if (!env.SESSION_SECRET) return null; // secret belum diset → tidak ada token valid
	const parts = token.split(".");
	if (parts.length !== 2) return null;
	const [payloadB64, sig] = parts;

	const expected = await hmac(env.SESSION_SECRET, payloadB64);
	if (!timingSafeEqual(sig, expected)) return null;

	try {
		const payload = JSON.parse(b64urlDecode(payloadB64)) as { u?: unknown; exp?: unknown };
		if (typeof payload.u !== "string" || typeof payload.exp !== "number") return null;
		if (payload.exp < Math.floor(Date.now() / 1000)) return null; // kedaluwarsa
		return { u: payload.u };
	} catch {
		return null;
	}
}

/* Ambil user dari header Authorization: Bearer <token> */
export async function requireAuth(request: Request, env: Env): Promise<{ u: string } | null> {
	const header = request.headers.get("Authorization") || "";
	const token = header.startsWith("Bearer ") ? header.slice(7) : "";
	if (!token) return null;
	return verifyToken(env, token);
}

/* ---------------- Helper KV generik (LEGACY — tidak dipakai kode baru) ---------------- */

export async function kvGetJson(kv: KVNamespace, key: string): Promise<unknown | null> {
	const raw = await kv.get(key);
	if (!raw) return null;
	try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
}

export async function kvSetJson(kv: KVNamespace, key: string, value: unknown): Promise<void> {
	await kv.put(key, JSON.stringify(value));
}

/* ---------------- Helper D1 generik (key-value di tabel kv_store) ---------------- */

export async function d1GetJson(db: D1Database, key: string): Promise<unknown | null> {
	const row = await db.prepare("SELECT value FROM kv_store WHERE key = ?").bind(key).first<{ value: string }>();
	if (!row) return null;
	try {
		return JSON.parse(row.value);
	} catch {
		return null;
	}
}

export async function d1SetJson(db: D1Database, key: string, value: unknown): Promise<void> {
	await db
		.prepare(
			"INSERT INTO kv_store (key, value, updated_at) VALUES (?, ?, ?) " +
				"ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at"
		)
		.bind(key, JSON.stringify(value), Date.now())
		.run();
}
