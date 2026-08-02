# voiceoftrisma-admin-worker

Worker Cloudflare untuk **Voice of Trisma** — login admin + pengelolaan jadwal siaran.

Situs utama (GitHub Pages) kini mengambil jadwal dari worker ini
(`GET /api/jadwal`) — **bukan** dari `jadwal.json` lagi.
Jadwal diedit lewat **Dashboard Admin** di `template/main/dashboard/`.

## Endpoint

| Method | Path            | Akses   | Fungsi                                                        |
|--------|-----------------|---------|---------------------------------------------------------------|
| GET    | `/api/health`   | Publik  | Cek status worker                                              |
| GET    | `/api/jadwal`   | Publik  | Ambil jadwal siaran (format sama dengan jadwal.json lama)      |
| POST   | `/api/login`    | Publik  | Login admin → `{ token }` (valid 7 hari)                       |
| PUT    | `/api/jadwal`   | Admin*  | Simpan seluruh dokumen jadwal (`{ jadwal: { "1": [...], ... } }`) |

\* Admin = header `Authorization: Bearer <token>` dari hasil login.

Contoh login:

```bash
curl -X POST https://voiceoftrisma-admin-worker.anandapradnyana68.workers.dev/api/login \
  -H "Content-Type: application/json" \
  -d '{"username":"mpvot","password":"<password-admin>"}'
```

## Setup & Deploy (sekali saja)

```bash
cd cloudflare/workers/voiceoftrisma-admin-worker

# 1. Install dependencies
npm install

# 2. Buat KV namespace untuk menyimpan jadwal
npx wrangler kv namespace create VOT_ADMIN_STORE
#    -> salin "id" hasilnya ke wrangler.jsonc → kv_namespaces[0].id

# 3. Set secrets (bisa lewat CLI di bawah, atau Dashboard Cloudflare
#    → Workers → voiceoftrisma-admin-worker → Settings → Variables and Secrets)
npx wrangler secret put ADMIN_USERNAME    # isi: mpvot
npx wrangler secret put ADMIN_PASSWORD    # isi: password admin
npx wrangler secret put SESSION_SECRET    # isi: openssl rand -base64 32

# 4. Deploy
npx wrangler deploy

# 5. (Opsional) Generate ulang tipe TypeScript setelah ubah binding
npx wrangler types
```

Secrets **tidak boleh** di-commit. Untuk development lokal, isi file `.dev.vars`
(ter-ignore git) dengan key yang sama, lalu `npx wrangler dev --local`.

## Struktur kode

| File                 | Isi                                                        |
|----------------------|------------------------------------------------------------|
| `src/index.ts`       | Seluruh logika worker (router, auth, jadwal, validasi)      |
| `wrangler.jsonc`     | Konfigurasi Wrangler (nama, binding KV, secrets)            |
| `worker-configuration.d.ts` | Tipe yang di-generate `wrangler types` (jangan edit manual) |

## Menambah fitur baru (biar bisa develop massive)

1. **Worker**: daftarkan route di array `ROUTES` di `src/index.ts`,
   tulis handler-nya (panggil `requireAuth()` jika endpoint admin),
   simpan data baru di KV dengan key sendiri lewat `kvGetJson` / `kvSetJson`.
2. **Dashboard**: tambah tab di `template/main/dashboard/index.html`
   (`data-section="nama"` + `<section id="section-nama">`), lalu daftarkan
   `nama: { init: ... }` di objek `SECTIONS` pada `dashboard.js`.
3. **Situs**: ubah pemanggilan API di `template/main/script.js` bila perlu.

## Keamanan

- Kredensial admin hanya di **secrets** Cloudflare (`ADMIN_USERNAME`,
  `ADMIN_PASSWORD`) — tidak ada di source code.
- Token sesi = payload HMAC-SHA256 yang ditandatangani `SESSION_SECRET`
  (stateless, kedaluwarsa 7 hari).
- Rate limit login: maksimal 5 percobaan gagal / 10 menit per IP (best-effort
  per isolate — cukup untuk kasus ini).
- Perbandingan kredensial memakai hash + timing-safe compare.
