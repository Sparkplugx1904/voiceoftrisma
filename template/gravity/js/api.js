/* js/api.js */

const BASE_URL = "https://archive.org/services/search/beta/page_production/";
const ACCOUNT = "@16_i_gede_ananda_pradnyana";

// Simple in-memory cache
const cache = {
    allUploads: null,
    timestamp: 0
};

const CACHE_DURATION = 1000 * 60 * 5; // 5 minutes

/**
 * Fetch all uploads from the specific account.
 */
export async function fetchAllUploads() {
    const now = Date.now();
    if (cache.allUploads && (now - cache.timestamp < CACHE_DURATION)) {
        return cache.allUploads;
    }

    const hitsPerPage = 25;
    const url = `${BASE_URL}?page_type=account_details&page_target=${ACCOUNT}&page_elements=[%22uploads%22]&hits_per_page=${hitsPerPage}&page=1&sort=publicdate:desc`;

    try {
        const response = await fetch(url, { cache: 'no-store' });
        if (!response.ok) throw new Error("Tidak dapat memuat data dari Archive.org");

        const data = await response.json();
        const uploads = data?.response?.body?.page_elements?.uploads;
        const initialHits = uploads?.hits?.hits || [];
        const total = uploads?.hits?.total || 0;

        let allHits = [...initialHits];
        const totalPages = Math.ceil(total / hitsPerPage);
        const maxPages = Math.min(totalPages, 100);

        // Fetch remaining pages in parallel
        const promises = [];
        for (let p = 2; p <= maxPages; p++) {
            const pUrl = `${BASE_URL}?page_type=account_details&page_target=${ACCOUNT}&page_elements=[%22uploads%22]&hits_per_page=${hitsPerPage}&page=${p}&sort=publicdate:desc`;
            promises.push(fetch(pUrl, { cache: 'no-store' }));
        }

        const responses = await Promise.all(promises);
        for (const res of responses) {
            if (res.ok) {
                const json = await res.json();
                const u = json?.response?.body?.page_elements?.uploads;
                const h = u?.hits?.hits || [];
                allHits = allHits.concat(h);
            }
        }

        // Map to cleaner objects
        const items = allHits.map(h => {
            const fields = h.fields;
            return {
                id: fields.identifier,
                title: fields.title || fields.identifier,
                date: fields.publicdate, // "2023-08-29T..."
                description: fields.description,
                url: `https://archive.org/details/${fields.identifier}`,
                // Note: We cannot guess the download URL accurately here.
                // We must resolve it via metadata API on demand.
            };
        });

        cache.allUploads = items;
        cache.timestamp = now;
        return items;

    } catch (error) {
        console.error("API Error:", error);
        throw error;
    }
}

/**
 * Resolves the actual MP3 URL for a given identifier by fetching metadata.
 */
export async function resolveAudioUrl(identifier) {
    try {
        const metaUrl = `https://archive.org/metadata/${identifier}`;
        const res = await fetch(metaUrl);
        if (!res.ok) throw new Error('Gagal mengambil metadata audio');

        const meta = await res.json();
        const files = meta.files || [];

        // Find MP3
        const candidate = files.find(f => (f.format || '').toLowerCase().includes('mp3'))
            || files.find(f => (f.name || '').toLowerCase().endsWith('.mp3'));

        if (!candidate) return null;

        // Construct download URL
        return `https://archive.org/download/${identifier}/${encodeURIComponent(candidate.name)}`;
    } catch (e) {
        console.error("Resolve Audio Error:", e);
        return null;
    }
}

/**
 * Organizes flat list of items into a Date Tree
 */
export function organizeByDate(items) {
    const tree = {};

    items.forEach(item => {
        if (!item.date) return;

        const dateObj = new Date(item.date);
        const year = dateObj.getFullYear().toString();
        const month = (dateObj.getMonth() + 1).toString().padStart(2, '0');
        const day = dateObj.getDate().toString().padStart(2, '0');

        if (!tree[year]) tree[year] = {};
        if (!tree[year][month]) tree[year][month] = {};
        if (!tree[year][month][day]) tree[year][month][day] = [];

        tree[year][month][day].push(item);
    });

    return tree;
}
