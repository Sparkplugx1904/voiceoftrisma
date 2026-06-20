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
	});

	return { workflow, status: res.status, ok: res.ok };
}

export default {
	async scheduled(_event: ScheduledController, env: Env, _ctx: ExecutionContext) {
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
	},

	async fetch() {
		return new Response("This worker only runs on a cron schedule.", { status: 200 });
	},
};
