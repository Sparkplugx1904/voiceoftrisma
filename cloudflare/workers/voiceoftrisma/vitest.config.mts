import { defineWorkersConfig } from "@cloudflare/vitest-pool-workers/config";

export default defineWorkersConfig({
	test: {
		poolOptions: {
			workers: {
				wrangler: { configPath: "./wrangler.jsonc" },
				defineEnv: {
					vars: {
						ADMIN_USERNAME: "test-admin",
						ADMIN_PASSWORD: "test-pass",
						SESSION_SECRET: "test-secret-for-vitest-only",
						GITHUB_TOKEN: "test-token",
						// Anti-DDoS limiter: nonaktifkan praktis di test (unit test
						// `rateLimited` menguji logika limiter secara terpisah).
						MAX_REQ_IP_10S: "1000000",
						MAX_REQ_GLOBAL_10S: "1000000",
					},
				},
			},
		},
	},
});
