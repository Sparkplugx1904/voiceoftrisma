/* =========================================================
   VOICE OF TRISMA — TUNNEL HUB (Durable Object WebSocket Relay)
   ---------------------------------------------------------
   Titik kumpul sentral antara semua runner VM GitHub Actions
   dan pengguna / agen AI:
     - Setiap VM memiliki instance_id unik (random hash, misal: vm-a4e92b1c).
     - VM streaming log terminal dan status via WebSocket secara real-time.
     - Pengguna / agen AI bisa:
         1. Melihat daftar VM aktif: GET /tunnel/vms atau GET /tunnel/list
         2. Membaca cuplikan log terakhir via HTTP: GET /tunnel/logs?vm=<id>&tail=50
         3. Menonton live terminal output via WebSocket: GET /tunnel/ws?role=viewer&vm=<id>
         4. Membaca status runner aktif: GET /tunnel/active
         5. Meminta restart runner: POST /tunnel/restart
   ========================================================= */

import { Env, Route, json, verifyControlAuth } from "./shared";

const GITHUB_OWNER = "Sparkplugx1904";
const GITHUB_REPO = "voiceoftrisma";
const WORKFLOW_V3 = "main+transcript_v3.0.yml";

export interface VMInfo {
	instance_id: string;
	rantai_id: string;
	nomor: number;
	status: string; // "connected" | "standby" | "recording" | "stopping" | "disconnected"
	boot_epoch?: number;
	connected_at: number;
	last_seen: number;
	log_count: number;
	latest_log?: string;
}

export class TunnelHubDO implements DurableObject {
	private vms = new Map<string, VMInfo>();
	private vmLogs = new Map<string, string[]>(); // ring buffer (max 500 lines per VM)
	private runnerSockets = new Map<string, WebSocket>();
	private viewers = new Set<{ ws: WebSocket; targetVm?: string }>();

	constructor(private state: DurableObjectState, private env: Env) {}

	private appendLog(instanceId: string, logLine: string) {
		let logs = this.vmLogs.get(instanceId);
		if (!logs) {
			logs = [];
			this.vmLogs.set(instanceId, logs);
		}
		logs.push(logLine);
		if (logs.length > 500) logs.shift();

		const vm = this.vms.get(instanceId);
		if (vm) {
			vm.last_seen = Date.now();
			vm.log_count = (vm.log_count || 0) + 1;
			vm.latest_log = logLine;
		}

		// Broadcast ke semua viewer yang terhubung
		const msg = JSON.stringify({
			type: "log",
			instance_id: instanceId,
			line: logLine,
			time: Date.now(),
		});
		for (const viewer of Array.from(this.viewers)) {
			if (!viewer.targetVm || viewer.targetVm === instanceId) {
				try {
					viewer.ws.send(msg);
				} catch {
					this.viewers.delete(viewer);
				}
			}
		}
	}

