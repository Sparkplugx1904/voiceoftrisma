/* =========================================
   VOICE OF TRISMA — DASHBOARD ADMIN
   -------------------------------------------------
   - Auth guard: tanpa token sesi -> redirect ke login
   - Kelola jadwal siaran (get/put ke Cloudflare Worker)
   - Tema sinkron dengan halaman utama

   CARA MENAMBAH FITUR BARU (biar bisa develop massive):
     1. Tambah tab di index.html:
          <button class="dash-nav-item" data-section="nama">...
        dan section-nya:
          <section id="section-nama" class="dash-section" style="display:none;">...
     2. Daftarkan di objek SECTIONS di bawah:
          nama: { title: '...', init: fungsiInisialisasi }
        (init dipanggil sekali saat tab pertama kali dibuka)
     3. Tambah route API di worker:
        cloudflare/workers/voiceoftrisma-admin-worker/src/index.ts
        (daftarkan di array ROUTES, simpan data di KV dengan
        key sendiri lewat helper kvGetJson / kvSetJson)
   ========================================= */

(function () {
    'use strict';

    // Alamat Worker Cloudflare (sesuaikan dengan wrangler.jsonc worker admin)
    var API_BASE = 'https://voiceoftrisma-admin-worker.anandapradnyana68.workers.dev';
    var TOKEN_KEY = 'vot_admin_token';
    var USER_KEY = 'vot_admin_user';

    // Hari siaran (key angka hari sesuai getDay(): 1=Senin ... 6=Sabtu)
    var DAYS = { 1: 'Senin', 2: 'Selasa', 3: 'Rabu', 4: 'Kamis', 5: 'Jumat', 6: 'Sabtu' };

    var state = {
        jadwal: {},      // { "1": [{waktu_mulai, waktu_selesai, acara, penyiar}, ...], ... }
        dirty: false,    // ada perubahan belum disimpan
        saving: false
    };

    var sectionsInitialized = {};

    /* ---------------- Token sesi ---------------- */

    function getToken() {
        return localStorage.getItem(TOKEN_KEY) || sessionStorage.getItem(TOKEN_KEY);
    }

    function clearToken() {
        localStorage.removeItem(TOKEN_KEY);
        sessionStorage.removeItem(TOKEN_KEY);
        localStorage.removeItem(USER_KEY);
        sessionStorage.removeItem(USER_KEY);
    }

    function redirectToLogin() {
        window.location.href = '../login/?next=../dashboard/';
    }

    /* ---------------- Tema (sinkron dengan halaman utama) ---------------- */

    var themeToggleBtn = document.getElementById('themeToggleBtn');
    var themeIcon = themeToggleBtn ? themeToggleBtn.querySelector('i') : null;

    function applyTheme(theme) {
        if (theme === 'light') {
            document.body.classList.add('light-theme');
            if (themeIcon) themeIcon.classList.replace('fa-moon', 'fa-sun');
        } else {
            document.body.classList.remove('light-theme');
            if (themeIcon) themeIcon.classList.replace('fa-sun', 'fa-moon');
        }
    }

    applyTheme(localStorage.getItem('theme') || 'dark');

    if (themeToggleBtn) {
        themeToggleBtn.addEventListener('click', function () {
            var next = document.body.classList.contains('light-theme') ? 'dark' : 'light';
            localStorage.setItem('theme', next);
            applyTheme(next);
        });
    }

    /* ---------------- Auth guard ---------------- */

    if (!getToken()) {
        redirectToLogin();
        return;
    }

    var dashUserEl = document.getElementById('dashUser');
    if (dashUserEl) {
        var savedUser = localStorage.getItem(USER_KEY) || sessionStorage.getItem(USER_KEY);
        dashUserEl.textContent = savedUser ? 'Masuk sebagai: ' + savedUser : 'Sesi aktif';
    }

    document.getElementById('logoutBtn').addEventListener('click', function () {
        clearToken();
        redirectToLogin();
    });

    /* ---------------- Helper API ---------------- */

    function api(path, options) {
        options = options || {};
        options.headers = Object.assign({ 'Content-Type': 'application/json' }, options.headers || {});

        var token = getToken();
        if (token) options.headers['Authorization'] = 'Bearer ' + token;

        return fetch(API_BASE + path, options).then(function (res) {
            if (res.status === 401) {
                clearToken();
                redirectToLogin();
                throw new Error('Sesi berakhir. Silakan masuk kembali.');
            }
            return res.json().catch(function () { return {}; }).then(function (data) {
                return { ok: res.ok, status: res.status, data: data };
            });
        });
    }

    /* ---------------- Jadwal: load & normalize ---------------- */

    function cloneItem(it) {
        return {
            waktu_mulai: it.waktu_mulai || '',
            waktu_selesai: it.waktu_selesai || '',
            acara: it.acara || '',
            penyiar: it.penyiar || ''
        };
    }

    function normalizeJadwal(doc) {
        var out = {};
        for (var d in DAYS) out[d] = [];
        if (doc && typeof doc === 'object') {
            for (var key in doc) {
                if (DAYS[key] && Array.isArray(doc[key])) {
                    out[key] = doc[key].map(cloneItem);
                }
            }
        }
        return out;
    }

    function loadJadwal() {
        setStatus('Memuat jadwal...');
        return api('/api/jadwal').then(function (r) {
            if (!r.ok) throw new Error(r.data.error || 'Gagal memuat jadwal.');
            state.jadwal = normalizeJadwal(r.data.jadwal);
            renderJadwal();
            setStatus('Siap. Belum ada perubahan.');
        }).catch(function (e) {
            setStatus('Gagal memuat jadwal: ' + e.message, true);
        });
    }

    /* ---------------- Jadwal: render ---------------- */

    function renderJadwal() {
        var container = document.getElementById('jadwalContainer');
        container.innerHTML = '';
        for (var d in DAYS) {
            container.appendChild(renderDayCard(d));
        }
    }

    function renderDayCard(day) {
        var card = document.createElement('div');
        card.className = 'day-card glass-panel';
        card.dataset.day = day;

        var head = document.createElement('div');
        head.className = 'day-head';

        var title = document.createElement('h3');
        title.textContent = DAYS[day];

        var btnAdd = document.createElement('button');
        btnAdd.type = 'button';
        btnAdd.className = 'btn-add';
        btnAdd.innerHTML = '<i class="fa-solid fa-plus"></i> Tambah';
        btnAdd.addEventListener('click', function () { addRow(day); });

        head.appendChild(title);
        head.appendChild(btnAdd);
        card.appendChild(head);

        var tableWrap = document.createElement('div');
        tableWrap.className = 'table-wrap';

        var table = document.createElement('table');
        table.className = 'jadwal-table';
        table.innerHTML =
            '<thead><tr>' +
            '<th>Mulai</th><th>Selesai</th><th>Acara</th><th>Penyiar</th><th></th>' +
            '</tr></thead>';

        var tbody = document.createElement('tbody');
        table.appendChild(tbody);
        tableWrap.appendChild(table);
        card.appendChild(tableWrap);

        state.jadwal[day].forEach(function (item) {
            tbody.appendChild(buildRow(item));
        });

        return card;
    }

    function buildRow(item) {
        var tpl = document.getElementById('rowTemplate');
        var tr = tpl.content.firstElementChild.cloneNode(true);

        tr.querySelector('.mulai').value = item.waktu_mulai || '';
        tr.querySelector('.selesai').value = item.waktu_selesai || '';
        tr.querySelector('.acara').value = item.acara || '';
        tr.querySelector('.penyiar').value = item.penyiar || '';

        tr.querySelectorAll('input').forEach(function (inp) {
            inp.addEventListener('input', markDirty);
        });

        tr.querySelector('.btn-del').addEventListener('click', function () {
            tr.remove();
            markDirty();
        });

        return tr;
    }

    function addRow(day) {
        var card = document.querySelector('.day-card[data-day="' + day + '"]');
        if (!card) return;
        var tbody = card.querySelector('tbody');
        tbody.appendChild(buildRow({ waktu_mulai: '', waktu_selesai: '', acara: '', penyiar: '' }));
        markDirty();
    }

    /* Kumpulkan semua baris dari DOM menjadi dokumen jadwal. */
    function collectJadwal() {
        var doc = {};
        document.querySelectorAll('.day-card').forEach(function (card) {
            var day = card.dataset.day;
            doc[day] = [];
            card.querySelectorAll('tbody tr').forEach(function (tr) {
                var mulai = tr.querySelector('.mulai').value.trim();
                var selesai = tr.querySelector('.selesai').value.trim();
                var acara = tr.querySelector('.acara').value.trim();
                var penyiar = tr.querySelector('.penyiar').value.trim();
                doc[day].push({
                    waktu_mulai: mulai,
                    waktu_selesai: selesai || null,
                    acara: acara,
                    penyiar: penyiar
                });
            });
        });
        return { jadwal: doc };
    }

    /* ---------------- Status & Save ---------------- */

    function setStatus(text, isError) {
        var el = document.getElementById('saveStatus');
        if (!el) return;
        el.textContent = text;
        el.classList.toggle('error', !!isError);
    }

    function updateSaveBar() {
        var btn = document.getElementById('saveBtn');
        if (!btn) return;
        btn.disabled = state.saving || !state.dirty;
        if (!state.saving) {
            setStatus(state.dirty ? 'Ada perubahan yang belum disimpan.' : 'Siap. Belum ada perubahan.');
        }
    }

    function markDirty() {
        state.dirty = true;
        updateSaveBar();
    }

    function saveJadwal() {
        if (state.saving || !state.dirty) return;

        state.saving = true;
        var btn = document.getElementById('saveBtn');
        btn.disabled = true;
        setStatus('Menyimpan...');

        api('/api/jadwal', {
            method: 'PUT',
            body: JSON.stringify(collectJadwal())
        }).then(function (r) {
            state.saving = false;
            if (!r.ok) {
                setStatus('Gagal menyimpan: ' + (r.data.error || 'Kesalahan server.'), true);
                updateSaveBar();
                return;
            }
            state.dirty = false;
            state.jadwal = normalizeJadwal(r.data.jadwal);
            renderJadwal(); // render ulang sesuai data tersimpan
            setStatus('Tersimpan ' + new Date().toLocaleTimeString('id-ID') + ' ✓');
            updateSaveBar();
        }).catch(function (e) {
            state.saving = false;
            setStatus('Gagal menyimpan: ' + e.message, true);
            updateSaveBar();
        });
    }

    document.getElementById('saveBtn').addEventListener('click', saveJadwal);

    // Peringatan jika menutup halaman saat masih ada perubahan.
    window.addEventListener('beforeunload', function (e) {
        if (state.dirty) {
            e.preventDefault();
            e.returnValue = '';
        }
    });

    /* ---------------- Navigasi section ---------------- */

    // Registrasi section. TAMBAH FITUR BARU: daftarkan di sini.
    var SECTIONS = {
        jadwal: { title: 'Jadwal Siaran', init: loadJadwal }
    };

    function switchSection(id) {
        document.querySelectorAll('.dash-nav-item').forEach(function (btn) {
            btn.classList.toggle('active', btn.dataset.section === id);
        });
        document.querySelectorAll('.dash-section').forEach(function (section) {
            section.style.display = section.id === 'section-' + id ? 'block' : 'none';
        });
        if (SECTIONS[id] && !sectionsInitialized[id]) {
            sectionsInitialized[id] = true;
            SECTIONS[id].init();
        }
    }

    document.querySelectorAll('.dash-nav-item').forEach(function (btn) {
        btn.addEventListener('click', function () {
            switchSection(btn.dataset.section);
        });
    });

    /* ---------------- Init ---------------- */

    switchSection('jadwal');
})();
