/* =========================================================
   VOICE OF TRISMA — STREAM STATS RECORDER
   ---------------------------------------------------------
   Tugas:
     1. Cron tiap 5 menit: poll icecast Klikhost
        (http://i.klikhost.com:8502/stats?json=1) lalu CATAT
        sample { t, currentlisteners, streamstatus } ke KV.
     2. Rolling window 6 jam: sample lebih tua dari 6 jam
        dibuang supaya nilai KV tetap kecil (format padat
        [t, listeners, status] → ~1,5 KB untuk 72 sample).
     3. Fetch (dipakai situs utama): tetap jadi proxy CORS
        live ke icecast (perilaku lama TIDAK berubah), plus
        catat sample di background (waitUntil).
     4. ?history=1 (dipakai dashboard admin untuk grafik):
        kembalikan seluruh time series + snapshot terakhir.

   Key KV:
     - "samples" : [[t, listeners, streamstatus], ...] terurut naik
     - "last"    : snapshot JSON mentah icecast terakhir
   ========================================================= */

export interface Env {
	VOT_STREAM_STATS: KVNamespace;
}

const STATS_URL = "http://i.klikhost.com:8502/stats?json=1";
const KV_KEY_SAMPLES = "samples";
const KV_KEY_LAST = "last";
const WINDOW_SECONDS = 6 * 3600; // rolling 6 jam
const BUCKET_SECONDS = 300; // 5 menit

type Sample = [number, number, number]; // [unix seconds, currentlisteners, streamstatus]

const CORS_HEADERS: Record<string, string> = {
	"Access-Control-Allow-Origin": "*",
	"Access-Control-Allow-Methods": "GET, OPTIONS",
	"Access-Control-Allow-Headers": "Content-Type",
	"Cache-Control": "no-store",
};

function json(data: unknown, status = 200): Response {
	return new Response(JSON.stringify(data), {
		status,
		headers: { "Content-Type": "application/json; charset=utf-8", ...CORS_HEADERS },
	});
}

async function getSamples(env: Env): Promise<Sample[]> {
	const raw = await env.VOT_STREAM_STATS.get(KV_KEY_SAMPLES);
	if (!raw) return [];
	try {
		const v = JSON.parse(raw);
		return Array.isArray(v) ? (v as Sample[]) : [];
	} catch {
		return [];
	}
}

/* Ambil + parse snapshot icecast. Return null jika gagal. */
async function fetchIcecast(): Promise<Record<string, unknown> | null> {
	try {
		const res = await fetch(`${STATS_URL}&t=${Date.now()}`);
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

/* Pure logic: gabungkan sample baru ke deret, dedupe per bucket 5 menit,
   trim sample lebih tua dari 6 jam. Diekspor agar bisa di-unit-test. */
export function mergeSample(samples: Sample[], now: number, listeners: number, status: number): Sample[] {
	const bucket = Math.floor(now / BUCKET_SECONDS);
	const last = samples[samples.length - 1];

	if (last && Math.floor(last[0] / BUCKET_SECONDS) === bucket) {
		// Update sample dalam bucket yang sama (refresh listener count)
		samples[samples.length - 1] = [now, listeners, status];
	} else {
		samples.push([now, listeners, status]);
	}

	// Trim: buang sample lebih tua dari 6 jam
	const cutoff = now - WINDOW_SECONDS;
	while (samples.length > 0 && samples[0][0] < cutoff) samples.shift();

	return samples;
}

/* Catat satu sample ke KV (dedupe per bucket 5 menit, trim 6 jam).
   HEMAT KUOTA PUT: (a) skip tulis samples jika nilai tidak berubah dalam
   bucket yang sama — tidak ada informasi baru; bucket BARU tetap ditulis
   karena grafik butuh titik waktu. (b) snapshot mentah "last" hanya
   ditulis jika isinya benar-benar berubah (bandingkan string JSON). */
async function recordSample(env: Env): Promise<void> {
	const data = await fetchIcecast();
	if (!data) return;

	const now = Math.floor(Date.now() / 1000);
	const listeners = toNumber(data.currentlisteners);
	const status = data.streamstatus === 1 || String(data.streamstatus) === "1" ? 1 : 0;

	const prevSamples = await getSamples(env);
	const prevLast = prevSamples[prevSamples.length - 1];
	const samples = mergeSample(prevSamples, now, listeners, status);

	const sameBucket =
		prevLast !== undefined && Math.floor(prevLast[0] / BUCKET_SECONDS) === Math.floor(now / BUCKET_SECONDS);
	const valueUnchanged = prevLast !== undefined && prevLast[1] === listeners && prevLast[2] === status;

	if (!(sameBucket && valueUnchanged)) {
		await env.VOT_STREAM_STATS.put(KV_KEY_SAMPLES, JSON.stringify(samples));
	}

	const rawLast = await env.VOT_STREAM_STATS.get(KV_KEY_LAST);
	const newLast = JSON.stringify(data);
	if (rawLast !== newLast) {
		await env.VOT_STREAM_STATS.put(KV_KEY_LAST, newLast);
	}
}

export default {
	/* Cron: tiap 5 menit */
	async scheduled(_event: ScheduledEvent, env: Env): Promise<void> {
		await recordSample(env);
	},

	async fetch(request: Request, env: Env, ctx: ExecutionContext): Promise<Response> {
		const url = new URL(request.url);

		// Preflight CORS
		if (request.method === "OPTIONS") {
			return new Response(null, { status: 204, headers: CORS_HEADERS });
		}

		// Endpoint riwayat untuk dashboard admin (?history=1)
		if (url.searchParams.has("history") || url.pathname.endsWith("/history")) {
			const samples = await getSamples(env);
			const rawLast = await env.VOT_STREAM_STATS.get(KV_KEY_LAST);
			let last: unknown = null;
			if (rawLast) {
				try {
					last = JSON.parse(rawLast);
				} catch {
					last = null;
				}
			}
			return json({
				window_hours: 6,
				interval_seconds: 300,
				history: samples,
				last,
				generated_at: new Date().toISOString(),
			});
		}

		// Proxy live (perilaku lama untuk situs utama) + catat sample di background
		ctx.waitUntil(recordSample(env));

		try {
			const res = await fetch(`${STATS_URL}&t=${Date.now()}`);
			const text = await res.text();
			return new Response(text, {
				headers: { "Content-Type": "application/json", ...CORS_HEADERS },
			});
		} catch {
			return json({ error: "Gagal terhubung ke server radio" }, 500);
		}
	},
};