	async fetch(request: Request): Promise<Response> {
		const url = new URL(request.url);
		const path = url.pathname.replace(/^\/tunnel/, "");

		// 1. WebSocket upgrade
		if (request.headers.get("Upgrade") === "websocket") {
			const role = url.searchParams.get("role") || "viewer";
			const pair = new WebSocketPair();
			const [client, server] = Object.values(pair);

			server.accept();

			if (role === "runner") {
				const instanceId = url.searchParams.get("instance_id") || `vm-${Math.random().toString(16).slice(2, 10)}`;
				const rantaiId = url.searchParams.get("rantai_id") || "unknown";
				const nomor = Number(url.searchParams.get("nomor")) || 1;
				const bootEpoch = Number(url.searchParams.get("boot_epoch")) || undefined;

				const now = Date.now();
				const vmInfo: VMInfo = {
					instance_id: instanceId,
					rantai_id: rantaiId,
					nomor,
					status: "standby",
					boot_epoch: bootEpoch,
					connected_at: now,
					last_seen: now,
					log_count: 0,
				};
				this.vms.set(instanceId, vmInfo);
				this.runnerSockets.set(instanceId, server);

				this.appendLog(instanceId, `[TUNNEL] Runner #${nomor} terhubung ke WebSocket Hub (Instance: ${instanceId})`);

				server.addEventListener("message", (event) => {
					try {
						const raw = typeof event.data === "string" ? event.data : new TextDecoder().decode(event.data);
						const data = JSON.parse(raw);
						if (data.type === "log" && typeof data.msg === "string") {
							this.appendLog(instanceId, data.msg);
						} else if (data.type === "heartbeat") {
							const vm = this.vms.get(instanceId);
							if (vm) {
								vm.last_seen = Date.now();
								if (data.status) vm.status = data.status;
							}
						} else if (data.type === "status") {
							const vm = this.vms.get(instanceId);
							if (vm) {
								vm.status = data.status;
								vm.last_seen = Date.now();
							}
							this.appendLog(instanceId, `[TUNNEL STATUS] ${data.status}`);
						}
					} catch {
						this.appendLog(instanceId, String(event.data));
					}
				});

				server.addEventListener("close", () => {
					const vm = this.vms.get(instanceId);
					if (vm) vm.status = "disconnected";
					this.runnerSockets.delete(instanceId);
					this.appendLog(instanceId, `[TUNNEL] Runner #${nomor} (${instanceId}) terputus.`);
				});

				return new Response(null, { status: 101, webSocket: client });
			} else {
				// role === "viewer"
				const targetVm = url.searchParams.get("vm") || undefined;
				const viewer = { ws: server, targetVm };
				this.viewers.add(viewer);

				// Kirim status awal VM yang tersedia
				const activeVms = Array.from(this.vms.values());
				server.send(
					JSON.stringify({
						type: "welcome",
						vms: activeVms,
						target_vm: targetVm || "all",
					})
				);

				// Kirim backlog riwayat log untuk VM yang diminta
				if (targetVm && this.vmLogs.has(targetVm)) {
					const backlog = this.vmLogs.get(targetVm) || [];
					for (const line of backlog.slice(-50)) {
						server.send(
							JSON.stringify({
								type: "log",
								instance_id: targetVm,
								line,
								backlog: true,
							})
						);
					}
				}

				server.addEventListener("close", () => {
					this.viewers.delete(viewer);
				});

				return new Response(null, { status: 101, webSocket: client });
			}
		}

		// 2. HTTP: Daftar semua VM yang aktif / pernah terhubung
		if (path === "/vms" || path === "/list" || path === "") {
			const now = Date.now();
			const list = Array.from(this.vms.values()).map((vm) => {
				const isAlive = now - vm.last_seen < 60_000 && vm.status !== "disconnected";
				return {
					...vm,
					active: isAlive,
					uptime_seconds: Math.floor((now - vm.connected_at) / 1000),
					idle_seconds: Math.floor((now - vm.last_seen) / 1000),
				};
			});
			return json({ ok: true, total: list.length, active_count: list.filter((v) => v.active).length, vms: list });
		}

		// 3. HTTP: Cek apakah ada runner aktif yang sedang bertugas
		if (path === "/active") {
			const now = Date.now();
			const rantaiId = url.searchParams.get("rantai_id");
			const nomor = Number(url.searchParams.get("nomor")) || undefined;
			const myInstance = url.searchParams.get("exclude_instance") || "";

			let active = false;
			let activeVm: VMInfo | null = null;
			for (const vm of this.vms.values()) {
				if (myInstance && vm.instance_id === myInstance) continue;
				const isAlive = now - vm.last_seen < 60_000 && vm.status !== "disconnected";
				if (isAlive) {
					if (rantaiId && vm.rantai_id !== rantaiId) continue;
					if (nomor && vm.nomor !== nomor) continue;
					active = true;
					activeVm = vm;
					break;
				}
			}
			return json({ ok: true, active, active_vm: activeVm });
		}

		// 4. HTTP: Ambil riwayat log terminal
		if (path === "/logs") {
			const targetVm = url.searchParams.get("vm");
			const tail = Math.min(Number(url.searchParams.get("tail")) || 50, 500);
			const format = url.searchParams.get("format") || "text";

			let logs: string[] = [];
			if (targetVm && this.vmLogs.has(targetVm)) {
				logs = this.vmLogs.get(targetVm) || [];
			} else {
				// Gabungkan logs dari semua VM
				for (const [id, lines] of this.vmLogs) {
					logs.push(...lines.map((l) => `[${id}] ${l}`));
				}
			}

			const slice = logs.slice(-tail);
			if (format === "json") {
				return json({ ok: true, vm: targetVm || "all", count: slice.length, logs: slice });
			}
			return new Response(slice.join("\n") + "\n", {
				headers: { "Content-Type": "text/plain; charset=utf-8" },
			});
		}

		// 5. HTTP POST: Fallback push log dari VM
		if (path === "/push" && request.method === "POST") {
			let body: {
				instance_id?: string;
				rantai_id?: string;
				nomor?: number;
				status?: string;
				logs?: string[];
				log?: string;
			};
			try {
				body = await request.json();
			} catch {
				return json({ ok: false, error: "INVALID_JSON" }, 400);
			}

			const instanceId = body.instance_id || "unknown";
			if (!this.vms.has(instanceId)) {
				this.vms.set(instanceId, {
					instance_id: instanceId,
					rantai_id: body.rantai_id || "unknown",
					nomor: body.nomor || 1,
					status: body.status || "connected",
					connected_at: Date.now(),
					last_seen: Date.now(),
					log_count: 0,
				});
			}
			if (body.status) {
				const vm = this.vms.get(instanceId);
				if (vm) vm.status = body.status;
			}
			if (body.log) {
				this.appendLog(instanceId, body.log);
			}
			if (Array.isArray(body.logs)) {
				for (const l of body.logs) this.appendLog(instanceId, l);
			}
			return json({ ok: true });
		}

		// 6. HTTP POST: Reset semua VM menjadi disconnected (saat restart)
		if (path === "/reset" && request.method === "POST") {
			for (const vm of this.vms.values()) {
				vm.status = "disconnected";
			}
			return json({ ok: true });
		}

		return json({ error: "Not found" }, 404);
	}
}

