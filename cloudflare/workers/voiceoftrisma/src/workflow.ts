/* =========================================================
   VOICE OF TRISMA — MODUL WORKFLOW TRIGGER (cron only)
   ---------------------------------------------------------
   Cron 07:00 UTC: trigger GHA record+transcript via GitHub API.
   MODUL INI TIDAK MEMILIKI ENDPOINT HTTP APAPUN.
   Semua eksekusi hanya dapat dipicu oleh internal scheduled cron.
   ========================================================= */

import { Env } from "./shared";

const GITHUB_OWNER = "sparkplugx1904";
const GITHUB_REPO = "voiceoftrisma";
const WORKFLOWS = ["main+transcript_v1.0.yml", "main+transcript_v2.0.yml"];
async function triggerWorkflow(
	workflow: string,
	token: string
): Promise<{ workflow: string; status: number; ok: boolean; error?: string }> {
	const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;

	const res = await fetch(url, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${token}`,
			Accept: "application/vnd.github+json",
			"X-GitHub-Api-Version": "2022-11-28",
			"Content-Type": "application/json",
			"User-Agent": "voiceoftrisma-worker",
		},
		body: JSON.stringify({ ref: "main" }),
		signal: AbortSignal.timeout(15000), // jangan biarkan cron menggantung
	});

	let error: string | undefined;
	if (!res.ok) {
		try {
			error = await res.text();
		} catch {
			error = res.statusText;
		}
	}

	return { workflow, status: res.status, ok: res.ok, error };
}

/* Cron 07:00 UTC: trigger semua workflow rekaman. */
export async function triggerWorkflows(
	env: Env
): Promise<{ workflow: string; status: number; ok: boolean; error?: string }[]> {
	const token: string | undefined = env.GITHUB_TOKEN;
	if (!token) {
		console.error("GITHUB_TOKEN secret missing");
		return [];
	}

	const results = await Promise.all(WORKFLOWS.map((wf) => triggerWorkflow(wf, token)));

	for (const r of results) {
		if (r.ok) {
			console.log(`[ OK ] ${r.workflow} triggered (${r.status})`);
		} else {
			console.error(`[FAIL] ${r.workflow} → ${r.status}: ${r.error || "Unknown error"}`);
		}
	}
	return results;
}
