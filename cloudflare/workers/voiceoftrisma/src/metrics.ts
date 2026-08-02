/* =========================================================
   VOICE OF TRISMA — MODUL METRICS (dari voiceoftrisma-metrics-worker)
   ---------------------------------------------------------
   Cron tiap menit: snapshot stats icecast HANYA saat jam siaran
   (15:00–18:59 WITA) → D1 kv_store ("last_stats").
   GET /metrics = kembalikan snapshot terakhir.
   ========================================================= */

import { Env, Route, json, d1GetJson, d1SetJson } from "./shared";

/* Update snapshot. Dilindungi guard jam siaran: hour 15–18 (15:00–18:59
   WITA, UTC+8). Catatan: worker lama memakai `getUTCHours() + 8` tanpa
   modulo — di sini ditambahkan % 24 supaya perhitungan jam WITA selalu
   valid (hasil 24–31 tidak mungkin lagi). Perilaku jendela tidak berubah
   dibanding worker lama. */
export async function updateMetrics(env: Env): Promise<void> {
	const hour = (new Date().getUTCHours() + 8) % 24; // WITA (UTC+8)

	if (hour >= 15 && hour < 19) {
		try {
			const response = await fetch("http://i.klikhost.com:8502/stats?json=1");
			if (response.ok) {
				const data = await response.json();
				await d1SetJson(env.DB, "last_stats", data);
			}
		} catch (e) {
			console.error("Gagal update data:", e);
		}
	}
}

/* GET /metrics — kembalikan snapshot terakhir. */
async function handleMetrics(_request: Request, env: Env): Promise<Response> {
	const cachedData = await d1GetJson(env.DB, "last_stats");

	return json(cachedData || { message: "Data belum tersedia" });
}

export const metricsRoutes: Route[] = [
	{ method: "GET", pattern: "/metrics", handler: handleMetrics },
];
