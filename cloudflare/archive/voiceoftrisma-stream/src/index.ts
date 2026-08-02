export default {
	async fetch(request) {
		const STREAM_URL = "http://i.klikhost.com:8502/stream";

		try {
			const response = await fetch(STREAM_URL, {
				method: "GET",
				headers: {
					"User-Agent": "Mozilla/5.0",
				},
			});

			// LANGSUNG kembalikan response.body (Bypass CPU processing)
			return new Response(response.body, {
				status: response.status,
				headers: {
					"Content-Type": "audio/mpeg",
					"Access-Control-Allow-Origin": "*",
					"Connection": "keep-alive",
					"Transfer-Encoding": "chunked",
					// Bawa header dari server asli jika memungkinkan
					"Cache-Control": "no-store",
				},
			});
		} catch (err) {
			return new Response("Gagal menyambung ke server radio", { status: 500 });
		}
	},
};