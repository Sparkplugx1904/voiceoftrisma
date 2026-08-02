/* =========================================================
   VOICE OF TRISMA — MODUL ADMIN (dari voiceoftrisma-admin-worker)
   ---------------------------------------------------------
   1. Login admin — kredensial dari SECRETS (ADMIN_USERNAME,
      ADMIN_PASSWORD). Token sesi HMAC-SHA256 stateless.
   2. Kelola jadwal siaran — D1 tabel `kv_store` (key "jadwal").

   Semua route di-prefix /api/* — path TIDAK berubah dari worker
   lama, jadi frontend cukup ganti host.
   ========================================================= */

import {
	Env,
	Route,
	json,
	secureEqual,
	signToken,
	requireAuth,
	secretsReady,
	d1GetJson,
	d1SetJson,
} from "./shared";

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

/* Jadwal bawaan — dipakai sebagai seed saat KV masih kosong.
   Hari Minggu (0) sengaja dikosongkan, konsisten dengan situs lama. */
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

/* ---------------- Rate limiter login (best-effort, per-isolate) ---------------- */

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
	// Prune sederhana: map tumbuh karena entry per-IP; bersihkan saat membesar
	// supaya isolate tidak menahan memory tanpa batas (best-effort).
	if (loginAttempts.size > 1000) {
		for (const [ip, e] of loginAttempts) {
			if (now - e.windowStart > LOGIN_WINDOW_MS) loginAttempts.delete(ip);
		}
	}
	return { allowed: true };
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
		const history = ((await d1GetJson(env.DB, KV_KEY_HISTORY)) || []) as HistoryEntry[];
		history.unshift({ saved_at: new Date().toISOString(), jadwal: doc });
		while (history.length > HISTORY_MAX) history.pop();
		await d1SetJson(env.DB, KV_KEY_HISTORY, history);
	} catch (e) {
		console.error("pushHistory gagal (best-effort):", e);
	}
}

/* Catat log aktivitas admin (index 0 = terbaru), potong ke LOGS_MAX.
   BEST-EFFORT: kegagalan penyimpanan tidak boleh menggagalkan request inti. */
async function appendLog(env: Env, action: string, detail?: unknown): Promise<void> {
	try {
		const logs = ((await d1GetJson(env.DB, KV_KEY_LOGS)) || []) as Array<{
			t: string;
			action: string;
			detail: unknown;
		}>;
		logs.unshift({ t: new Date().toISOString(), action, detail: detail ?? null });
		while (logs.length > LOGS_MAX) logs.pop();
		await d1SetJson(env.DB, KV_KEY_LOGS, logs);
	} catch (e) {
		console.error("appendLog gagal (best-effort):", e);
	}
}

async function kvGetJadwal(env: Env): Promise<JadwalDoc> {
	const raw = await d1GetJson(env.DB, KV_KEY_JADWAL);
	if (raw && typeof raw === "object") return raw as JadwalDoc;

	// Lazy seed: D1 kosong → isi jadwal bawaan supaya situs langsung jalan.
	await d1SetJson(env.DB, KV_KEY_JADWAL, DEFAULT_JADWAL);
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
		if (raw.length > 24) {
			return { ok: false, error: `Jadwal hari '${day}' maksimal 24 item` };
		}
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
function handleHealth(_request: Request, env: Env): Response {
	return json({ ok: true, service: "voiceoftrisma", time: new Date().toISOString() });
}

/* GET /api/jadwal — publik, dipakai situs utama & dashboard. */
async function handleGetJadwal(_request: Request, env: Env): Promise<Response> {
	const doc = await kvGetJadwal(env);
	// max-age pendek supaya perubahan jadwal cepat terlihat.
	return json({ jadwal: doc }, 200, { "cache-control": "public, max-age=120" });
}

/* POST /api/login — publik. Body: { username, password }. */
async function handleLogin(request: Request, env: Env): Promise<Response> {
	// Guard: kalau secret belum terpasang, jangan bandingkan dengan "undefined".
	if (!secretsReady(env)) {
		return json({ error: "Server belum dikonfigurasi (secrets belum terpasang)." }, 503);
	}

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

	// Batas panjang input — cegah body raksasa masuk ke hash/DB.
	if (username.length > 100 || password.length > 200) {
		return json({ error: "Username atau password salah." }, 401);
	}

	// Perbandingan konstan-waktu terhadap secrets.
	const userOk = await secureEqual(username, env.ADMIN_USERNAME);
	const passOk = await secureEqual(password, env.ADMIN_PASSWORD);

	if (!userOk || !passOk) {
		// Login GAGAL tidak dicatat ke DB (hindari banjir log dari brute-force).
		// Proteksi brute-force tetap aktif via checkRateLimit (in-memory).
		return json({ error: "Username atau password salah." }, 401);
	}

	const token = await signToken(env, username);
	// Audit trail: catat login sukses (best-effort).
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

	// Dedupe: isi sama persis dengan yang tersimpan → skip tulis (hemat kuota tulis).
	const current = await kvGetJadwal(env);
	if (JSON.stringify(current) === JSON.stringify(result.doc)) {
		return json({
			ok: true,
			saved_at: new Date().toISOString(),
			message: "Tidak ada perubahan — jadwal tidak ditulis ulang.",
			jadwal: result.doc,
		});
	}

	// Simpan versi lama ke riwayat sebelum di-overwrite (undo).
	await pushHistory(env, current);

	// Tulis utama: kalau gagal, beri pesan jelas.
	try {
		await d1SetJson(env.DB, KV_KEY_JADWAL, result.doc);
	} catch (e) {
		console.error("Simpan jadwal gagal:", e);
		return json({ error: "Gagal menyimpan jadwal. Coba lagi nanti." }, 503);
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

	const history = ((await d1GetJson(env.DB, KV_KEY_HISTORY)) || []) as HistoryEntry[];
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

	const history = ((await d1GetJson(env.DB, KV_KEY_HISTORY)) || []) as HistoryEntry[];
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
		await d1SetJson(env.DB, KV_KEY_JADWAL, result.doc);
	} catch (e) {
		console.error("Restore jadwal gagal:", e);
		return json({ error: "Gagal restore jadwal. Coba lagi nanti." }, 503);
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

	const logs = (await d1GetJson(env.DB, KV_KEY_LOGS)) || [];
	return json({ logs });
}

/* ---------------- Route modul admin ---------------- */

export const adminRoutes: Route[] = [
	{ method: "GET", pattern: "/api/health", handler: handleHealth },
	{ method: "GET", pattern: "/api/jadwal", handler: handleGetJadwal },
	{ method: "POST", pattern: "/api/login", handler: handleLogin },
	{ method: "PUT", pattern: "/api/jadwal", handler: handlePutJadwal },
	{ method: "GET", pattern: "/api/me", handler: handleMe },
	{ method: "GET", pattern: "/api/jadwal/history", handler: handleGetHistory },
	{ method: "POST", pattern: "/api/jadwal/restore", handler: handleRestoreJadwal },
	{ method: "GET", pattern: "/api/logs", handler: handleGetLogs },
];
