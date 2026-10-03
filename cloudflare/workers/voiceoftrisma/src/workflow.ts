/* =========================================================
   VOICE OF TRISMA — MODUL WORKFLOW TRIGGER (v3.0 Orchestrator)
   ---------------------------------------------------------
   Orkestrator pintar & satpam anti-kembar untuk rekaman v3:
   1. Cek status live Icecast: hanya trigger jika ON-AIR (streamstatus: 1).
   2. Cek GitHub Actions API: batalkan jika sudah ada runner v3 yang
      sedang 'in_progress' atau 'queued' (100% anti-banjir runner).
   3. Hitung nomor sesi hari ini (Sesi 1, Sesi 2, dst) via D1 kv_store.
   4. Trigger workflow main+transcript_v3.0.yml dengan input lengkap.
   ========================================================= */

import { Env, Route, json, d1GetJson, d1SetJson, verifyControlAuth } from "./shared";

const GITHUB_OWNER = "Sparkplugx1904";
const GITHUB_REPO = "voiceoftrisma";
const WORKFLOW_V3 = "main+transcript_v3.0.yml";
const STATS_URL = "http://i.klikhost.com:8502/stats?json=1";

/* Ambil tanggal hari ini dalam format WITA (UTC+8): YYYY-MM-DD */
export function getWitaDateString(): string {
	const nowWita = new Date(Date.now() + 8 * 3600 * 1000);
	return nowWita.toISOString().slice(0, 10);
}

/* Cek status radio di Klikhost: true bila ON-AIR */
export async function isStreamOnAir(): Promise<boolean> {
	try {
		const res = await fetch(`${STATS_URL}&t=${Date.now()}`, { signal: AbortSignal.timeout(8000) });
		if (!res.ok) return false;
		const data = (await res.json()) as Record<string, unknown>;
		return data.streamstatus === 1 || String(data.streamstatus) === "1";
	} catch {
		return false;
	}
}

/* Cek apakah ada runner v3 yang sedang berjalan atau mengantre di GitHub */
export async function hasActiveRunner(token: string): Promise<boolean> {
	const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(WORKFLOW_V3)}/runs?per_page=10`;
	try {
		const res = await fetch(url, {
			headers: {
				Authorization: `Bearer ${token}`,
				Accept: "application/vnd.github+json",
				"User-Agent": "voiceoftrisma-worker",
			},
			signal: AbortSignal.timeout(10000),
		});
		if (!res.ok) {
			console.warn(`[ANTI-KEMBAR] Gagal cek status runner GitHub: HTTP ${res.status}`);
			return false;
		}
		const data = (await res.json()) as { workflow_runs?: Array<{ status: string }> };
		const active = (data.workflow_runs || []).some((r) =>
			["in_progress", "queued", "waiting", "requested"].includes(r.status)
		);
		return active;
	} catch (e) {
		console.warn("[ANTI-KEMBAR] Galat saat menghubungi GitHub API:", e);
		return false;
	}
}

/* Kirim dispatch workflow ke GitHub Actions */
async function dispatchV3(
	token: string,
	rantaiId: string,
	sesi: number
): Promise<{ status: number; ok: boolean; error?: string }> {
	const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(WORKFLOW_V3)}/dispatches`;
	const body = {
		ref: "main",
		inputs: {
			rantai_id: rantaiId,
			nomor: "1",
			sesi: String(sesi),
		},
	};

	try {
		const res = await fetch(url, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
				Accept: "application/vnd.github+json",
				"User-Agent": "voiceoftrisma-worker",
			},
			body: JSON.stringify(body),
			signal: AbortSignal.timeout(15000),
		});
		return { status: res.status, ok: res.ok };
	} catch (e) {
		return { status: 500, ok: false, error: String(e) };
	}
}

/* Eksekusi pemicu orkestrator (dipanggil oleh cron atau endpoint manual) */
export async function triggerWorkflows(env: Env, paksa = false): Promise<{ triggered: boolean; alasan: string; sesi?: number }> {
	// Workflow V3 dinonaktifkan sementara atas permintaan pengguna
	console.log("[WORKFLOW] Workflow V3 sedang dinonaktifkan (DISABLED).");
	return { triggered: false, alasan: "WORKFLOW_V3_DISABLED" };
}

/* GET /workflow — info status orkestrator atau trigger manual (?trigger=1) */
async function handleWorkflowInfo(request: Request, env: Env): Promise<Response> {
	const url = new URL(request.url);
	const rantaiId = getWitaDateString();

	if (url.searchParams.get("trigger") === "1") {
		const auth = await verifyControlAuth(request, env);
		if (!auth.authorized) {
			return json({ ok: false, error: "UNAUTHORIZED_TRIGGER", detail: auth.error }, 401);
		}
		const paksa = url.searchParams.get("paksa") === "1";
		const hasil = await triggerWorkflows(env, paksa);
		return json({
			ok: hasil.triggered,
			alasan: hasil.alasan,
			sesi: hasil.sesi,
			wita_date: rantaiId,
			target_workflow: WORKFLOW_V3,
		});
	}

	let sesiAktif = 1;
	try {
		const dataSesi = (await d1GetJson(env.DB, `sesi_${rantaiId}`)) as { sesi?: number } | null;
		sesiAktif = dataSesi?.sesi ?? 0;
	} catch {}

	return json({
		ok: true,
		service: "workflow-orchestrator-v3",
		wita_date: rantaiId,
		sesi_tercatat_hari_ini: sesiAktif,
		target_workflow: WORKFLOW_V3,
		note: "Worker ini bertindak sebagai watchdog 24 jam penjaga gawang runner estafet v3.",
	});
}

export const workflowRoutes: Route[] = [
	{ method: "GET", pattern: "/workflow", handler: handleWorkflowInfo },
];
