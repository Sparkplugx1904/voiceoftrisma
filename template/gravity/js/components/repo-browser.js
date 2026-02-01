/* js/components/repo-browser.js */
import { fetchAllUploads, organizeByDate, resolveAudioUrl } from '../api.js';

export class RepoBrowser {
    constructor(containerId, playerInstance, liveManager) {
        this.container = document.getElementById(containerId);
        this.player = playerInstance;
        this.liveManager = liveManager;
        this.dataTree = null;
        this.flatData = null;
    }

    async render(params) {
        this.container.innerHTML = '<div style="text-align:center; padding:40px;"><span class="material-symbols-outlined" style="font-size:48px; animation:spin 1s linear infinite;">progress_activity</span><p>Memuat Repository...</p></div>';

        try {
            if (!this.flatData) {
                this.flatData = await fetchAllUploads();
                this.dataTree = organizeByDate(this.flatData);
            }

            // Params: [year, month, date]
            const [year, month, date] = params;

            if (!year) {
                this.renderYears();
            } else if (!month) {
                this.renderMonths(year);
            } else if (!date) {
                this.renderDates(year, month);
            } else {
                this.renderFiles(year, month, date);
            }

        } catch (err) {
            this.container.innerHTML = `<div class="card" style="color:red; text-align:center;">Gagal memuat data: ${err.message}</div>`;
        }
    }

    renderHeader(breadcrumbs) {
        const html = `
            <div class="repo-header">
                <div class="breadcrumb">
                    <div class="breadcrumb-item" onclick="window.location.hash='#/repo'">Repository</div>
                    ${breadcrumbs.map(b => `
                        <span class="breadcrumb-separator">/</span>
                        <div class="breadcrumb-item ${b.active ? 'active' : ''}" 
                             ${b.link ? `onclick="window.location.hash='${b.link}'"` : ''}>
                             ${b.label}
                        </div>
                    `).join('')}
                </div>
            </div>
        `;
        return html;
    }

    renderYears() {
        const years = Object.keys(this.dataTree).sort((a, b) => b - a);

        const getYearCount = (y) => {
            let count = 0;
            const months = this.dataTree[y];
            // Months object -> dates object -> files array
            Object.values(months).forEach(dates => {
                Object.values(dates).forEach(files => count += files.length);
            });
            return count;
        };

        let gridHtml = years.map(y => `
            <div class="repo-item" onclick="window.location.hash='#/repo/${y}'">
                <span class="material-symbols-outlined repo-icon folder">folder</span>
                <span class="repo-label">${y}</span>
                <span class="repo-date">${getYearCount(y)} File</span>
            </div>
        `).join('');

        this.container.innerHTML = `
            <div class="repo-container">
                ${this.renderHeader([])}
                <div class="repo-grid">
                    ${gridHtml}
                </div>
            </div>
        `;
    }

    renderMonths(year) {
        const months = Object.keys(this.dataTree[year] || {}).sort((a, b) => a - b);
        const monthNames = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"];

        const getMonthCount = (m) => {
            let count = 0;
            const dates = this.dataTree[year][m];
            Object.values(dates).forEach(files => count += files.length);
            return count;
        }

        let gridHtml = months.map(m => {
            const count = getMonthCount(m);
            const name = monthNames[parseInt(m)] || m;
            return `
            <div class="repo-item" onclick="window.location.hash='#/repo/${year}/${m}'">
                <span class="material-symbols-outlined repo-icon folder">folder_open</span>
                <span class="repo-label">${name}</span>
                <span class="repo-date">${count} Audio</span>
            </div>
            `;
        }).join('');

        const breadcrumbs = [
            { label: year, active: true }
        ];

        this.container.innerHTML = `
            <div class="repo-container">
                ${this.renderHeader(breadcrumbs)}
                <div class="repo-grid">
                    ${gridHtml}
                </div>
            </div>
        `;
    }

    renderDates(year, month) {
        const dates = Object.keys(this.dataTree[year]?.[month] || {}).sort();
        const monthNames = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"];
        const monthName = monthNames[parseInt(month)] || month;

        let gridHtml = dates.map(d => {
            const count = this.dataTree[year][month][d].length;
            return `
            <div class="repo-item" onclick="window.location.hash='#/repo/${year}/${month}/${d}'">
                <span class="material-symbols-outlined repo-icon folder">calendar_month</span>
                <span class="repo-label">Tgl ${d}</span>
                <span class="repo-date">${count} Audio</span>
            </div>
            `;
        }).join('');

        const breadcrumbs = [
            { label: year, link: `#/repo/${year}` },
            { label: monthName, active: true }
        ];

        this.container.innerHTML = `
            <div class="repo-container">
                ${this.renderHeader(breadcrumbs)}
                <div class="repo-grid">
                    ${gridHtml}
                </div>
            </div>
        `;
    }

    renderFiles(year, month, date) {
        const files = this.dataTree[year]?.[month]?.[date] || [];
        // sort by date desc (time)
        files.sort((a, b) => new Date(b.date) - new Date(a.date));

        const monthNames = ["", "Januari", "Februari", "Maret", "April", "Mei", "Juni", "Juli", "Agustus", "September", "Oktober", "November", "Desember"];
        const monthName = monthNames[parseInt(month)] || month;

        let listHtml = files.map(f => `
            <div class="repo-row" data-id="${f.id}">
                <div class="repo-row-icon">
                    <span class="material-symbols-outlined">audio_file</span>
                </div>
                <div class="repo-row-name">
                    <div>${f.title}</div>
                    <div style="font-size:0.8rem; color:var(--md-sys-color-secondary);">${f.id}</div>
                </div>
                <div class="repo-row-meta">
                    ${f.date ? f.date.split('T')[1].substring(0, 5) : ''}
                </div>
            </div>
        `).join('');

        const breadcrumbs = [
            { label: year, link: `#/repo/${year}` },
            { label: monthName, link: `#/repo/${year}/${month}` },
            { label: `Tgl ${date}`, active: true }
        ];

        this.container.innerHTML = `
            <div class="repo-container">
                ${this.renderHeader(breadcrumbs)}
                <div class="repo-list">
                    ${listHtml}
                </div>
            </div>
        `;

        // Add Click Events for playing
        this.container.querySelectorAll('.repo-row').forEach(row => {
            row.addEventListener('click', async (e) => {
                if (this.liveManager) this.liveManager.stop();

                const id = row.dataset.id;
                const file = files.find(f => f.id === id);

                row.style.opacity = '0.5';
                row.style.cursor = 'wait';

                if (file) {
                    try {
                        let audioUrl = await resolveAudioUrl(file.id);
                        if (!audioUrl) {
                            console.warn("Helpers: Could not resolve MP3, trying fallback.");
                            audioUrl = `https://archive.org/download/${file.id}/${file.id}.mp3`;
                        }
                        this.player.load(audioUrl, file.title, dateOnly(file.date));
                    } catch (err) {
                        alert("Gagal memuat audio: " + err.message);
                    } finally {
                        row.style.opacity = '1';
                        row.style.cursor = 'pointer';
                    }
                }
            });
        });
    }
}

function dateOnly(iso) {
    if (!iso) return '';
    return iso.split('T')[0];
}
