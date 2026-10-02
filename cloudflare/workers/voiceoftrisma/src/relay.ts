/* =========================================================
   VOICE OF TRISMA — RELAY (komunikasi aman antar VM runner)
   ---------------------------------------------------------
   Tiga endpoint relay, semua dilindungi HMAC-SHA256:

   POST /relay/trigger  — Runner A minta Worker dispatch Runner B
   POST /relay/ready    — Runner B umumkan dirinya sudah mulai rekam
   GET  /relay/status   — Runner A poll: apakah B sudah siap?

   Keamanan:
   - Setiap request wajib membawa header X-Relay-Sig: sha256=<HMAC>
   - HMAC dihitung atas body JSON (atau query string untuk GET) dengan
     kunci RELAY_SECRET yang hanya ada di Cloudflare Secrets + GH Secrets
   - Verifikasi konstan-waktu (constant-time) via Web Crypto API
   ========================================================= */

import { Env, Route, json } from "./shared";

const GITHUB_OWNER = "Sparkplugx1904";
const GITHUB_REPO  = "voiceoftrisma";
const WORKFLOW_V3  = "main+transcript_v3.0.yml";

/* -------- HMAC-SHA256 -------- */

async function hmacSha256Hex(secret: string, data: string): Promise<string> {
	const key = await crypto.subtle.importKey(
		"raw",
		new TextEncoder().encode(secret),
		{ name: "HMAC", hash: "SHA-256" },
		false,
		["sign"],
	);
	const sig = await crypto.subtle.sign("HMAC", key, new TextEncoder().encode(data));
	return "sha256=" + Array.from(new Uint8Array(sig))
		.map((b) => b.toString(16).padStart(2, "0"))
		.join("");
}

/** Verifikasi HMAC konstan-waktu. Kembalikan true bila valid. */
async function verifyRelaySig(secret: string, data: string, sig: string): Promise<boolean> {
	if (!sig || !sig.startsWith("sha256=")) return false;
	try {
		const expected = await hmacSha256Hex(secret, data);
		// constant-time compare: bandingkan karakter demi karakter
		if (expected.length !== sig.length) return false;
		let diff = 0;
		for (let i = 0; i < expected.length; i++) {
			diff |= expected.charCodeAt(i) ^ sig.charCodeAt(i);
		}
		return diff === 0;
	} catch {
		return false;
	}
}

function missingSecret(env: Env): Response | null {
	if (!env.RELAY_SECRET) {
		console.error("[RELAY] RELAY_SECRET belum dikonfigurasi di Worker secrets.");
		return json({ ok: false, error: "RELAY_SECRET_MISSING" }, 500);
	}
	return null;
}

/* ================================================================
   POST /relay/trigger
   Body JSON: { rantai_id, nomor_b, tumpang, margin, sesi, induk_run_id }
   Header: X-Relay-Sig: sha256=<HMAC dari body>

   Dispatch Runner B ke GitHub Actions.
   ================================================================ */
async function handleRelayTrigger(request: Request, env: Env): Promise<Response> {
	const guard = missingSecret(env);
	if (guard) return guard;

	let bodyText: string;
	try {
		bodyText = await request.text();
	} catch {
		return json({ ok: false, error: "INVALID_BODY" }, 400);
	}

	const sig = request.headers.get("X-Relay-Sig") || "";
	if (!(await verifyRelaySig(env.RELAY_SECRET, bodyText, sig))) {
		console.warn("[RELAY] /trigger — tanda tangan tidak valid, tolak.");
		return json({ ok: false, error: "UNAUTHORIZED" }, 401);
	}

	let body: Record<string, unknown>;
	try {
		body = JSON.parse(bodyText);
	} catch {
		return json({ ok: false, error: "INVALID_JSON" }, 400);
	}

	const { rantai_id, nomor_b, tumpang, margin, sesi, induk_run_id } = body as Record<string, unknown>;
	if (!rantai_id || !nomor_b) {
		return json({ ok: false, error: "MISSING_FIELDS" }, 400);
	}

	const token: string | undefined = env.GITHUB_TOKEN;
	if (!token) {
		console.error("[RELAY] GITHUB_TOKEN missing.");
		return json({ ok: false, error: "GITHUB_TOKEN_MISSING" }, 500);
	}

	// Buat baris relay_signals dengan siap_pada = NULL (B belum siap)
	try {
		await env.DB.prepare(
			"INSERT INTO relay_signals (rantai_id, nomor, run_id, siap_pada) VALUES (?, ?, NULL, NULL) " +
			"ON CONFLICT(rantai_id, nomor) DO NOTHING"
		).bind(String(rantai_id), Number(nomor_b)).run();
	} catch (e) {
		console.warn("[RELAY] Gagal insert relay_signals:", e);
	}

	// Dispatch ke GitHub Actions
	const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(WORKFLOW_V3)}/dispatches`;
	const inputs = {
		rantai_id:    String(rantai_id),
		nomor:        String(nomor_b),
		tumpang:      String(tumpang ?? 900),
		margin:       String(margin ?? 300),
		sesi:         String(sesi ?? 1),
		induk_run_id: String(induk_run_id ?? ""),
		perintah:     "jalan",
		mau_transkrip: "false",
	};

	try {
		const res = await fetch(url, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
				Accept: "application/vnd.github+json",
				"User-Agent": "voiceoftrisma-relay/3.0",
			},
			body: JSON.stringify({ ref: "main", inputs }),
			signal: AbortSignal.timeout(15000),
		});
		if (res.ok) {
			console.log(`[RELAY] Runner #${nomor_b} berhasil dipicu (HTTP ${res.status}).`);
			return json({ ok: true, nomor_b, sesi });
		}
		const errText = await res.text().catch(() => "");
		console.error(`[RELAY] Dispatch gagal HTTP ${res.status}: ${errText.slice(0, 200)}`);
		return json({ ok: false, error: `DISPATCH_HTTP_${res.status}` }, 502);
	} catch (e) {
		console.error("[RELAY] Dispatch exception:", e);
		return json({ ok: false, error: "DISPATCH_ERROR" }, 502);
	}
}

