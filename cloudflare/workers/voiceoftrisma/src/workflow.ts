/* =========================================================
   VOICE OF TRISMA — MODUL WORKFLOW TRIGGER (dari workflow-trigger)
   ---------------------------------------------------------
   Cron 07:00 UTC: trigger GHA record+transcript via GitHub API.
   Worker ini tidak punya endpoint fungsional — GET /workflow
   hanya info (debug).
   ========================================================= */

import { Env, Route, json, requireAuth } from "./shared";
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

/* GET /workflow — info & status token GitHub */
async function handleWorkflowInfo(_request: Request, env: Env): Promise<Response> {
	const token = env.GITHUB_TOKEN;
	let githubAuth = "missing";
	if (token && token !== "test-token") {
		try {
			const res = await fetch("https://api.github.com/user", {
				headers: {
					Authorization: `Bearer ${token}`,
					Accept: "application/vnd.github+json",
					"X-GitHub-Api-Version": "2022-11-28",
					"User-Agent": "voiceoftrisma-worker",
				},
				signal: AbortSignal.timeout(5000),
			});
			if (res.ok) {
				const u = (await res.json()) as { login: string };
				githubAuth = `authenticated as ${u.login}`;
			} else {
				githubAuth = `HTTP ${res.status}`;
			}
		} catch (e) {
			githubAuth = `network error: ${e}`;
		}
	} else if (token === "test-token") {
		githubAuth = "test-token";
	}

	return json({
		ok: true,
		service: "workflow-trigger",
		note: "Worker ini berjalan lewat cron (0 7 * * * UTC) — endpoint ini untuk pengecekan status.",
		repo: `${GITHUB_OWNER}/${GITHUB_REPO}`,
		github_auth: githubAuth,
		workflows: WORKFLOWS,
	});
}

/* POST /api/workflow/trigger — trigger manual rekaman (admin only) */
async function handleTriggerManual(request: Request, env: Env): Promise<Response> {
	const user = await requireAuth(request, env);
	if (!user) {
		return json({ error: "Unauthorized" }, 401);
	}
	const results = await triggerWorkflows(env);
	return json({ ok: true, results });
}

export const workflowRoutes: Route[] = [
	{ method: "GET", pattern: "/workflow", handler: handleWorkflowInfo },
	{ method: "POST", pattern: "/api/workflow/trigger", handler: handleTriggerManual },
];
