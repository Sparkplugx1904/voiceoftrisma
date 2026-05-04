const WORKER_URL = 'https://archive-cache-worker.anandapradnyana68.workers.dev/';

const state = {
    data: [],
    page: 1,
    totalPages: 1,
    hitsPerPage: 24,
    query: '',
    sortDesc: true,
    isSearch: false,
    currentRepoItems: []
};

const listContainer = document.getElementById('listContainer');
const listEl = document.getElementById('list');
const paginationEl = document.getElementById('pagination');
const searchInput = document.getElementById('q');
const countDisplay = document.getElementById('countDisplay');
const searchInd = document.getElementById('searchIndicator');
const searchQ = document.getElementById('searchQ');

// Repo View Elements
const repoView = document.getElementById('repoView');
const repoTitle = document.getElementById('repoTitle');
const repoSub = document.getElementById('repoSub');
const repoFilesList = document.getElementById('repoFilesList');
const repoReadme = document.getElementById('repoReadme');
const readmeTitle = document.getElementById('readmeTitle');
const readmeText = document.getElementById('readmeText');
const readmePrograms = document.getElementById('readmePrograms');

const playerTitle = document.getElementById('playerTitle');
const playerSub = document.getElementById('playerSub');
const archiveLink = document.getElementById('archiveLink');
const playerBar = document.getElementById('playerBar');

function formatDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return `${String(d.getUTCDate()).padStart(2, '0')}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCFullYear()).slice(-2)}`;
}

async function loadData() {
    showLoading();
    const params = new URLSearchParams({
        page: state.page,
        hits_per_page: state.hitsPerPage,
        query: state.query,
        sort: state.sortDesc ? 'publicdate:desc' : 'publicdate:asc'
    });

    try {
        const response = await fetch(`${WORKER_URL}?${params.toString()}`);
        if (!response.ok) throw new Error('Gagal mengambil data dari Worker');

        const result = await response.json();

        state.data = result.data.map(it => ({
            id: it.id,
            title: it.title,
            publicdate: it.publicdate,
            date: formatDate(it.publicdate),
            url: `https://archive.org/details/${it.id}`
        }));

        state.totalPages = result.total_pages || 1;

        render();
        renderPagination();

        if (state.data.length === 0 && state.query) {
            listEl.innerHTML = `<div class="empty-state">Tidak ditemukan hasil untuk "${state.query}"</div>`;
        }
    } catch (e) {
        console.error("Gagal memuat data:", e);
        listEl.innerHTML = `<div class="empty-state"><i class="fa-solid fa-triangle-exclamation fa-3x empty-icon"></i><br>${e.message}</div>`;
    }
}

async function loadJson() {
    state.isSearch = false;
    state.query = '';
    state.page = 1;
    if (searchInd) searchInd.style.display = 'none';
    await loadData();
}

async function loadAllForSearch(q) {
    state.query = q.toLowerCase();
    state.isSearch = true;
    state.page = 1;

    if (searchInd) {
        searchInd.style.display = 'flex';
        searchQ.innerHTML = `"${q}"`;
    }

    await loadData();
}

function displayPage(page) {
    state.page = page;
    loadData();
}

function showLoading() {
    listEl.innerHTML = `<div class="loading-state"><i class="fa-solid fa-circle-notch fa-spin fa-3x loading-icon"></i><br>Memuat arsip...</div>`;
}

function render() {
    listEl.innerHTML = '';
    if (!state.data.length) {
        listEl.innerHTML = `<div class="empty-state"><i class="fa-solid fa-box-open fa-3x empty-icon"></i><br>Tidak ada rekaman ditemukan.</div>`;
        countDisplay.textContent = '0';
        return;
    }

    const groups = {}, gDates = {};
    for (const it of state.data) {
        if (!groups[it.date]) groups[it.date] = [];
        groups[it.date].push(it);
        gDates[it.date] = it.publicdate;
    }

    const keys = Object.keys(groups).sort((a, b) => { const da = new Date(gDates[a] || 0), db = new Date(gDates[b] || 0); return state.sortDesc ? db - da : da - db; });
    countDisplay.innerHTML = `${keys.length} ` + (keys.length === state.hitsPerPage ? "(dari total date)" : "tanggal siaran");

    for (const date of keys) {
        const items = groups[date].sort((a, b) => state.sortDesc ? (b.id || '').localeCompare(a.id || '') : (a.id || '').localeCompare(b.id || ''));

        const card = document.createElement('div');
        card.className = 'card glass-panel repo-card';
        card.innerHTML = `
        <div class="card-body" style="padding-bottom: 0;">
            <div class="repo-card-header">
                <i class="fa-solid fa-book-bookmark"></i>
                <h3>Arsip ${date}</h3>
            </div>
            <p class="repo-card-meta">
                <i class="fa-solid fa-file-audio"></i> ${items.length} rekaman tersedia
            </p>
            <div class="repo-card-tags">
                ${items.slice(0, 3).map(i => `<span>${i.id.substring(13, 28)}..</span>`).join('')}
                ${items.length > 3 ? `<span class="tag-more">+${items.length - 3} lainnya</span>` : ''}
            </div>
        </div>
        `;
        card.addEventListener('click', () => showDatePopup(date, items));
        listEl.appendChild(card);
    }
}

