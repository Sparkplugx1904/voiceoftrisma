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

import { Env, Route, json, d1GetJson, d1SetJson } from "./shared";

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
	const token: string | undefined = env.GITHUB_TOKEN;
	if (!token) {
		console.error("[WORKFLOW] GITHUB_TOKEN secret missing di Cloudflare Worker");
		return { triggered: false, alasan: "GITHUB_TOKEN_MISSING" };
	}

	const rantaiId = getWitaDateString();

	// 1. Cek Anti-Kembar: Jangan picu jika sudah ada runner v3 yang aktif
	if (!paksa) {
		const adaRunner = await hasActiveRunner(token);
		if (adaRunner) {
			console.log("[ANTI-KEMBAR] Runner v3 sedang aktif berjalan/mengantre. Trigger dibatalkan.");
			return { triggered: false, alasan: "RUNNER_ALREADY_ACTIVE" };
		}
	}

	// 2. Cek Status Radio: Jangan picu jika radio sedang OFF-AIR
	if (!paksa) {
		const onAir = await isStreamOnAir();
		if (!onAir) {
			console.log("[WORKFLOW] Radio sedang OFF-AIR. Trigger dibatalkan untuk menghemat kuota runner.");
			return { triggered: false, alasan: "STREAM_OFF_AIR" };
		}
	}

	// 3. Tentukan nomor sesi hari ini (via D1 kv_store)
	let sesi = 1;
	try {
		const keySesi = `sesi_${rantaiId}`;
		const dataSesi = (await d1GetJson(env.DB, keySesi)) as { sesi?: number } | null;
		sesi = (dataSesi?.sesi ?? 0) + 1;
		await d1SetJson(env.DB, keySesi, { sesi, updated_at: Date.now() });
	} catch (e) {
		console.warn("[WORKFLOW] Gagal baca/tulis sesi di D1, fallback ke sesi 1:", e);
	}

	// 4. Dispatch runner #1 untuk sesi ini
	console.log(`[WORKFLOW] Memulai Runner v3: rantai=${rantaiId} nomor=1 sesi=${sesi}`);
	const hasil = await dispatchV3(token, rantaiId, sesi);

	if (hasil.ok) {
		console.log(`[ OK ] ${WORKFLOW_V3} berhasil dipicu (${hasil.status}) untuk Sesi ${sesi}`);
		return { triggered: true, alasan: "SUCCESS", sesi };
	} else {
		console.error(`[FAIL] Gagal memicu ${WORKFLOW_V3} → HTTP ${hasil.status}`);
		return { triggered: false, alasan: `DISPATCH_FAILED_HTTP_${hasil.status}` };
	}
}

/* GET /workflow — info status orkestrator */
async function handleWorkflowInfo(_request: Request, env: Env): Promise<Response> {
	const rantaiId = getWitaDateString();
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
		note: "Worker ini bertindak sebagai satpam anti-kembar dan pemicu terjadwal.",
	});
}

export const workflowRoutes: Route[] = [
	{ method: "GET", pattern: "/workflow", handler: handleWorkflowInfo },
];
