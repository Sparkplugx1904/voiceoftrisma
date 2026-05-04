/**
 * Unified Player Logic for Voice of Trisma
 */
const Player = {
    audio: document.getElementById('mainAudio'),
    playPauseBtn: document.getElementById('playPauseBtn'),
    morphPath: document.querySelector('.morph-path'),
    progressBar: document.getElementById('progressBar'),
    volumeBtn: document.getElementById('volumeBtn'),
    volumeSliderWrapper: document.getElementById('volumeSliderWrapper'),
    volumeSlider: document.getElementById('volumeSlider'),
    timeText: document.getElementById('timeText'),

    isPlaying: false,
    mode: 'stream', // 'stream' or 'archive'

    init() {
        if (!this.audio || !this.playPauseBtn) return;

        this.playPauseBtn.addEventListener('click', () => this.togglePlay());

        if (this.volumeBtn) {
            this.volumeBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                this.volumeSliderWrapper.classList.toggle('show');
            });
        }

        if (this.volumeSlider) {
            this.volumeSlider.addEventListener('input', (e) => {
                this.audio.volume = e.target.value / 100;
            });
        }

        document.addEventListener('click', (e) => {
            if (this.volumeSliderWrapper && !this.volumeSliderWrapper.contains(e.target) && !this.volumeBtn.contains(e.target)) {
                this.volumeSliderWrapper.classList.remove('show');
            }
        });

        this.audio.addEventListener('timeupdate', () => this.updateProgress());
        this.audio.addEventListener('loadedmetadata', () => this.updateProgress());

        if (this.progressBar) {
            this.progressBar.addEventListener('input', (e) => {
                if (this.mode === 'archive') {
                    const time = (e.target.value / 100) * this.audio.duration;
                    this.audio.currentTime = time;
                }
            });
        }

        // Detect mode
        if (window.location.pathname.includes('/archive')) {
            this.setMode('archive');
        } else {
            this.setMode('stream');
        }
    },

    setMode(mode) {
        this.mode = mode;
        if (mode === 'stream') {
            if (this.progressBar) this.progressBar.style.display = 'none';
            if (this.timeText) this.timeText.style.display = 'none';
        } else {
            if (this.progressBar) this.progressBar.style.display = 'block';
            if (this.timeText) this.timeText.style.display = 'block';
        }
    },

    togglePlay() {
        if (this.isPlaying) {
            this.pause();
        } else {
            this.play();
        }
    },

    play(url) {
        if (url) {
            this.audio.src = url;
        } else if (this.mode === 'stream' && !this.audio.src.includes('klikhost')) {
             this.audio.src = 'http://i.klikhost.com:8502/stream?' + 't=' + new Date().getTime();
        } else if (this.mode === 'stream') {
             // Refresh stream URL to avoid cache/stale connection
             this.audio.src = 'http://i.klikhost.com:8502/stream?' + 't=' + new Date().getTime();
        }

        this.audio.play().then(() => {
            this.isPlaying = true;
            this.updateUI();
        }).catch(e => {
            console.error("Playback failed", e);
        });
    },

    pause() {
        this.audio.pause();
        if (this.mode === 'stream') {
            this.audio.src = ''; // Stop downloading stream
        }
        this.isPlaying = false;
        this.updateUI();
    },

    updateUI() {
        if (this.isPlaying) {
            this.morphPath.setAttribute('d', 'M 6 5 L 10 5 L 10 19 L 6 19 Z M 14 5 L 18 5 L 18 19 L 14 19 Z');
            this.playPauseBtn.classList.add('playing');
        } else {
            this.morphPath.setAttribute('d', 'M 8 5 L 19 12 L 8 19 Z');
            this.playPauseBtn.classList.remove('playing');
        }
    },

    updateProgress() {
        if (this.mode === 'archive' && this.audio.duration) {
            const percent = (this.audio.currentTime / this.audio.duration) * 100;
            this.progressBar.value = percent;
            this.progressBar.style.setProperty('--val', percent + '%');

            const current = this.formatTime(this.audio.currentTime);
            const total = this.formatTime(this.audio.duration);
            this.timeText.textContent = `${current} / ${total}`;
        }
    },

    formatTime(seconds) {
        const m = Math.floor(seconds / 60);
        const s = Math.floor(seconds % 60);
        return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
    },

    seek(seconds) {
        this.audio.currentTime += seconds;
    }
};

document.addEventListener('DOMContentLoaded', () => Player.init());

// Global functions for backward/forward compatibility
window.seekAudio = (seconds) => Player.seek(seconds);