// --- Repo View Logic ---
let currentProgBtn = null;

function closeRepo() {
    if (playerBar) playerBar.style.display = 'none';
    const url = new URL(location.href);
    url.searchParams.delete('identifier');
    window.location.href = url.toString();
}

function renderReadme(data) {
    if (!data.description && !data.programs?.length) return;
    repoReadme.style.display = 'block';
    document.getElementById('readmePrograms').parentElement.style.display = 'block';

    readmeTitle.textContent = data.title || "Informasi Siaran";
    readmeText.textContent = data.description || "";
    readmePrograms.innerHTML = '';

    if (data.programs && data.programs.length > 0) {
        data.programs.forEach((prog, idx) => {
            const tsSeconds = parseTimestamp(prog.timestamp);
            const tsDisplay = Player.formatTime(tsSeconds);
            const div = document.createElement('div');

            div.className = "segment-block";
            div.innerHTML = `
                <div class="segment-card">
                    <div class="segment-content">
                        <button class="prog-ts-btn" data-seconds="${tsSeconds}">
                            <i class="fa-solid fa-play"></i> Munculkan di ${tsDisplay}
                        </button>
                        <div class="segment-info">
                            <h5 class="segment-title">
                                ${prog.program || 'Segmen ' + (idx + 1)}
                                ${prog.announcer ? `<span class="segment-announcer"><i class="fa-solid fa-microphone-lines"></i> ${prog.announcer}</span>` : ''}
                            </h5>
                            ${prog.topic ? `<p class="segment-topic"><i class="fa-solid fa-hashtag" style="opacity: 0.7;"></i> <strong>Topik:</strong> ${prog.topic}</p>` : ''}
                            <p class="segment-desc">${prog.description || ''}</p>
                        </div>
                    </div>
                </div>
            `;
            const btn = div.querySelector('.prog-ts-btn');
            btn.addEventListener('click', (e) => seekTo(e, tsSeconds, btn));
            readmePrograms.appendChild(div);
        });
    } else {
        document.getElementById('readmePrograms').parentElement.style.display = 'none';
    }
}

function parseTimestamp(ts) {
    if (!ts) return 0;
    const clean = ts.replace(',', '.').split('.')[0];
    const parts = clean.split(':').map(Number);
    if (parts.length === 3) return parts[0] * 3600 + parts[1] * 60 + parts[2];
    if (parts.length === 2) return parts[0] * 60 + parts[1];
    return parseInt(parts[0]) || 0;
}

function seekTo(event, seconds, btn) {
    event.stopPropagation();
    Player.audio.currentTime = seconds;
    Player.play();
    if (currentProgBtn) currentProgBtn.classList.remove('active');
    currentProgBtn = btn;
    btn.classList.add('active');
}

// --- Pagination ---
function renderPagination() {
    paginationEl.innerHTML = ''; if (state.totalPages <= 1) return;
    const make = (label, page, disabled, active) => {
        const b = document.createElement('button');
        b.className = `glass-panel page-btn ${active ? 'active' : ''}`;
        b.innerHTML = label; b.disabled = disabled;
        b.onclick = () => { if (!disabled) { displayPage(page); window.scrollTo({ top: 0, behavior: 'smooth' }); } };
        return b;
    };
    paginationEl.appendChild(make('<i class="fa-solid fa-chevron-left"></i>', state.page - 1, state.page === 1));
    const s = Math.max(1, state.page - 2), e = Math.min(state.totalPages, state.page + 2);
    for (let i = s; i <= e; i++) paginationEl.appendChild(make(i, i, false, i === state.page));
    paginationEl.appendChild(make('<i class="fa-solid fa-chevron-right"></i>', state.page + 1, state.page === state.totalPages));
}

// Subtitles Logic
let currentSubtitles = [];
let activeSubtitleIndex = -1;

function parseTranscriptJSON(data) {
    const items = data?.transcription;
    if (!Array.isArray(items)) return [];
    return items.map(item => ({
        start: (item.offsets?.from ?? 0) / 1000,
        end: (item.offsets?.to ?? 0) / 1000,
        text: (item.text || '').trim().replace(/\n/g, '<br>')
    })).filter(item => item.end > item.start);
}

