/* js/main.js */
import { Router } from './router.js';
import { initHeader } from './components/header.js';
import { AudioPlayer } from './components/audio-player.js';
import { RepoBrowser } from './components/repo-browser.js';
import { LiveStreamManager } from './components/live-stream.js';

// Initialize App
document.addEventListener('DOMContentLoaded', () => {

    // 1. Init Shell
    initHeader();

    // 2. Init Player
    const player = new AudioPlayer();

    // 2b. Init Live Manager
    const liveManager = new LiveStreamManager(player);

    // 3. Init Components
    const repoBrowser = new RepoBrowser('app', player, liveManager);

    // 4. Init Router
    const router = new Router((viewName, params, routeConfig) => {
        const app = document.getElementById('app');

        // Update Title via Config
        const pageTitleEl = document.getElementById('pageTitle');
        if (pageTitleEl) pageTitleEl.textContent = routeConfig.title;

        // Render View
        if (viewName === 'home') {
            renderHome(app, liveManager);
        } else if (viewName === 'repo') {
            repoBrowser.render(params);
        } else {
            app.innerHTML = `
                <div style="text-align:center; padding: 50px;">
                    <h1>404</h1>
                    <p>Halaman tidak ditemukan.</p>
                    <a href="#/" class="btn-primary" style="text-decoration:none;">Kembali ke Beranda</a>
                </div>
            `;
        }
    });
});

function renderHome(container, liveManager) {
    container.innerHTML = `
        <div class="home-container">
            <img src="./madyapadma-voice-of-trisma.svg" alt="Voice of Trisma" style="max-width: 480px; width: 100%; height: auto; margin-bottom: 20px; padding-bottom: 10px;">
            
            <!-- Big Central Player Button -->
            <button class="central-player-btn" id="btnBigPlay" title="Putar Radio Live">
                <span class="material-symbols-outlined" id="bigPlayIcon">play_arrow</span>
            </button>
            
            <div id="liveStatus" class="live-status-text">OFF AIR</div>

            <!-- Fitur Baru Card (Kept as requested) -->
            <div class="card" style="text-align: left; max-width: 600px; width: 100%; margin-top:20px;">
                <h3>Fitur Baru</h3>
                <ul>
                    <li><strong>Repository Browsing:</strong> Jelajahi arsip audio berdasarkan Tahun dan Bulan seperti file manager.</li>
                    <li><strong>Live Radio:</strong> Streaming langsung dengan fitur Self-Healing (Auto-Reconnect).</li>
                    <li><strong>Dark Mode:</strong> Tampilan gelap yang nyaman di mata, otomatis mengikuti preferensi sistem.</li>
                    <li><strong>Audio Player:</strong> Pemutar musik yang persisten di bawah, tidak putus saat navigasi.</li>
                </ul>
            </div>
        </div>
    `;

    // Logic for the Big Button
    const btn = container.querySelector('#btnBigPlay');
    const icon = container.querySelector('#bigPlayIcon');
    const status = container.querySelector('#liveStatus');

    // Sync initial state
    if (liveManager.isActive && !liveManager.player.audio.paused) {
        btn.classList.add('active');
        icon.textContent = 'stop'; // or pause
        status.textContent = "ON AIR - Live Streaming";
    }

    btn.addEventListener('click', () => {
        if (liveManager.isActive) {
            // Stop logic
            liveManager.stop();
            liveManager.player.pause(); // pause the audio
            btn.classList.remove('active');
            icon.textContent = 'play_arrow';
            status.textContent = "OFF AIR";
        } else {
            // Start logic
            liveManager.start();
            btn.classList.add('active');
            icon.textContent = 'hourglass_empty'; // Loading state
            status.textContent = "Connecting...";

            // Wait for play (simplified)
            // LiveManager playStream sets the source. 
            // We can listen to player events or just assume it starts.
            // Let's add a small timeout or listener to switch icon to stop/pause
            setTimeout(() => {
                icon.textContent = 'stop';
                status.textContent = "ON AIR - Live Streaming";
            }, 1000);
        }
    });

    // Optional: We could listen to player pause events to untoggle the button 
    // if the user pauses via the bottom bar.
    liveManager.player.audio.addEventListener('pause', () => {
        if (liveManager.isActive) { // Only if we were in live mode
            // liveManager.stop(); // Maybe? Or just update UI
            btn.classList.remove('active');
            icon.textContent = 'play_arrow';
            status.textContent = "Paused";
        }
    });

    liveManager.player.audio.addEventListener('play', () => {
        // If playing live url
        if (liveManager.player.audio.src.includes('klikhost')) {
            btn.classList.add('active');
            icon.textContent = 'stop';
            status.textContent = "ON AIR - Live Streaming";
        }
    });
}
