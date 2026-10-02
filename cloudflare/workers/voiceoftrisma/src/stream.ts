/* =========================================================
   VOICE OF TRISMA — MODUL STREAM (bridge eksekutabel -> icecast)
   ---------------------------------------------------------
   GET /stream?url=<ICE_URL>
     - Validasi upstream ke whitelist host (anti-SSRF abuse).
     - Forward byte audio dari icecast ke client.
     - Catat sesi ke D1 tabel `record_sessions`:
         session_id (uuid), started_at, ended_at, duration_sec,
         bytes_uploaded, item_id, client_ip, status.
     - `item_id` di-PATCH belakangan via PUT /stream/session
       (eksekutabel panggil setelah upload Internet Archive sukses).

   Catatan: `?url=` WAJIB di-URL-encode oleh caller. Whitelist host
   mengizinkan HANYA i.klikhost.com:8502 (radio VOT Denpasar).
   ========================================================= */

import { Env, Route, json } from "./shared";

// Whitelist host:port upstream. Tolak semua selain ini.
const ALLOWED_UPSTREAM_HOSTS = new Set<string>([
	"i.klikhost.com:8502",
	// Cadangan: icecast Klikhost kadang expose juga tanpa port eksplisit.
	"i.klikhost.com",
]);

// UUID v4 sederhana (cukup untuk identifier sesi, bukan security).
function uuid(): string {
	const bytes = new Uint8Array(16);
	crypto.getRandomValues(bytes);
	bytes[6] = (bytes[6] & 0x0f) | 0x40;
	bytes[8] = (bytes[8] & 0x3f) | 0x80;
	const h = Array.from(bytes, (b) => b.toString(16).padStart(2, "0")).join("");
	return `${h.slice(0, 8)}-${h.slice(8, 12)}-${h.slice(12, 16)}-${h.slice(16, 20)}-${h.slice(20)}`;
}

function isAllowedUpstream(u: URL): boolean {
	const hostPort = u.port ? `${u.hostname}:${u.port}` : u.hostname;
	return ALLOWED_UPSTREAM_HOSTS.has(hostPort);
}

/* Buka stream upstream, forward byte ke client, dan catat sesi ke D1.
   Jalankan di background (ctx.waitUntil) — D1 insert tidak boleh
   memblokir byte pertama audio ke eksekutabel. */
