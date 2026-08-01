/* =========================================================
   VOICE OF TRISMA — ADMIN WORKER
   ---------------------------------------------------------
   Worker Cloudflare untuk:
     1. Login admin  — kredensial dari SECRETS Cloudflare
        (ADMIN_USERNAME, ADMIN_PASSWORD). Token sesi
        HMAC-SHA256 (stateless, tanpa DB sesi).
     2. Kelola jadwal siaran — tersimpan di KV
        (binding VOT_ADMIN_STORE, key "jadwal").

   Situs utama memakai  GET  /api/jadwal  (publik) sebagai
   pengganti jadwal.json.
   Dashboard admin memakai POST /api/login  + PUT /api/jadwal
   (endpoint admin butuh header: Authorization: Bearer <token>).

   ---------------------------------------------------------
   CARA MENAMBAH FITUR BARU (biar bisa develop massive):
     1. Daftarkan route baru di array ROUTES (paling bawah).
     2. Tulis handler-nya. Kalau endpoint khusus admin,
        panggil requireAuth() di awal handler.
     3. Data baru simpan di KV dengan key sendiri
        (mis. "pengumuman") lewat helper kvGetJson / kvSetJson.
   ========================================================= */

export interface Env {
	VOT_ADMIN_STORE: KVNamespace;
	ADMIN_USERNAME: string;
	ADMIN_PASSWORD: string;
	SESSION_SECRET: string;
}

interface JadwalItem {
	waktu_mulai: string;
	waktu_selesai: string | null;
	acara: string;
	penyiar: string;
}

type JadwalDoc = Record<string, JadwalItem[]>;

/* ---------------- Konstanta ---------------- */

const KV_KEY_JADWAL = "jadwal";
const KV_KEY_HISTORY = "jadwal_history";
const KV_KEY_LOGS = "admin_logs";
const TOKEN_TTL_SECONDS = 7 * 24 * 3600; // token berlaku 7 hari
const LOGIN_MAX_ATTEMPTS = 5; // percobaan login gagal per jendela waktu
const LOGIN_WINDOW_MS = 10 * 60 * 1000; // 10 menit
const HISTORY_MAX = 5; // riwayat jadwal: maksimal 5 versi terakhir
const LOGS_MAX = 100; // log aktivitas admin: maksimal 100 entri

const TIME_RE = /^([01]\d|2[0-3]):[0-5]\d$/;
const DAY_KEYS = ["0", "1", "2", "3", "4", "5", "6"];

/* Jadwal bawaan — dipakai sebagai seed saat KV masih kosong
   (sumber historis: template/main/jadwal.json). Hari Minggu (0)
   sengaja dikosongkan, konsisten dengan perilaku situs lama. */
const DEFAULT_JADWAL: JadwalDoc = {
	"1": [
		{ waktu_mulai: "17:00", waktu_selesai: "18:00", acara: "Info Science", penyiar: "Vita" },
		{ waktu_mulai: "18:00", waktu_selesai: null, acara: "Tri Sandhya", penyiar: "-" },
	],
	"2": [
		{ waktu_mulai: "17:00", waktu_selesai: "17:15", acara: "Berita Umum", penyiar: "Nia" },
		{ waktu_mulai: "17:15", waktu_selesai: "18:00", acara: "info IPTEK", penyiar: "Vita" },
		{ waktu_mulai: "18:00", waktu_selesai: null, acara: "Tri Sandhya", penyiar: "-" },
	],
	"3": [
		{ waktu_mulai: "17:00", waktu_selesai: "17:30", acara: "T. Kupas Kording", penyiar: "Nia" },
		{ waktu_mulai: "17:30", waktu_selesai: "18:00", acara: "Conveito", penyiar: "Vita" },
		{ waktu_mulai: "18:00", waktu_selesai: null, acara: "Tri Sandhya", penyiar: "-" },
	],
	"4": [
		{ waktu_mulai: "17:00", waktu_selesai: "18:00", acara: "Tau Gak Sih?", penyiar: "Deya" },
		{ waktu_mulai: "18:00", waktu_selesai: null, acara: "Tri Sandhya", penyiar: "-" },
	],
	"5": [
		{ waktu_mulai: "16:00", waktu_selesai: "16:30", acara: "T. (Profil Siswa Berprestasi)", penyiar: "Nia" },
		{ waktu_mulai: "16:30", waktu_selesai: "17:30", acara: "Nonstop Music", penyiar: "Deya" },
		{ waktu_mulai: "17:30", waktu_selesai: "18:00", acara: "Conveito", penyiar: "Vita" },
		{ waktu_mulai: "18:00", waktu_selesai: null, acara: "Tri Sandhya", penyiar: "-" },
	],
	"6": [
		{ waktu_mulai: "16:00", waktu_selesai: "16:45", acara: "All about Movie", penyiar: "Deya" },
		{ waktu_mulai: "16:45", waktu_selesai: "17:15", acara: "Tangga Lagu Barat", penyiar: "Vita" },
		{ waktu_mulai: "17:15", waktu_selesai: "17:30", acara: "Kilas Trisma", penyiar: "Deya" },
		{ waktu_mulai: "17:30", waktu_selesai: "18:00", acara: "Tangga Lagu Indonesia", penyiar: "Nia" },
	],
};

