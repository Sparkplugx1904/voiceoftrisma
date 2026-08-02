interface Env {
	ARCHIVE_KV: KVNamespace;
}

const IA_USER = "@16_i_gede_ananda_pradnyana";
const KV_KEY = "UPLOADS_DATA_FULL";

export default {
	// Jalankan rekap otomatis via Cron
	async scheduled(event: ScheduledEvent, env: Env, ctx: ExecutionContext) {
		ctx.waitUntil(this.updateArchiveCache(env));
	},

	// Layani request API untuk Frontend
	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		const url = new URL(request.url);
		const corsHeaders = {
			"Access-Control-Allow-Origin": "*",
			"Content-Type": "application/json"
		};

		if (request.method === "OPTIONS") return new Response(null, { headers: corsHeaders });

		const rawData = await env.ARCHIVE_KV.get(KV_KEY);
		if (!rawData) {
			return new Response(JSON.stringify({ error: "Cache empty or warming up." }), { status: 404, headers: corsHeaders });
		}

		let items: any[] = [];
		try {
			const parsed = JSON.parse(rawData);
			items = parsed.items || [];
		} catch (e) {
			return new Response(JSON.stringify({ error: "Cache corrupt." }), { status: 500, headers: corsHeaders });
		}

		// Params
		const query = url.searchParams.get("query")?.toLowerCase() || "";
		const sort = url.searchParams.get("sort") || "publicdate:desc";
		const rawHitsPerPage = url.searchParams.get("hits_per_page") || "24";
		const page = Math.max(1, parseInt(url.searchParams.get("page") || "1"));

		// Filter
		if (query) {
			items = items.filter(item =>
				(item.title?.toLowerCase().includes(query)) ||
				(item.id?.toLowerCase().includes(query)) ||
				(item.subject?.some((s: string) => s.toLowerCase().includes(query)))
			);
		}

		// Sort
		const [field, direction] = sort.split(":");
		items.sort((a, b) => {
			let valA = a[field] ?? 0;
			let valB = b[field] ?? 0;
			if (typeof valA === 'number') return direction === "desc" ? valB - valA : valA - valB;
			return direction === "desc" ? String(valB).localeCompare(String(valA)) : String(valA).localeCompare(String(valB));
		});

		// Pagination
		const totalItems = items.length;
		let resultItems = [];
		let hitsLimit = totalItems;

		if (rawHitsPerPage !== "all") {
			hitsLimit = parseInt(rawHitsPerPage) || 24;
			resultItems = items.slice((page - 1) * hitsLimit, page * hitsLimit);
		} else {
			resultItems = items;
		}

		return new Response(JSON.stringify({
			status: "success",
			total_results: totalItems,
			current_page: page,
			total_pages: Math.ceil(totalItems / hitsLimit),
			data: resultItems
		}), { headers: corsHeaders });
	},

	// Fungsi Scraper (Internal)
	async updateArchiveCache(env: Env) {
		let allHits: any[] = [];
		let page = 1;
		let totalHits: number | null = null;

		try {
			while (true) {
				const resp = await fetch(`https://archive.org/services/search/beta/page_production/?page_type=account_details&page_target=${IA_USER}&page_elements=[%22uploads%22]&hits_per_page=1000&page=${page}`);
				const json: any = await resp.json();
				const uploads = json?.response?.body?.page_elements?.uploads;
				const hits = uploads?.hits?.hits || [];

				if (totalHits === null) totalHits = uploads?.hits?.total || 0;

				allHits = [...allHits, ...hits.map((h: any) => ({
					id: h.fields.identifier,
					title: h.fields.title || h.fields.identifier,
					publicdate: h.fields.publicdate,
					downloads: h.fields.downloads || 0,
					subject: h.fields.subject || [],
					creator: h.fields.creator || []
				}))];

				if (hits.length === 0 || allHits.length >= (totalHits || 0)) break;
				page++;
			}
			await env.ARCHIVE_KV.put(KV_KEY, JSON.stringify({ items: allHits }));
		} catch (e) { console.error(e); }
	}
};