export interface Env {
	VOT_METRICS_STORE: KVNamespace;
}

export default {
	async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
		const hour = new Date().getUTCHours() + 8; // WITA

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
	},

	async fetch(request: Request, env: Env, ctx: ExecutionContext) {
		const cachedData = await env.VOT_METRICS_STORE.get("last_stats");

		return new Response(cachedData || JSON.stringify({ message: "Data belum tersedia" }), {
			headers: {
				"content-type": "application/json",
				"Access-Control-Allow-Origin": "*"
			},
		});
	},
};