/* ---------------- Helper dasar ---------------- */

function json(data: unknown, status = 200, extraHeaders: Record<string, string> = {}): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: {
			"content-type": "application/json; charset=utf-8",
			"cache-control": "no-store",
			...extraHeaders,
		},
	});
}

const CORS_HEADERS: Record<string, string> = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
	"Access-Control-Allow-Headers": "Content-Type, Authorization",
	"Access-Control-Max-Age": "86400",
};

function withCors(response: Response): Response {
	const headers = new Headers(response.headers);
	for (const [k, v] of Object.entries(CORS_HEADERS)) headers.set(k, v);
	return new Response(response.body, { status: response.status, headers });
}

/* Perbandingan string konstan-waktu (anti timing attack). */
function timingSafeEqual(a: string, b: string): boolean {
	if (a.length !== b.length) return false;
	let diff = 0;
	for (let i = 0; i < a.length; i++) diff |= a.charCodeAt(i) ^ b.charCodeAt(i);
	return diff === 0;
}

/* Hash kedua sisi dulu supaya panjang string tidak bocor. */
async function secureEqual(a: string, b: string): Promise<boolean> {
	const hash = async (s: string) => {
		const digest = await crypto.subtle.digest("SHA-256", new TextEncoder().encode(s));
		return b64url(String.fromCharCode(...new Uint8Array(digest)));
	};
	return timingSafeEqual(await hash(a), await hash(b));
}

/* ---------------- Base64 URL-safe ---------------- */

