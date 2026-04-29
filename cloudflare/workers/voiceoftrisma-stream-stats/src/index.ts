export default {
	async fetch(request, env, ctx) {
		const targetUrl = 'http://i.klikhost.com:8502/stats?json=1';

		// Ganti tanda bintang (*) dengan domain web Anda jika sudah masuk production
		// Contoh: 'https://website-radio-anda.com'
		const corsHeaders = {
			'Access-Control-Allow-Origin': 'https://voiceoftrisma.github.io',
			'Access-Control-Allow-Methods': 'GET, OPTIONS',
			'Access-Control-Allow-Headers': 'Content-Type',
		};

		// 1. Tangani request OPTIONS (Preflight) dari browser secara otomatis
		if (request.method === 'OPTIONS') {
			return new Response(null, { headers: corsHeaders });
		}

		try {
			// 2. Ambil data asli dari server Klikhost (jembatan bekerja)
			const response = await fetch(targetUrl);
			const data = await response.text(); // Mengambil sebagai teks murni dulu

			// 3. Kirim kembali ke browser dengan stempel izin CORS
			return new Response(data, {
				headers: {
					'Content-Type': 'application/json',
					...corsHeaders,
				},
			});

		} catch (error) {
			// Jika server Klikhost sedang down, beri tahu frontend dengan elegan
			return new Response(JSON.stringify({ error: 'Gagal terhubung ke server radio' }), {
				status: 500,
				headers: {
					'Content-Type': 'application/json',
					...corsHeaders,
				}
			});
		}
	},
};