async function loadSubtitles(identifier) {
    currentSubtitles = []; activeSubtitleIndex = -1;
    const subContainer = document.getElementById('subtitleContainer');
    const subText = document.getElementById('subtitleText');
    if (subContainer) subContainer.style.display = 'none';
    if (subText) subText.innerHTML = '';
    try {
        const res = await fetch(`https://archive.org/download/${identifier}/transcript.json`);
        if (res.ok) { const jsonData = await res.json(); currentSubtitles = parseTranscriptJSON(jsonData); }
    } catch (e) { console.warn("No transcript.json found"); }
}

Player.audio.addEventListener('timeupdate', () => {
    syncTimestamp();
    const subContainer = document.getElementById('subtitleContainer');
    const subText = document.getElementById('subtitleText');
    if (currentSubtitles.length > 0 && subContainer && subText) {
        const ct = Player.audio.currentTime;
        if (activeSubtitleIndex >= 0 && activeSubtitleIndex < currentSubtitles.length) {
            const sub = currentSubtitles[activeSubtitleIndex];
            if (!(ct >= sub.start && ct <= sub.end)) activeSubtitleIndex = -1;
        }
        if (activeSubtitleIndex === -1) {
            for (let i = 0; i < currentSubtitles.length; i++) {
                const sub = currentSubtitles[i];
                if (ct >= sub.start && ct <= sub.end) {
                    activeSubtitleIndex = i;
                    subText.innerHTML = sub.text;
                    subContainer.style.display = 'block';
                    break;
                }
            }
            if (activeSubtitleIndex === -1) subContainer.style.display = 'none';
        }
    } else if (subContainer) {
        subContainer.style.display = 'none';
    }
});

function syncTimestamp() {
    if (!repoReadme || repoReadme.style.display === 'none') return;
    const btns = document.querySelectorAll('.prog-ts-btn');
    if (!btns.length) return;
    const ct = Player.audio.currentTime;
    let lastMatch = null;
    btns.forEach(b => { if (parseFloat(b.dataset.seconds) <= ct) lastMatch = b; });
    if (lastMatch !== currentProgBtn) {
        if (currentProgBtn) currentProgBtn.classList.remove('active');
        if (lastMatch) lastMatch.classList.add('active');
        currentProgBtn = lastMatch;
    }
}

function playDirectUrl(url, title, archiveUrl, btnElem) {
    playerTitle.textContent = title;
    playerSub.textContent = 'Memuat audio...';
    archiveLink.href = archiveUrl;
    document.querySelectorAll('.repofile-play-btn i').forEach(i => i.className = 'fa-solid fa-play');
    if (btnElem) btnElem.querySelector('i').className = 'fa-solid fa-spinner fa-spin';

    const identifier = archiveUrl.split('/').pop();
    loadSubtitles(identifier);

    Player.play(url);
    Player.audio.onplay = () => {
        playerSub.textContent = decodeURIComponent(url.split('/').pop());
        if (btnElem) btnElem.querySelector('i').className = 'fa-solid fa-volume-high';
    };
}

function cueStandbyTrack(url, title, archiveUrl) {
    playerTitle.textContent = title;
    playerSub.textContent = 'Tekan play untuk memulai';
    archiveLink.href = archiveUrl;
    Player.audio.src = url;
    Player.audio.load();
    const identifier = archiveUrl.split('/').pop();
    loadSubtitles(identifier);
}

