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
					},
				},
			},
		},
	},
});
