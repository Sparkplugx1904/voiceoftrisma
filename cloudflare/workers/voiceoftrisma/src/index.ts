/* =========================================================
   VOICE OF TRISMA — WORKER GABUNGAN (router + cron)
   ---------------------------------------------------------
   Satu entry point untuk semua modul. Route prefix:
     /api/*     admin        (voiceoftrisma-admin-worker)
     /stats     stream-stats (voiceoftrisma-stream-stats)
     /archive   archive      (archive-cache-worker)
     /metrics   metrics      (voiceoftrisma-metrics-worker)
     /workflow  workflow     (workflow-trigger)

   *   Cron (named triggers) di-routing lewat event.cron:
   *     stream-stats setiap 5 menit · archive setiap 30 menit ·
   *     workflow 07:00 UTC · metrics setiap menit
   ========================================================= */

import { Env, Route, CORS_HEADERS, json, withCors } from "./shared";
import { adminRoutes } from "./admin";
import { statsRoutes, recordSample } from "./stream-stats";
import { archiveRoutes, updateArchiveCache } from "./archive";
import { metricsRoutes, updateMetrics } from "./metrics";
import { workflowRoutes, triggerWorkflows } from "./workflow";

function handleRoot(_request: Request, _env: Env): Response {
	return json({
		ok: true,
		service: "voiceoftrisma",
		endpoints: ["/api/*", "/stats", "/archive", "/metrics", "/workflow"],
		time: new Date().toISOString(),
	});
}

const ROUTES: Route[] = [
	{ method: "GET", pattern: "/", handler: handleRoot },
	...adminRoutes,
	...statsRoutes,
	...archiveRoutes,
	...metricsRoutes,
	...workflowRoutes,
];

const compiledRoutes = ROUTES.map((r) => ({ ...r, pattern: new URLPattern({ pathname: r.pattern }) }));

export default {
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		const url = new URL(request.url);

		// Normalisasi: buang trailing slash supaya "/stats/" == "/stats"
		// (dashboard & archive.js memakai trailing slash).
		const normalized = new URL(url.href);
		if (normalized.pathname.length > 1 && normalized.pathname.endsWith("/")) {
			normalized.pathname = normalized.pathname.slice(0, -1);
		}

		// Preflight CORS
		if (request.method === "OPTIONS") {
			return new Response(null, { status: 204, headers: CORS_HEADERS });
		}

		for (const route of compiledRoutes) {
			if (route.method !== request.method) continue;
			if (!route.pattern.exec(normalized)) continue;

			try {
				return withCors(await route.handler(request, env, ctx));
			} catch (err) {
				console.error("Unhandled error:", err);
				return withCors(json({ error: "Internal server error" }, 500));
			}
		}

		return withCors(json({ error: "Not found" }, 404));
	},

	async scheduled(event: ScheduledEvent, env: Env): Promise<void> {
		// event.cron berisi string cron (named trigger tidak didukung endpoint
		// schedules akun ini — pakai string cron biasa sebagai kunci routing).
		switch (event.cron) {
			case "*/5 * * * *":
				await recordSample(env);
				break;
			case "*/30 * * * *":
				await updateArchiveCache(env);
				break;
			case "0 7 * * *":
				await triggerWorkflows(env);
				break;
			case "* * * * *":
				await updateMetrics(env);
				break;
			default:
				console.error("Cron tidak dikenal:", event.cron);
		}
	},
};