/* ---------------- Helper Route Handler ---------------- */

async function forwardToTunnelHub(request: Request, env: Env): Promise<Response> {
	if (!env.TUNNEL_HUB) {
		return json({ error: "TUNNEL_HUB binding tidak tersedia." }, 503);
	}

	const url = new URL(request.url);

	// 1. Verifikasi koneksi WebSocket Runner (/tunnel/ws?role=runner)
	if (url.pathname === "/tunnel/ws" && url.searchParams.get("role") === "runner") {
		const instanceId = url.searchParams.get("instance_id") || "";
		const auth = await verifyControlAuth(request, env, instanceId);
		if (!auth.authorized) {
			console.warn(`[SECURITY] Koneksi runner WS palsu ditolak (${auth.error}) untuk instance: ${instanceId}`);
			return json({ ok: false, error: "UNAUTHORIZED_RUNNER", detail: auth.error }, 401);
		}
	}

	// 2. Verifikasi HTTP Push Logs (/tunnel/push)
	if (url.pathname === "/tunnel/push" && request.method === "POST") {
		const cloned = request.clone();
		const bodyText = await cloned.text().catch(() => "");
		const auth = await verifyControlAuth(request, env, bodyText);
		if (!auth.authorized) {
			console.warn(`[SECURITY] HTTP push log ditolak: ${auth.error}`);
			return json({ ok: false, error: "UNAUTHORIZED_PUSH", detail: auth.error }, 401);
		}
	}

	const id = env.TUNNEL_HUB.idFromName("GlobalTunnel");
	const doStub = env.TUNNEL_HUB.get(id);
	return doStub.fetch(request);
}

