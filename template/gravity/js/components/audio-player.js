/* js/components/audio-player.js */

export class AudioPlayer {
    constructor() {
        this.container = document.getElementById('audioPlayerBar');
        this.audio = new Audio();
        this.isPlaying = false;

        this.render();
        this.bindEvents();
    }

    render() {
        this.container.innerHTML = `
            <div class="player-content">
                <div class="player-info">
                    <span class="player-title" id="pTitle">Pilih Audio</span>
                    <span class="player-subtitle" id="pSubtitle">--</span>
                </div>
                
                <div class="player-controls">
                    <button class="ctrl-btn" id="btnPrev" title="Mundur 10d">
                        <span class="material-symbols-outlined">replay_10</span>
                    </button>
                    <button class="ctrl-btn play-pause" id="btnPlay">
                        <span class="material-symbols-outlined" id="iconPlay">play_arrow</span>
                    </button>
                    <button class="ctrl-btn" id="btnNext" title="Maju 10d">
                        <span class="material-symbols-outlined">forward_10</span>
                    </button>
                </div>
                
                <div class="player-progress">
                    <span id="currTime" style="font-size:0.75rem; width:40px; text-align:right;">0:00</span>
                    <div class="progress-bar" id="progBar">
                        <div class="progress-fill" id="progFill"></div>
                    </div>
                    <span id="durTime" style="font-size:0.75rem; width:40px;">0:00</span>
                </div>
                
                <div style="width: 100px; display:flex; align-items:center;">
                     <span class="material-symbols-outlined" style="font-size:20px; color:var(--md-sys-color-secondary);">volume_up</span>
                     <input type="range" min="0" max="1" step="0.1" value="1" style="width:60px; margin-left:8px;" id="volRange">
                </div>
            </div>
        `;

        this.els = {
            title: document.getElementById('pTitle'),
            subtitle: document.getElementById('pSubtitle'),
            btnPlay: document.getElementById('btnPlay'),
            iconPlay: document.getElementById('iconPlay'),
            btnPrev: document.getElementById('btnPrev'),
            btnNext: document.getElementById('btnNext'),
            progBar: document.getElementById('progBar'),
            progFill: document.getElementById('progFill'),
            currTime: document.getElementById('currTime'),
            durTime: document.getElementById('durTime'),
            volRange: document.getElementById('volRange')
        };
    }

    bindEvents() {
        // Play/Pause
        this.els.btnPlay.addEventListener('click', () => {
            if (this.audio.paused) this.play();
            else this.pause();
        });

        // Skip
        this.els.btnPrev.addEventListener('click', () => { this.audio.currentTime -= 10; });
        this.els.btnNext.addEventListener('click', () => { this.audio.currentTime += 10; });

        // Time Update
        this.audio.addEventListener('timeupdate', () => {
            const pct = (this.audio.currentTime / this.audio.duration) * 100;
            this.els.progFill.style.width = `${pct}%`;
            this.els.currTime.textContent = this.formatTime(this.audio.currentTime);
        });

        this.audio.addEventListener('loadedmetadata', () => {
            this.els.durTime.textContent = this.formatTime(this.audio.duration);
        });

        this.audio.addEventListener('ended', () => {
            this.pause();
            this.els.progFill.style.width = '0%';
            this.audio.currentTime = 0;
        });

        // Seek
        this.els.progBar.addEventListener('click', (e) => {
            const rect = this.els.progBar.getBoundingClientRect();
            const pos = (e.clientX - rect.left) / rect.width;
            if (this.audio.duration) {
                this.audio.currentTime = pos * this.audio.duration;
            }
        });

        // Volume
        this.els.volRange.addEventListener('input', (e) => {
            this.audio.volume = e.target.value;
        });
    }

    load(url, title, subtitle, autoPlay = true) {
        this.audio.src = url;
        this.els.title.textContent = title;
        this.els.subtitle.textContent = subtitle || '';
        this.container.classList.add('active'); // Show player bar

        if (autoPlay) {
            this.play();
        }
    }

    play() {
        this.audio.play();
        this.els.iconPlay.textContent = 'pause';
        this.isPlaying = true;
    }

    pause() {
        this.audio.pause();
        this.els.iconPlay.textContent = 'play_arrow';
        this.isPlaying = false;
    }

    formatTime(s) {
        if (!s || isNaN(s)) return "0:00";
        const m = Math.floor(s / 60);
        const sec = Math.floor(s % 60);
        return `${m}:${String(sec).padStart(2, '0')}`;
    }
}
