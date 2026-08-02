import { env, createExecutionContext, waitOnExecutionContext, SELF, applyD1Migrations } from "cloudflare:test";
import { beforeAll, describe, it, expect } from "vitest";
import { adminRoutes } from "../src/admin";
import { verifyToken, signToken } from "../src/shared";

const IncomingRequest = Request<unknown, IncomingRequestCfProperties>;

/* D1 tiruan untuk unit test handler (get → null, semua op no-op). */
function fakeD1() {
	return {
		prepare: () => ({
			bind: () => ({
				first: async () => null,
				all: async () => ({ results: [] }),
				run: async () => ({}),
			}),
		}),
	} as unknown as D1Database;
}

function fakeEnv() {
	return {
		DB: fakeD1(),
		VOT_ADMIN_STORE: null,
		VOT_STREAM_STATS: null,
		ARCHIVE_KV: null,
		VOT_METRICS_STORE: null,
		ADMIN_USERNAME: "test-admin",
		ADMIN_PASSWORD: "test-pass",
		SESSION_SECRET: "test-secret-for-unit",
		GITHUB_TOKEN: "test-token",
	};
}

/* Env tanpa secrets — harus ditolak oleh guard (jangan bandingkan "undefined"). */
function fakeEnvNoSecrets() {
	return { ...fakeEnv(), ADMIN_USERNAME: "", ADMIN_PASSWORD: "", SESSION_SECRET: "" };
}

/* Skema D1 untuk database test in-memory (cermin migrations/0001_init.sql). */
const MIGRATIONS = [
	{
		name: "0001_init",
		queries: [
			"CREATE TABLE IF NOT EXISTS kv_store (key TEXT PRIMARY KEY, value TEXT NOT NULL, updated_at INTEGER NOT NULL) WITHOUT ROWID",
			"CREATE TABLE IF NOT EXISTS samples (t INTEGER PRIMARY KEY, listeners INTEGER NOT NULL, status INTEGER NOT NULL) WITHOUT ROWID",
		],
	},
];

beforeAll(async () => {
	// Terapkan skema D1 (kv_store + samples) ke database test in-memory.
	await applyD1Migrations(env.DB, MIGRATIONS);
});

describe("samples D1 (time-series)", () => {
	it("history hanya memuat sample dalam 6 jam terakhir, terurut naik", async () => {
		const now = Math.floor(Date.now() / 1000);
		// seed: 1 sample lama (>6 jam) + 2 sample baru
		await env.DB.prepare("INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?)")
			.bind(now - 7 * 3600, 9, 1)
			.run();
		await env.DB.prepare("INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?)")
			.bind(now - 600, 2, 1)
			.run();
		await env.DB.prepare("INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?)")
			.bind(now - 300, 5, 1)
			.run();

		const res = await SELF.fetch("https://example.com/stats?history=1");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(Array.isArray(body.history)).toBe(true);
		// sample tua ter-trim oleh query WHERE t >= cutoff
		expect(body.history).toHaveLength(2);
		expect(body.history[0][1]).toBe(2);
		expect(body.history[1][1]).toBe(5);
	});

	it("upsert per bucket: nilai terakhir menang (dedupe)", async () => {
		const t = Math.floor(Date.now() / 1000 / 300) * 300; // awal bucket saat ini
		await env.DB.prepare("INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?) ON CONFLICT(t) DO UPDATE SET listeners = excluded.listeners, status = excluded.status")
			.bind(t, 3, 1)
			.run();
		await env.DB.prepare("INSERT INTO samples (t, listeners, status) VALUES (?, ?, ?) ON CONFLICT(t) DO UPDATE SET listeners = excluded.listeners, status = excluded.status")
			.bind(t, 7, 1)
			.run();

		const { results } = await env.DB.prepare("SELECT t, listeners, status FROM samples WHERE t = ?")
			.bind(t)
			.all<{ t: number; listeners: number; status: number }>();
		expect(results).toHaveLength(1);
		expect(results[0].listeners).toBe(7);
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

	it("POST /api/login saat secrets belum terpasang -> 503 (guard)", async () => {
		const loginRoute = adminRoutes.find((r) => r.method === "POST" && r.pattern === "/api/login");
		expect(loginRoute).toBeDefined();
		const request = new IncomingRequest("https://example.com/api/login", {
			method: "POST",
			headers: { "Content-Type": "application/json" },
			body: JSON.stringify({ username: "undefined", password: "undefined" }),
		});
		const res = await loginRoute!.handler(request, fakeEnvNoSecrets() as any, createExecutionContext());
		expect(res.status).toBe(503);
	});

	it("verifyToken tanpa SESSION_SECRET -> null (guard)", async () => {
		const token = await signToken(fakeEnv() as any, "test-admin");
		const verified = await verifyToken(fakeEnvNoSecrets() as any, token);
		expect(verified).toBeNull();
	});

	it("GET /api/jadwal -> seed default saat D1 kosong", async () => {
		const res = await SELF.fetch("https://example.com/api/jadwal");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(typeof body.jadwal).toBe("object");
	});

	it("GET /stats?history=1 -> struktur history (D1 kosong)", async () => {
		const res = await SELF.fetch("https://example.com/stats?history=1");
		expect(res.status).toBe(200);
		const body = await res.json();
		expect(Array.isArray(body.history)).toBe(true);
	});

	it("GET /metrics -> data belum tersedia saat D1 kosong", async () => {
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
