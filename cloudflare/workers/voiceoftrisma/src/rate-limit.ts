/* =========================================================
   VOICE OF TRISMA — DUROBJECT RATE LIMITER (lintas-isolate)
   ---------------------------------------------------------
   Limiter anti-DDoS layer-7 yang DI-SHARED lintas isolate.

   Masalah limiter Map in-memory biasa: eksis PER-ISOLATE. Cloudflare
   membagi request beruntun ke banyak isolate, jadi tiap isolate cuma
   melihat sebagian request — botnet / request terpencar tidak tertangkap.

   Solusi: satu durable-object instance dengan ID tetap "Global". Semua
   request worker dirutekan ke instance yang SAMA, sehingga counter per-IP
   dan global benar-benar terpusat. Hitungan murni in-memory (tanpa tulis
   storage per request) → latensi rendah. Kehilangan counter saat instance
   di-evict (idle) hanya mereset jendela — ditolerir; defense lain
   (in-memory per-isolate + login_rl D1) tetap jalan.
   ============================================================= */

import { json } from "./shared";

export class RateLimitDO implements DurableObject {
	private static readonly WINDOW_MS = 10_000; // jendela sliding: 10 detik
	private ipBuckets = new Map<string, number[]>();
	private globalBucket: number[] = [];
	private lastPrune = Date.now();

	/* true bila masih di bawah ambang; false bila melebihi (harus 429). */
	private allowed(bucket: number[], max: number, now: number): boolean {
		// buang stempel yang sudah lewat jendela (sliding window)
		while (bucket.length > 0 && now - bucket[0] > RateLimitDO.WINDOW_MS) bucket.shift();
		if (bucket.length >= max) return false;
		bucket.push(now);
		return true;
	}

	private prune(now: number): void {
		if (now - this.lastPrune < 60_000) return;
		for (const [k, arr] of this.ipBuckets) {
			// buang bucket IP yang sudah diam > 1 menit
			if (arr.length === 0 || now - arr[arr.length - 1] > 60_000) this.ipBuckets.delete(k);
		}
		this.lastPrune = now;
	}

	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		const key = url.searchParams.get("k") || "global";
		const ipMax = Number(url.searchParams.get("m")) || 120;
		const gMax = Number(url.searchParams.get("g")) || 600;
		const now = Date.now();
		this.prune(now);

		let ok = true;
		if (key !== "global" && ok) {
			let arr = this.ipBuckets.get(key);
			if (!arr) {
				arr = [];
				this.ipBuckets.set(key, arr);
			}
			ok = this.allowed(arr, ipMax, now);
		}
		if (ok) ok = this.allowed(this.globalBucket, gMax, now);

		return json({ ok, retryAfterSeconds: ok ? 0 : 10 });
	}
}
