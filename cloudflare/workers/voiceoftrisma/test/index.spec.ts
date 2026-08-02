import { env, createExecutionContext, waitOnExecutionContext, SELF } from "cloudflare:test";
import { describe, it, expect } from "vitest";
import { mergeSample } from "../src/stream-stats";
import { adminRoutes } from "../src/admin";
import { verifyToken } from "../src/shared";

const IncomingRequest = Request<unknown, IncomingRequestCfProperties>;

/* KV tiruan untuk unit test handler (get → null, put → no-op). */
function fakeKv() {
	return {
		get: async () => null,
		put: async () => {},
		list: async () => ({ keys: [] }),
		delete: async () => {},
	} as unknown as KVNamespace;
}

function fakeEnv() {
	return {
		VOT_ADMIN_STORE: fakeKv(),
		VOT_STREAM_STATS: fakeKv(),
		ARCHIVE_KV: fakeKv(),
		VOT_METRICS_STORE: fakeKv(),
		ADMIN_USERNAME: "test-admin",
		ADMIN_PASSWORD: "test-pass",
		SESSION_SECRET: "test-secret-for-unit",
		GITHUB_TOKEN: "test-token",
	};
}

describe("mergeSample (pure logic)", () => {
	it("menambahkan sample baru di bucket berbeda", () => {
		const out = mergeSample([], 1_700_000_000, 5, 1);
		expect(out).toEqual([[1_700_000_000, 5, 1]]);
	});

	it("meng-update sample dalam bucket yang sama", () => {
		const base = 1_700_000_000;
		const t0 = base - (base % 300); // awal bucket
		const out = mergeSample([[t0, 3, 1]], t0 + 120, 9, 1);
		expect(out).toHaveLength(1);
		expect(out[0][1]).toBe(9);
	});

	it("trim sample lebih tua dari 6 jam", () => {
		const now = 1_700_000_000;
		const cutoff = now - 6 * 3600;
		const out = mergeSample(
			[[cutoff - 1, 1, 1], [now - 600, 2, 1]],
			now,
			4,
			1
		);
		expect(out.map((s) => s[0])).toEqual([now - 600, now]);
	});
});

describe("router worker gabungan", () => {
	it("GET / -> info root", async () => {
		const res = await SELF.fetch("https://example.com/");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.service).toBe("voiceoftrisma");
	});

	it("GET /api/health -> 200 + CORS", async () => {
		const res = await SELF.fetch("https://example.com/api/health");
		expect(res.status).toBe(200);
		expect(res.headers.get("Access-Control-Allow-Origin")).toBe("*");
	});

	it("POST /api/login dengan kredensial test -> token (unit test handler)", async () => {
		const loginRoute = adminRoutes.find((r) => r.method === "POST" && r.pattern === "/api/login");
		expect(loginRoute).toBeDefined();
		const request = new IncomingRequest("https://example.com/api/login", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ username: "test-admin", password: "test-pass" }),
		});
		const res = await loginRoute!.handler(request, fakeEnv() as any, createExecutionContext());
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(typeof body.token).toBe("string");
		// Token harus bisa diverifikasi ulang (stateless, HMAC).
		const verified = await verifyToken(fakeEnv() as any, body.token);
		expect(verified?.u).toBe("test-admin");
	});

	it("POST /api/login salah -> 401", async () => {
		const res = await SELF.fetch("https://example.com/api/login", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ username: "test-admin", password: "salah" }),
		});
		expect(res.status).toBe(401);
	});

	it("GET /api/jadwal -> seed default saat KV kosong", async () => {
		const res = await SELF.fetch("https://example.com/api/jadwal");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(typeof body.jadwal).toBe("object");
	});

	it("GET /stats?history=1 -> struktur history (KV kosong)", async () => {
		const res = await SELF.fetch("https://example.com/stats?history=1");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(Array.isArray(body.history)).toBe(true);
	});

	it("GET /metrics -> data belum tersedia saat KV kosong", async () => {
		const res = await SELF.fetch("https://example.com/metrics");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(body.message).toBe("Data belum tersedia");
	});

	it("GET /workflow -> info", async () => {
		const res = await SELF.fetch("https://example.com/workflow");
		expect(res.status).toBe(200);
	});

	it("route tak dikenal -> 404", async () => {
		const res = await SELF.fetch("https://example.com/tidak-ada");
		expect(res.status).toBe(404);
	});

	it("OPTIONS -> 204 + CORS headers", async () => {
		const res = await SELF.fetch("https://example.com/api/login", { method: "OPTIONS" });
		expect(res.status).toBe(204);
		expect(res.headers.get("Access-Control-Allow-Methods")).toContain("POST");
	});
});
