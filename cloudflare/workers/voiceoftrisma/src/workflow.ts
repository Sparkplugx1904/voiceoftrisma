/* =========================================================
   VOICE OF TRISMA — MODUL WORKFLOW TRIGGER (dari workflow-trigger)
   ---------------------------------------------------------
   Cron 07:00 UTC: trigger GHA record+transcript via GitHub API.
   Worker ini tidak punya endpoint fungsional — GET /workflow
   hanya info (debug).
   ========================================================= */

import { Env, Route, json } from "./shared";

const GITHUB_OWNER = "Sparkplugx1904";
const GITHUB_REPO = "voiceoftrisma";
const WORKFLOWS = ["main+transcript_v1.0.yml", "main+transcript_v2.0.yml"];

async function triggerWorkflow(workflow: string, token: string): Promise<{ workflow: string; status: number; ok: boolean }> {
	const url = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(workflow)}/dispatches`;

	const res = await fetch(url, {
		method: "POST",
		headers: {
			Authorization: `Bearer ${token}`,
			"Content-Type": "application/json",
			"User-Agent": "voiceoftrisma-worker",
		},
		body: JSON.stringify({ ref: "main" }),
		signal: AbortSignal.timeout(15000), // jangan biarkan cron menggantung
	});

	return { workflow, status: res.status, ok: res.ok };
}

/* Cron 07:00 UTC: trigger semua workflow rekaman. */
export async function triggerWorkflows(env: Env): Promise<void> {
	const token: string | undefined = env.GITHUB_TOKEN;
	if (!token) {
		console.error("GITHUB_TOKEN secret missing");
		return;
	}

	const results = await Promise.all(WORKFLOWS.map((wf) => triggerWorkflow(wf, token)));

	for (const r of results) {
		if (r.ok) {
			console.log(`[ OK ] ${r.workflow} triggered (${r.status})`);
		} else {
			console.error(`[FAIL] ${r.workflow} → ${r.status}`);
		}
	}
}

/* GET /workflow — info saja. */
function handleWorkflowInfo(): Response {
	return json({
		ok: true,
		service: "workflow-trigger",
		note: "Worker ini berjalan lewat cron (0 7 * * * UTC) — endpoint ini hanya untuk pengecekan.",
		workflows: WORKFLOWS,
	});
}

export const workflowRoutes: Route[] = [
	{ method: "GET", pattern: "/workflow", handler: handleWorkflowInfo },
];