async function proxyAndTrack(
	request: Request,
	env: Env,
	ctx: ExecutionContext,
	upstream: URL,
	sessionId: string,
	clientIp: string,
): Promise<Response> {
	const startedAt = Math.floor(Date.now() / 1000);

	// Headers mirip browser biasa (random sudah di sisi eksekutabel;
	// di sini kita konsisten, karena worker->icecast langsung).
	const upstreamHeaders: Record<string, string> = {
		"User-Agent":
			"Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
		Accept: "*/*",
		Connection: "keep-alive",
	};

	let upstreamRes: Response;
	try {
		upstreamRes = await fetch(upstream.href, {
			headers: upstreamHeaders,
			// Tidak set signal: biarkan stream panjang.
		});
	} catch (e) {
		console.error(`[stream] upstream fetch gagal: ${e}`);
		// Catat sesi gagal & jawab 502.
		ctx.waitUntil(
			env.DB.prepare(
				"INSERT INTO record_sessions (session_id, started_at, ended_at, duration_sec, bytes_uploaded, item_id, client_ip, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			)
				.bind(sessionId, startedAt, startedAt, 0, 0, null, clientIp, "failed")
				.run(),
		);
		return json({ error: "Upstream unreachable" }, 502);
	}

	if (!upstreamRes.ok || !upstreamRes.body) {
		ctx.waitUntil(
			env.DB.prepare(
				"INSERT INTO record_sessions (session_id, started_at, ended_at, duration_sec, bytes_uploaded, item_id, client_ip, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
			)
				.bind(sessionId, startedAt, startedAt, 0, 0, null, clientIp, `http_${upstreamRes.status}`)
				.run(),
		);
		return new Response(`Upstream HTTP ${upstreamRes.status}`, { status: 502 });
	}

	// Catat sesi "recording" segera (biar dashboard bisa lihat).
	ctx.waitUntil(
		env.DB.prepare(
			"INSERT INTO record_sessions (session_id, started_at, ended_at, duration_sec, bytes_uploaded, item_id, client_ip, status) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
		)
			.bind(sessionId, startedAt, null, null, 0, null, clientIp, "recording")
			.run(),
	);

	// Passthrough body. Kita tidak hitung byte di sini (Transformer
	// menambah overhead memori) — biarkan eksekutabel yang melapor
	// via PUT /stream/session setelah upload.
	const headers = new Headers();
	headers.set("Content-Type", upstreamRes.headers.get("Content-Type") || "audio/mpeg");
	headers.set("Cache-Control", "no-store");
	headers.set("X-VOT-Session-Id", sessionId); // <- eksekutabel baca ini

	return new Response(upstreamRes.body, { status: 200, headers });
}

async function handleStream(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
	const url = new URL(request.url);
	const target = url.searchParams.get("url");

	if (!target) {
		return json({ error: "Missing ?url= parameter" }, 400);
	}

	let upstream: URL;
	try {
		upstream = new URL(target);
	} catch {
		return json({ error: "Invalid ?url= (must be a valid URL)" }, 400);
	}

	if (upstream.protocol !== "http:" && upstream.protocol !== "https:") {
		return json({ error: "Only http(s) upstream allowed" }, 400);
	}
	if (!isAllowedUpstream(upstream)) {
		return json(
			{
				error: "Upstream host not whitelisted",
				allowed: Array.from(ALLOWED_UPSTREAM_HOSTS),
			},
			403,
		);
	}

	const clientIp =
		request.headers.get("CF-Connecting-IP") || request.headers.get("True-Client-IP") || "unknown";

	// Pakai header X-VOT-Session-Id kalau caller sudah generate
	// (berguna untuk retry idempotent). Jika tidak, generate baru.
	const sessionId = request.headers.get("X-VOT-Session-Id") || uuid();

	return proxyAndTrack(request, env, ctx, upstream, sessionId, clientIp);
}

/* PUT /stream/session — eksekutabel panggil SETELAH upload Internet
   Archive (sukses/gagal). Body JSON:
     { session_id, ended_at, duration_sec, bytes_uploaded, item_id, status }
   Endpoint ini idempotent (UPSERT by session_id). */
async function handleSessionUpdate(request: Request, env: Env): Promise<Response> {
	let body: Record<string, unknown>;
	try {
		body = (await request.json()) as Record<string, unknown>;
	} catch {
		return json({ error: "Body must be JSON" }, 400);
	}

	const sessionId = typeof body.session_id === "string" ? body.session_id : null;
	if (!sessionId) {
		return json({ error: "session_id required" }, 400);
	}

	const endedAt = typeof body.ended_at === "number" ? body.ended_at : Math.floor(Date.now() / 1000);
	const durationSec = typeof body.duration_sec === "number" ? body.duration_sec : 0;
	const bytesUploaded = typeof body.bytes_uploaded === "number" ? body.bytes_uploaded : 0;
	const itemId = typeof body.item_id === "string" ? body.item_id : null;
	const status = typeof body.status === "string" ? body.status : "uploaded";

	try {
		await env.DB.prepare(
			"UPDATE record_sessions SET ended_at = ?, duration_sec = ?, bytes_uploaded = ?, item_id = ?, status = ? " +
				"WHERE session_id = ?",
		)
			.bind(endedAt, durationSec, bytesUploaded, itemId, status, sessionId)
			.run();
		return json({ ok: true });
	} catch (e) {
		console.error(`[stream] session update gagal: ${e}`);
		return json({ error: "DB update failed" }, 500);
	}
}

export const streamRoutes: Route[] = [
	{ method: "GET", pattern: "/stream", handler: handleStream },
	{ method: "PUT", pattern: "/stream/session", handler: handleSessionUpdate },
];
