import {
	env,
	createExecutionContext,
	waitOnExecutionContext,
} from "cloudflare:test";
import { describe, it, expect } from "vitest";
import worker, { mergeSample } from "../src/index";

const IncomingRequest = Request<unknown, IncomingRequestCfProperties>;

describe("voiceoftrisma-stream-stats — pure logic (mergeSample)", () => {
	it("sample pertama ditambahkan", () => {
		const out = mergeSample([], 1000, 5, 1);
		expect(out).toEqual([[1000, 5, 1]]);
	});

	it("dedupe: sample kedua dalam bucket 5 menit yang sama meng-update, tidak menambah", () => {
		let out = mergeSample([], 1000, 5, 1);
		out = mergeSample(out, 1000 + 60, 9, 1); // +1 menit, bucket sama
		expect(out.length).toBe(1);
		expect(out[0]).toEqual([1060, 9, 1]); // listeners di-refresh
	});

	it("bucket berbeda (>5 menit) menambah sample baru", () => {
		let out = mergeSample([], 1000, 5, 1);
		out = mergeSample(out, 1000 + 301, 7, 0); // +5 menit 1 detik
		expect(out.length).toBe(2);
		expect(out[1]).toEqual([1301, 7, 0]);
	});

	it("trim: sample lebih tua dari 6 jam dibuang", () => {
		const now = 1_000_000;
		const old = now - 7 * 3600; // 7 jam lalu
		let out = mergeSample([[old, 3, 1]], now, 8, 1);
		expect(out.length).toBe(1);
		expect(out[0]).toEqual([now, 8, 1]);
	});

	it("trim: sample tepat di batas 6 jam tetap disimpan", () => {
		const now = 1_000_000;
		const edge = now - 6 * 3600;
		let out = mergeSample([[edge, 3, 1]], now, 8, 1);
		expect(out.length).toBe(2);
	});

	it("status offline (0) tersimpan sebagai angka", () => {
		const out = mergeSample([], 500, 0, 0);
		expect(out[0]).toEqual([500, 0, 0]);
	});
});

describe("voiceoftrisma-stream-stats — endpoint", () => {
	it("?history=1 mengembalikan shape riwayat", async () => {
		const request = new IncomingRequest("http://example.com/?history=1");
		const ctx = createExecutionContext();
		const response = await worker.fetch(request, env, ctx);
		await waitOnExecutionContext(ctx);
		expect(response.status).toBe(200);
		const data = await response.json();
		expect(data).toMatchObject({
			window_hours: 6,
			interval_seconds: 300,
			history: [],
			last: null,
		});
	});

	it("OPTIONS preflight -> 204 + header CORS", async () => {
		const request = new IncomingRequest("http://example.com/", { method: "OPTIONS" });
		const response = await worker.fetch(request, env, createExecutionContext());
		expect(response.status).toBe(204);
		expect(response.headers.get("Access-Control-Allow-Origin")).toBe("*");
	});

	it("history endpoint tidak membutuhkan auth & mengembalikan CORS", async () => {
		const request = new IncomingRequest("http://example.com/?history=1");
		const response = await worker.fetch(request, env, createExecutionContext());
		expect(response.status).toBe(200);
		expect(response.headers.get("Access-Control-Allow-Origin")).toBe("*");
	});

	it("scheduled + KV round-trip: sample tercatat di history (fetch live diblokir di test, jadi sample mungkin kosong — path KV diverifikasi via mergeSample + produksi)", async () => {
		// Catat langsung ke KV dengan format yang sama seperti recordSample
		const samples = mergeSample([], Math.floor(Date.now() / 1000), 4, 1);
		await env.VOT_STREAM_STATS.put("samples", JSON.stringify(samples));

		const request = new IncomingRequest("http://example.com/?history=1");
		const response = await worker.fetch(request, env, createExecutionContext());
		const data = await response.json();
		expect(data.history.length).toBe(1);
		expect(data.history[0]).toEqual([expect.any(Number), 4, 1]);
	});
});