/* ================================================================
   POST /relay/ready
   Body JSON: { rantai_id, nomor, run_id }
   Header: X-Relay-Sig: sha256=<HMAC dari body>

   Runner B umumkan dirinya sudah mulai merekam.
   ================================================================ */
async function handleRelayReady(request: Request, env: Env): Promise<Response> {
	const guard = missingSecret(env);
	if (guard) return guard;

	let bodyText: string;
	try {
		bodyText = await request.text();
	} catch {
		return json({ ok: false, error: "INVALID_BODY" }, 400);
	}

	const sig = request.headers.get("X-Relay-Sig") || "";
	if (!(await verifyRelaySig(env.RELAY_SECRET, bodyText, sig))) {
		console.warn("[RELAY] /ready — tanda tangan tidak valid, tolak.");
		return json({ ok: false, error: "UNAUTHORIZED" }, 401);
	}

	let body: Record<string, unknown>;
	try {
		body = JSON.parse(bodyText);
	} catch {
		return json({ ok: false, error: "INVALID_JSON" }, 400);
	}

	const { rantai_id, nomor, run_id } = body as Record<string, unknown>;
	if (!rantai_id || !nomor) {
		return json({ ok: false, error: "MISSING_FIELDS" }, 400);
	}

	const siapPada = Date.now();
	try {
		await env.DB.prepare(
			"INSERT INTO relay_signals (rantai_id, nomor, run_id, siap_pada) VALUES (?, ?, ?, ?) " +
			"ON CONFLICT(rantai_id, nomor) DO UPDATE SET run_id = excluded.run_id, siap_pada = excluded.siap_pada"
		).bind(String(rantai_id), Number(nomor), String(run_id ?? ""), siapPada).run();
	} catch (e) {
		console.error("[RELAY] Gagal update relay_signals:", e);
		return json({ ok: false, error: "DB_ERROR" }, 500);
	}

	console.log(`[RELAY] Runner #${nomor} (rantai ${rantai_id}) siap merekam pada ${siapPada}.`);
	return json({ ok: true, rantai_id, nomor, siap_pada: siapPada });
}

/* ================================================================
   GET /relay/status?rantai_id=X&nomor=2
   Header: X-Relay-Sig: sha256=<HMAC dari "rantai_id=X&nomor=2">

   Runner A poll: apakah B sudah siap?
   Kembalikan { siap: true/false, siap_pada? }
   ================================================================ */
async function handleRelayStatus(request: Request, env: Env): Promise<Response> {
	const guard = missingSecret(env);
	if (guard) return guard;

	const url = new URL(request.url);
	const rantai_id = url.searchParams.get("rantai_id") || "";
	const nomor = url.searchParams.get("nomor") || "";
	// Data yang di-HMAC: canonical query string
	const data = `rantai_id=${rantai_id}&nomor=${nomor}`;

	const sig = request.headers.get("X-Relay-Sig") || "";
	if (!(await verifyRelaySig(env.RELAY_SECRET, data, sig))) {
		console.warn("[RELAY] /status — tanda tangan tidak valid, tolak.");
		return json({ ok: false, error: "UNAUTHORIZED" }, 401);
	}

	if (!rantai_id || !nomor) {
		return json({ ok: false, error: "MISSING_PARAMS" }, 400);
	}

	try {
		const row = await env.DB.prepare(
			"SELECT siap_pada, run_id FROM relay_signals WHERE rantai_id = ? AND nomor = ?"
		).bind(rantai_id, Number(nomor)).first<{ siap_pada: number | null; run_id: string | null }>();

		if (!row) {
			return json({ ok: true, siap: false, alasan: "belum_ada_entri" });
		}
		if (row.siap_pada === null) {
			return json({ ok: true, siap: false, alasan: "menunggu_boot" });
		}
		return json({ ok: true, siap: true, siap_pada: row.siap_pada, run_id: row.run_id });
	} catch (e) {
		console.error("[RELAY] Gagal query relay_signals:", e);
		return json({ ok: false, error: "DB_ERROR" }, 500);
	}
}

export const relayRoutes: Route[] = [
	{ method: "POST", pattern: "/relay/trigger", handler: handleRelayTrigger },
	{ method: "POST", pattern: "/relay/ready",   handler: handleRelayReady   },
	{ method: "GET",  pattern: "/relay/status",  handler: handleRelayStatus  },
];