function b64url(input: string): string {
	return btoa(input).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function b64urlDecode(input: string): string {
	const pad = input.length % 4 === 0 ? "" : "=".repeat(4 - (input.length % 4));
	return atob(input.replace(/-/g, "+").replace(/_/g, "/") + pad);
}

/* ---------------- Sesi token (HMAC-SHA256) ---------------- */

async function hmac(secret: string, data: string): Promise<string> {
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

async function signToken(env: Env, username: string): Promise<string> {
	const payload = b64url(
		JSON.stringify({ u: username, exp: Math.floor(Date.now() / 1000) + TOKEN_TTL_SECONDS })
	);
	const sig = await hmac(env.SESSION_SECRET, payload);
	return `${payload}.${sig}`;
}

async function verifyToken(env: Env, token: string): Promise<{ u: string } | null> {
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

/* Ambil user dari header Authorization: Bearer <token>. */
async function requireAuth(request: Request, env: Env): Promise<{ u: string } | null> {
	const header = request.headers.get("Authorization") || "";
	const token = header.startsWith("Bearer ") ? header.slice(7) : "";
	if (!token) return null;
	return verifyToken(env, token);
}

/* ---------------- Rate limiter login (best-effort) ----------------
   Catatan: Map ini per-isolate. Untuk throttle ketat lintas global
   gunakan KV dengan TTL, tapi untuk admin login ini sudah cukup. */
const loginAttempts = new Map<string, { count: number; windowStart: number }>();

function checkRateLimit(request: Request): { allowed: boolean; retryAfterSeconds?: number } {
	const ip = request.headers.get("CF-Connecting-IP") || "unknown";
	const now = Date.now();
	const entry = loginAttempts.get(ip);

	if (!entry || now - entry.windowStart > LOGIN_WINDOW_MS) {
		loginAttempts.set(ip, { count: 1, windowStart: now });
		return { allowed: true };
	}

	entry.count += 1;
	if (entry.count > LOGIN_MAX_ATTEMPTS) {
		const retryAfterSeconds = Math.max(1, Math.ceil((LOGIN_WINDOW_MS - (now - entry.windowStart)) / 1000));
		return { allowed: false, retryAfterSeconds };
	}
	return { allowed: true };
}

/* ---------------- Helper KV ---------------- */

async function kvGetJson(env: Env, key: string): Promise<unknown | null> {
	const raw = await env.VOT_ADMIN_STORE.get(key);
	if (!raw) return null;
	try {
		return JSON.parse(raw);
	} catch {
		return null;
	}
}

async function kvSetJson(env: Env, key: string, value: unknown): Promise<void> {
	await env.VOT_ADMIN_STORE.put(key, JSON.stringify(value));
}

/* ---------------- Riwayat jadwal & log aktivitas ---------------- */

interface HistoryEntry {
	saved_at: string;
	jadwal: JadwalDoc;
}

/* Simpan versi sekarang ke riwayat (index 0 = terbaru), potong ke HISTORY_MAX.
   BEST-EFFORT: riwayat tidak boleh menggagalkan simpan jadwal utama. */
async function pushHistory(env: Env, doc: JadwalDoc): Promise<void> {
	try {
		const history = ((await kvGetJson(env, KV_KEY_HISTORY)) || []) as HistoryEntry[];
		history.unshift({ saved_at: new Date().toISOString(), jadwal: doc });
		while (history.length > HISTORY_MAX) history.pop();
		await kvSetJson(env, KV_KEY_HISTORY, history);
	} catch (e) {
		console.error("pushHistory gagal (best-effort):", e);
	}
}

/* Catat log aktivitas admin (index 0 = terbaru), potong ke LOGS_MAX.
   BEST-EFFORT: kegagalan KV (mis. kuota put harian habis) TIDAK boleh
   menggagalkan request inti (login/simpan) — log hanya catatan sekunder. */
async function appendLog(env: Env, action: string, detail?: unknown): Promise<void> {
	try {
		const logs = ((await kvGetJson(env, KV_KEY_LOGS)) || []) as Array<{
			t: string;
			action: string;
			detail: unknown;
		}>;
		logs.unshift({ t: new Date().toISOString(), action, detail: detail ?? null });
		while (logs.length > LOGS_MAX) logs.pop();
		await kvSetJson(env, KV_KEY_LOGS, logs);
	} catch (e) {
		console.error("appendLog gagal (best-effort):", e);
	}
}

async function kvGetJadwal(env: Env): Promise<JadwalDoc> {
	const raw = await kvGetJson(env, KV_KEY_JADWAL);
	if (raw && typeof raw === "object") return raw as JadwalDoc;

	// Lazy seed: KV kosong → isi jadwal bawaan supaya situs langsung jalan.
	await kvSetJson(env, KV_KEY_JADWAL, DEFAULT_JADWAL);
	return DEFAULT_JADWAL;
}

/* ---------------- Validasi dokumen jadwal ---------------- */

function validateJadwalDoc(input: unknown): { ok: true; doc: JadwalDoc } | { ok: false; error: string } {
	if (typeof input !== "object" || input === null) {
		return { ok: false, error: "Body harus berupa objek { jadwal: {...} }" };
	}

	const jadwal = (input as { jadwal?: unknown }).jadwal;
	if (typeof jadwal !== "object" || jadwal === null || Array.isArray(jadwal)) {
		return { ok: false, error: "Field 'jadwal' harus objek berisi hari (kunci \"0\" sampai \"6\")" };
	}

	const doc: JadwalDoc = {};

	for (const day of DAY_KEYS) {
		const raw = (jadwal as Record<string, unknown>)[day];
		if (raw === undefined) continue; // hari boleh kosong / tidak ada

		if (!Array.isArray(raw)) {
			return { ok: false, error: `Jadwal hari '${day}' harus berupa array` };
		}

		const items: JadwalItem[] = [];
		for (let i = 0; i < raw.length; i++) {
			const it = raw[i] as Record<string, unknown> | null;
			if (typeof it !== "object" || it === null) {
				return { ok: false, error: `Item ke-${i + 1} hari '${day}' bukan objek` };
			}

			const mulai = String(it.waktu_mulai ?? "").trim();
			const selesaiRaw = it.waktu_selesai;
			const selesai =
				selesaiRaw === null || selesaiRaw === undefined || String(selesaiRaw).trim() === ""
					? null
					: String(selesaiRaw).trim();
			const acara = String(it.acara ?? "").trim();
			const penyiar = String(it.penyiar ?? "").trim();

			if (!TIME_RE.test(mulai)) {
				return { ok: false, error: `Item ke-${i + 1} hari '${day}': waktu_mulai harus format HH:MM` };
			}
			if (selesai !== null && !TIME_RE.test(selesai)) {
				return { ok: false, error: `Item ke-${i + 1} hari '${day}': waktu_selesai harus HH:MM atau kosong` };
			}
			if (!acara) {
				return { ok: false, error: `Item ke-${i + 1} hari '${day}': acara wajib diisi` };
			}

			items.push({ waktu_mulai: mulai, waktu_selesai: selesai, acara, penyiar });
		}
		doc[day] = items;
	}

	return { ok: true, doc };
}

/* ---------------- Handler endpoint ---------------- */

/* GET /api/health — publik, pengecekan status worker. */
function handleHealth(_request: Request, _env: Env): Response {
	return json({ ok: true, service: "voiceoftrisma-admin-worker", time: new Date().toISOString() });
}

/* GET /api/jadwal — publik, dipakai situs utama & dashboard. */
async function handleGetJadwal(_request: Request, env: Env): Promise<Response> {
	const doc = await kvGetJadwal(env);
	// max-age pendek supaya perubahan jadwal cepat terlihat.
	return json({ jadwal: doc }, 200, { "cache-control": "public, max-age=120" });
}

/* POST /api/login — publik. Body: { username, password }. */
async function handleLogin(request: Request, env: Env): Promise<Response> {
	const rl = checkRateLimit(request);
	if (!rl.allowed) {
		return json({ error: "Terlalu banyak percobaan login. Coba lagi nanti." }, 429, {
			"Retry-After": String(rl.retryAfterSeconds ?? 60),
		});
	}

	let body: { username?: unknown; password?: unknown };
	try {
		body = await request.json();
	} catch {
		return json({ error: "Body harus berupa JSON" }, 400);
	}

	const username = typeof body.username === "string" ? body.username : "";
	const password = typeof body.password === "string" ? body.password : "";

	// Perbandingan konstan-waktu terhadap secrets.
	const userOk = await secureEqual(username, env.ADMIN_USERNAME);
	const passOk = await secureEqual(password, env.ADMIN_PASSWORD);

	if (!userOk || !passOk) {
		await appendLog(env, "login_failed", {
			ip: request.headers.get("CF-Connecting-IP") || "unknown",
		});
		return json({ error: "Username atau password salah." }, 401);
	}

	const token = await signToken(env, username);
	await appendLog(env, "login", { user: username });
	return json({ token, expires_in: TOKEN_TTL_SECONDS, user: username });
}

/* PUT /api/jadwal — admin. Body: { jadwal: { "1": [...], ... } }.
   Menyimpan SELURUH dokumen jadwal (full replace). */
async function handlePutJadwal(request: Request, env: Env): Promise<Response> {
	const auth = await requireAuth(request, env);
	if (!auth) {
		return json({ error: "Unauthorized. Token tidak valid atau kedaluwarsa." }, 401);
	}

	let raw: unknown;
	try {
		raw = await request.json();
	} catch {
		return json({ error: "Body harus berupa JSON" }, 400);
	}

	const result = validateJadwalDoc(raw);
	if (!result.ok) return json({ error: result.error }, 400);

	// Simpan versi lama ke riwayat sebelum di-overwrite (undo).
	const current = await kvGetJadwal(env);
	await pushHistory(env, current);

	// Tulis utama: kalau gagal (mis. kuota KV put harian habis), beri pesan jelas.
	try {
		await kvSetJson(env, KV_KEY_JADWAL, result.doc);
	} catch (e) {
		console.error("Simpan jadwal gagal:", e);
		return json({ error: "Gagal menyimpan: kuota tulis KV habis. Coba lagi nanti." }, 503);
	}
	await appendLog(env, "jadwal_update", {
		user: auth.u,
		hari: Object.keys(result.doc).length,
	});
	return json({ ok: true, saved_at: new Date().toISOString(), jadwal: result.doc });
}

/* GET /api/me — admin. Validasi sesi server-side (dipakai dashboard saat load). */
async function handleMe(request: Request, env: Env): Promise<Response> {
	const auth = await requireAuth(request, env);
	if (!auth) {
		return json({ error: "Unauthorized. Token tidak valid atau kedaluwarsa." }, 401);
	}
	return json({ ok: true, user: auth.u, time: new Date().toISOString() });
}

/* GET /api/jadwal/history — admin. Daftar versi jadwal tersimpan (index 0 = terbaru). */
async function handleGetHistory(request: Request, env: Env): Promise<Response> {
	const auth = await requireAuth(request, env);
	if (!auth) {
		return json({ error: "Unauthorized. Token tidak valid atau kedaluwarsa." }, 401);
	}

	const history = ((await kvGetJson(env, KV_KEY_HISTORY)) || []) as HistoryEntry[];
	const versions = history.map((h, index) => ({ index, saved_at: h.saved_at, jadwal: h.jadwal }));
	return json({ versions });
}

/* POST /api/jadwal/restore — admin. Body: { index }. Kembalikan versi riwayat. */
async function handleRestoreJadwal(request: Request, env: Env): Promise<Response> {
	const auth = await requireAuth(request, env);
	if (!auth) {
		return json({ error: "Unauthorized. Token tidak valid atau kedaluwarsa." }, 401);
	}

	let body: { index?: unknown };
	try {
		body = await request.json();
	} catch {
		return json({ error: "Body harus berupa JSON" }, 400);
	}

	const history = ((await kvGetJson(env, KV_KEY_HISTORY)) || []) as HistoryEntry[];
	const index = body.index;
	if (typeof index !== "number" || !Number.isInteger(index) || index < 0 || index >= history.length) {
		return json({ error: `index harus angka 0..${Math.max(0, history.length - 1)}` }, 400);
	}

	const version = history[index];
	const result = validateJadwalDoc({ jadwal: version.jadwal });
	if (!result.ok) return json({ error: result.error }, 400);

	// Simpan versi sekarang ke riwayat (rantai tetap utuh), lalu restore.
	const current = await kvGetJadwal(env);
	await pushHistory(env, current);
	try {
		await kvSetJson(env, KV_KEY_JADWAL, result.doc);
	} catch (e) {
		console.error("Restore jadwal gagal:", e);
		return json({ error: "Gagal restore: kuota tulis KV habis. Coba lagi nanti." }, 503);
	}
	await appendLog(env, "jadwal_restore", {
		user: auth.u,
		index,
		restored_saved_at: version.saved_at,
	});

	return json({ ok: true, restored_at: new Date().toISOString(), jadwal: result.doc });
}

/* GET /api/logs — admin. Log aktivitas (login, update, restore). */
async function handleGetLogs(request: Request, env: Env): Promise<Response> {
	const auth = await requireAuth(request, env);
	if (!auth) {
		return json({ error: "Unauthorized. Token tidak valid atau kedaluwarsa." }, 401);
	}

	const logs = (await kvGetJson(env, KV_KEY_LOGS)) || [];
	return json({ logs });
}

/* ---------------- Router (tambahkan fitur baru di sini) ---------------- */

interface Route {
	method: string;
	pattern: string;
	handler: (request: Request, env: Env) => Response | Promise<Response>;
}

const ROUTES: Route[] = [
	{ method: "GET", pattern: "/api/health", handler: handleHealth },
	{ method: "GET", pattern: "/api/jadwal", handler: handleGetJadwal },
	{ method: "POST", pattern: "/api/login", handler: handleLogin },
	{ method: "PUT", pattern: "/api/jadwal", handler: handlePutJadwal },
	{ method: "GET", pattern: "/api/me", handler: handleMe },
	{ method: "GET", pattern: "/api/jadwal/history", handler: handleGetHistory },
	{ method: "POST", pattern: "/api/jadwal/restore", handler: handleRestoreJadwal },
	{ method: "GET", pattern: "/api/logs", handler: handleGetLogs },
];

const compiledRoutes = ROUTES.map((r) => ({ ...r, pattern: new URLPattern({ pathname: r.pattern }) }));

export default {
	async fetch(request: Request, env: Env): Promise<Response> {
		const url = new URL(request.url);

		// Preflight CORS
		if (request.method === "OPTIONS") {
			return new Response(null, { status: 204, headers: CORS_HEADERS });
		}

		for (const route of compiledRoutes) {
			if (route.method !== request.method) continue;
			if (!route.pattern.exec(url)) continue;

			try {
				return withCors(await route.handler(request, env));
			} catch (err) {
				console.error("Unhandled error:", err);
				return withCors(json({ error: "Internal server error" }, 500));
			}
		}

		return withCors(json({ error: "Not found" }, 404));
	},
};
