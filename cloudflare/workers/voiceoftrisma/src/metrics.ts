/* =========================================================
   VOICE OF TRISMA — MODUL METRICS (dari voiceoftrisma-metrics-worker)
   ---------------------------------------------------------
   Cron tiap menit: snapshot stats icecast HANYA saat jam siaran
   (15:00–19:00 WITA) → KV VOT_METRICS_STORE ("last_stats").
   GET /metrics = kembalikan snapshot terakhir.
   ========================================================= */

import { Env, Route } from "./shared";

/* Update snapshot. Dilindungi guard jam siaran (15:00–19:00 WITA).
   Catatan: worker lama menulis `getUTCHours() + 8` tanpa modulo — untuk
   UTC 16–23 menghasilkan nilai 24–31 sehingga guard salah di luar jendela;
   di sini ditambahkan % 24 supaya perhitungan jam WITA selalu valid. */
export async function updateMetrics(env: Env): Promise<void> {
	const hour = (new Date().getUTCHours() + 8) % 24; // WITA (UTC+8)

	if (hour >= 15 && hour < 19) {
		try {
			const response = await fetch("http://i.klikhost.com:8502/stats?json=1");
			if (response.ok) {
				const data = await response.json();
				await env.VOT_METRICS_STORE.put("last_stats", JSON.stringify(data));
			}
		} catch (e) {
			console.error("Gagal update data:", e);
		}
	}
}

/* GET /metrics — kembalikan snapshot terakhir. */
async function handleMetrics(_request: Request, env: Env): Promise<Response> {
	const cachedData = await env.VOT_METRICS_STORE.get("last_stats");

	return new Response(cachedData || JSON.stringify({ message: "Data belum tersedia" }), {
		headers: {
			"content-type": "application/json",
			"Access-Control-Allow-Origin": "*",
		},
	});
}

export const metricsRoutes: Route[] = [
	{ method: "GET", pattern: "/metrics", handler: handleMetrics },
];