async function loadRepoFromIdentifier(identifier) {
    listContainer.style.display = 'none';
    if (playerBar) playerBar.style.display = 'flex';
    repoView.style.display = 'block';
    repoTitle.innerHTML = `<i class="fa-solid fa-circle-notch fa-spin" style="color: var(--primary-color);"></i> Memuat arsip...`;
    repoSub.textContent = 'Mengambil metadata dari Archive.org...';
    repoFilesList.innerHTML = '';
    repoReadme.style.display = 'none';

    ['heroSection', 'countTag', 'searchIndicator'].forEach(id => {
        const el = document.getElementById(id); if (el) el.style.display = 'none';
    });

    try {
        const res = await fetch(`https://archive.org/metadata/${identifier}`);
        if (!res.ok) throw new Error('Rekaman tidak ditemukan');
        const meta = await res.json();
        const title = meta.metadata?.title || identifier;
        const publicdate = meta.metadata?.publicdate;
        const date = formatDate(publicdate);
        const files = meta.files || [];

        repoTitle.innerHTML = `<i class="fa-solid fa-book-bookmark"></i> ${title}`;
        repoSub.innerHTML = `Diupload pada <b>${date}</b> &nbsp;·&nbsp; <a href="https://archive.org/details/${identifier}" target="_blank" style="color:var(--primary-color);">Buka di Archive.org</a>`;

        const HIDE = ['.torrent', '_meta.xml', '_files.xml', '_meta.sqlite', '.btree'];
        const shown = files.filter(f => !HIDE.some(s => f.name.endsWith(s)));

        repoFilesList.innerHTML = shown.map(f => {
            const lname = f.name.toLowerCase();
            const isAudio = (f.format || '').toLowerCase().includes('audio') || (f.format || '').toLowerCase().includes('mp3') || lname.match(/\.(mp3|aac|wav|ogg|flac|m4a)$/);
            const isSimple = isAudio || lname.endsWith('.json') || lname.endsWith('.srt') || lname.endsWith('.txt');
            const fileUrl = `https://archive.org/download/${identifier}/${encodeURIComponent(f.name)}`;
            const icon = isAudio ? 'fa-file-audio' : 'fa-file';
            return `
            <div class="repofile-row" data-audio="${isAudio}" data-is-simple="${isSimple}">
                <div class="repofile-left">
                    <i class="fa-solid ${icon} repofile-icon"></i>
                    <span class="repofile-name">${f.name}</span>
                </div>
                <div class="repofile-actions">
                    ${isAudio ? `<button class="repofile-btn repofile-play-btn" data-url="${fileUrl}" data-title="${title.replace(/"/g, '&quot;')}" data-archive="https://archive.org/details/${identifier}"><i class="fa-solid fa-play"></i></button>` : ''}
                    <a href="${fileUrl}" target="_blank" class="repofile-btn"><i class="fa-solid fa-download"></i></a>
                </div>
            </div>`;
        }).join('');

        setRepoViewMode('simple');
        document.querySelectorAll('.repofile-play-btn').forEach(btn => {
            btn.addEventListener('click', e => { e.stopPropagation(); playDirectUrl(btn.dataset.url, btn.dataset.title, btn.dataset.archive, btn); });
        });

        const firstAudioBtn = document.querySelector('#repoFilesList .repofile-row[data-audio="true"] .repofile-play-btn');
        if (firstAudioBtn) cueStandbyTrack(firstAudioBtn.dataset.url, firstAudioBtn.dataset.title, firstAudioBtn.dataset.archive);

        if (meta.metadata?.description) {
            repoReadme.style.display = 'block';
            readmeTitle.textContent = title;
            readmeText.innerHTML = meta.metadata.description;
        }
    } catch (e) {
        repoTitle.innerHTML = `Gagal Memuat`;
        repoSub.textContent = e.message;
    }
}

function setRepoViewMode(mode) {
    document.querySelectorAll('#repoFilesList .repofile-row').forEach(row => {
        row.style.display = (mode === 'simple' && row.dataset.isSimple !== 'true') ? 'none' : 'flex';
    });
}

function toggleSort() {
    state.sortDesc = !state.sortDesc;
    document.getElementById('sortBtn').innerHTML = state.sortDesc ? '<i class="fa-solid fa-sort"></i> Terbaru' : '<i class="fa-solid fa-sort"></i> Terlama';
    state.page = 1;
    loadData();
}

async function doRefresh() {
    await loadJson();
    if (state.isSearch && state.query) await loadAllForSearch(state.query);
}

function clearSearch() {
    searchInput.value = ''; if (searchInd) searchInd.style.display = 'none';
    state.isSearch = false; state.query = ''; state.page = 1;
    loadData();
}

function doSearch() {
    const q = searchInput.value.trim();
    if (q) loadAllForSearch(q); else clearSearch();
}

if (document.getElementById('mainSearchBtn')) document.getElementById('mainSearchBtn').onclick = doSearch;
if (searchInput) searchInput.addEventListener('keydown', e => { if (e.key === 'Enter') doSearch(); });

const urlP = new URLSearchParams(location.search);
const initIdentifier = urlP.get('identifier');
const initQ = urlP.get('query') || urlP.get('title');

if (initIdentifier) {
    loadRepoFromIdentifier(initIdentifier);
} else {
    loadJson().then(() => { if (initQ) { searchInput.value = initQ; loadAllForSearch(initQ); } });
}

function showDatePopup(date, items) {
    const overlay = document.getElementById('datePopupOverlay');
    const title = document.getElementById('datePopupTitle');
    const list = document.getElementById('datePopupList');
    title.innerHTML = `Arsip: ${date}`;
    list.innerHTML = items.map((it) => `
        <div class="repofile-row" onclick="navigateToRepo('${it.id}')">
            <div class="repofile-left"><i class="fa-solid fa-file-audio"></i> ${it.title}</div>
            <button class="repofile-btn"><i class="fa-solid fa-play"></i></button>
        </div>
    `).join('');
    overlay.style.display = 'flex';
}

function navigateToRepo(id) {
    const url = new URL(location.href);
    url.searchParams.set('identifier', id);
    window.location.href = url.toString();
}

function closeDatePopup() { document.getElementById('datePopupOverlay').style.display = 'none'; }