/* Endpoint POST /tunnel/restart — membatalkan runner lama dan dispatch runner baru */
async function handleTunnelRestart(request: Request, env: Env): Promise<Response> {
	// Wajib terotentikasi: Hanya Admin atau pihak berwenang dengan RELAY_SECRET / SESSION_SECRET
	const auth = await verifyControlAuth(request, env);
	if (!auth.authorized) {
		console.warn(`[SECURITY] Percobaan restart runner ditolak: ${auth.error}`);
		return json({ ok: false, error: "UNAUTHORIZED_RESTART", detail: auth.error }, 401);
	}

	const token: string | undefined = env.GITHUB_TOKEN;
	if (!token) {
		return json({ ok: false, error: "GITHUB_TOKEN_MISSING" }, 500);
	}

	// 1. Cari workflow run yang sedang in_progress / queued
	const urlRuns = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(WORKFLOW_V3)}/runs?per_page=5`;
	let activeRunId: number | null = null;
	try {
		const res = await fetch(urlRuns, {
			headers: {
				Authorization: `Bearer ${token}`,
				Accept: "application/vnd.github+json",
				"User-Agent": "voiceoftrisma-tunnel/3.0",
			},
			signal: AbortSignal.timeout(10000),
		});
		if (res.ok) {
			const data = (await res.json()) as { workflow_runs?: Array<{ id: number; status: string }> };
			const activeRun = (data.workflow_runs || []).find((r) =>
				["in_progress", "queued", "waiting", "requested"].includes(r.status)
			);
			if (activeRun) activeRunId = activeRun.id;
		}
	} catch (e) {
		console.warn("[TUNNEL RESTART] Gagal cek active run:", e);
	}

	// 2. Batalkan run aktif jika ada
	if (activeRunId) {
		console.log(`[TUNNEL RESTART] Membatalkan run aktif ${activeRunId}...`);
		try {
			await fetch(
				`https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/runs/${activeRunId}/cancel`,
				{
					method: "POST",
					headers: {
						Authorization: `Bearer ${token}`,
						Accept: "application/vnd.github+json",
						"User-Agent": "voiceoftrisma-tunnel/3.0",
					},
					signal: AbortSignal.timeout(10000),
				}
			);
		} catch (e) {
			console.warn("[TUNNEL RESTART] Gagal cancel run:", e);
		}
	}

	// 3. Reset status VM di Durable Object agar runner baru tidak terblokir anti-kembar
	if (env.TUNNEL_HUB) {
		try {
			const id = env.TUNNEL_HUB.idFromName("GlobalTunnel");
			await env.TUNNEL_HUB.get(id).fetch("https://internal/tunnel/reset", { method: "POST" });
		} catch {}
	}

	// Jeda 3 detik agar sinyal pembatalan efektif
	await new Promise((r) => setTimeout(r, 3000));

	// 4. Dispatch run baru dengan nomor 1 atau sesi berikutnya
	const nowWita = new Date(Date.now() + 8 * 3600 * 1000);
	const rantaiId = nowWita.toISOString().slice(0, 10);
	const urlDispatch = `https://api.github.com/repos/${GITHUB_OWNER}/${GITHUB_REPO}/actions/workflows/${encodeURIComponent(WORKFLOW_V3)}/dispatches`;

	try {
		const resDispatch = await fetch(urlDispatch, {
			method: "POST",
			headers: {
				Authorization: `Bearer ${token}`,
				"Content-Type": "application/json",
				Accept: "application/vnd.github+json",
				"User-Agent": "voiceoftrisma-tunnel/3.0",
			},
			body: JSON.stringify({
				ref: "main",
				inputs: {
					rantai_id: rantaiId,
					nomor: "1",
					sesi: "1",
					tumpang: "900",
					margin: "300",
					perintah: "jalan",
					mau_transkrip: "false",
				},
			}),
			signal: AbortSignal.timeout(15000),
		});

		if (resDispatch.ok) {
			return json({
				ok: true,
				message: "Runner lama dibatalkan dan Runner baru berhasil di-dispatch.",
				cancelled_run_id: activeRunId,
				rantai_id: rantaiId,
			});
		} else {
			const err = await resDispatch.text().catch(() => "");
			return json({ ok: false, error: `DISPATCH_HTTP_${resDispatch.status}`, detail: err }, 502);
		}
	} catch (e) {
		return json({ ok: false, error: "DISPATCH_FAILED", detail: String(e) }, 502);
	}
}

export const tunnelRoutes: Route[] = [
	{ method: "GET", pattern: "/tunnel/ws", handler: forwardToTunnelHub },
	{ method: "GET", pattern: "/tunnel/vms", handler: forwardToTunnelHub },
	{ method: "GET", pattern: "/tunnel/list", handler: forwardToTunnelHub },
	{ method: "GET", pattern: "/tunnel/active", handler: forwardToTunnelHub },
	{ method: "GET", pattern: "/tunnel/logs", handler: forwardToTunnelHub },
	{ method: "POST", pattern: "/tunnel/push", handler: forwardToTunnelHub },
	{ method: "POST", pattern: "/tunnel/restart", handler: handleTunnelRestart },
];
