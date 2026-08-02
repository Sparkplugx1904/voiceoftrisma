/* =========================================================
   VOICE OF TRISMA — MODUL STREAM-STATS (dari voiceoftrisma-stream-stats)
   ---------------------------------------------------------
   1. Cron tiap 5 menit: poll icecast Klikhost lalu CATAT sample
      { t, currentlisteners, streamstatus } ke D1 tabel `samples`.
      t = awal bucket 5 menit (kelipatan 300) → upsert ON CONFLICT
      dedupe otomatis per bucket (tanpa read-modify-write).
   2. Rolling window 6 jam: DELETE sample lebih tua dari 6 jam.
   3. GET /stats = proxy CORS live ke icecast (perilaku lama).
      Catatan: TIDAK lagi mencatat sample per request (waitUntil
      dihapus 2026-08-02) — hanya cron yang menulis, kuota tulis
      jadi terkendali.
   4. GET /stats?history=1 = seluruh time series + snapshot terakhir.

   D1: tabel `samples` (time series), key "last" di tabel `kv_store`.
   ========================================================= */

import { Env, Route, json, d1GetJson, d1SetJson } from "./shared";

const STATS_URL = "http://i.klikhost.com:8502/stats?json=1";
const KV_KEY_LAST = "last";
const WINDOW_SECONDS = 6 * 3600; // rolling 6 jam
const BUCKET_SECONDS = 300; // 5 menit

type Sample = [number, number, number]; // [unix seconds, currentlisteners, streamstatus]

/* Ambil + parse snapshot icecast. Return null jika gagal/terlambat. */
async function fetchIcecast(): Promise<Record<string, unknown> | null> {
	try {
		const res = await fetch(`${STATS_URL}&t=${Date.now()}`, { signal: AbortSignal.timeout(8000) });
		if (!res.ok) return null;
		const data = (await res.json()) as Record<string, unknown>;
		return data;
	} catch {
		return null;
	}
}

function toNumber(v: unknown): number {
	if (typeof v === "number") return v;
	const n = parseInt(String(v ?? ""), 10);
	return Number.isFinite(n) ? n : 0;
}

/* Catat satu sample ke D1: upsert per bucket 5 menit + trim 6 jam.
   Snapshot mentah "last" hanya ditulis jika isinya berubah. */
export async function recordSample(env: Env): Promise<void> {
	const data = await fetchIcecast();
	if (!data) return;

	const now = Math.floor(Date.now() / 1000);
	const listeners = toNumber(data.currentlisteners);
	const status = data.streamstatus === 1 || String(data.streamstatus) === "1" ? 1 : 0;
	const bucketStart = Math.floor(now / BUCKET_SECONDS) * BUCKET_SECONDS;

	try {
		await env.DB.prepare(
			"INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?) " +
				"ON CONFLICT(t) DO UPDATE SET listeners = excluded.listeners, status = excluded.status"
		)
			.bind(bucketStart, listeners, status)
			.run();

		await env.DB.prepare("DELETE FROM samples WHERE t < ?").bind(now - WINDOW_SECONDS).run();
	} catch (e) {
		console.error("recordSample gagal:", e);
		return;
	}

	try {
		const rawLast = await d1GetJson(env.DB, KV_KEY_LAST);
		const newLast = JSON.stringify(data);
		if (JSON.stringify(rawLast) !== newLast) {
			await d1SetJson(env.DB, KV_KEY_LAST, data);
		}
	} catch (e) {
		console.error("recordSample (last) gagal:", e);
	}
}

/* GET /stats?history=1 — time series 6 jam + snapshot terakhir (dipakai dashboard). */
async function handleHistory(_request: Request, env: Env): Promise<Response> {
	const cutoff = Math.floor(Date.now() / 1000) - WINDOW_SECONDS;
	const { results } = await env.DB.prepare(
		"SELECT t, listeners, status FROM samples WHERE t >= ? ORDER BY t"
	)
		.bind(cutoff)
		.all<{ t: number; listeners: number; status: number }>();

	const history: Sample[] = results.map((r) => [r.t, r.listeners, r.status]);
	const last = await d1GetJson(env.DB, KV_KEY_LAST);

	return json({
		window_hours: 6,
		interval_seconds: 300,
		history,
		last,
		generated_at: new Date().toISOString(),
	});
}

/* GET /stats — proxy live (perilaku lama untuk situs utama). Hanya proxy;
   pencatatan sample dilakukan cron 5-menit, bukan per request (keputusan
   2026-08-02: kendalikan kuota tulis D1/KV). */
async function handleStats(request: Request, env: Env): Promise<Response> {
	const url = new URL(request.url);

	// Endpoint riwayat untuk dashboard admin (?history=1)
	if (url.searchParams.has("history") || url.pathname.endsWith("/history")) {
		return handleHistory(request, env);
	}

	try {
		const res = await fetch(`${STATS_URL}&t=${Date.now()}`, { signal: AbortSignal.timeout(8000) });
		const text = await res.text();
		return new Response(text, {
			headers: { "Content-Type": "application/json", "Access-Control-Allow-Origin": "*" },
		});
	} catch {
		return json({ error: "Gagal terhubung ke server radio" }, 500);
	}
}

export const statsRoutes: Route[] = [
	{ method: "GET", pattern: "/stats", handler: handleStats },
];
