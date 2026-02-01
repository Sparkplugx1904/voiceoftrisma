/* js/components/live-stream.js */

const STREAM_URL = "https://i.klikhost.com:8502/stream";
const HEARTBEAT_INTERVAL = 2000;

export class LiveStreamManager {
    constructor(player) {
        this.player = player; // Instance of AudioPlayer
        this.isActive = false;
        this.heartbeatTimer = null;
        this.lastPlayPos = -1;
        this.retryCount = 0;
    }

    start() {
        if (this.isActive) return;
        this.isActive = true;
        this.retryCount = 0;

        // Take over the player
        this.playStream();

        // Start Heartbeat
        this.startHeartbeat();
    }

    stop() {
        this.isActive = false;
        this.stopHeartbeat();
        // We don't necessarily stop the player here, 
        // because the user might have switched to a Repo file which triggers this stop.
    }

    playStream() {
        console.log("Starting Live Stream...");
        const timestamp = Date.now();
        // Append timestamp to bust cache/force reconnect
        const url = `${STREAM_URL}?t=${timestamp}`;

        // Use the global player to load this URL
        // We pass a special flag or just standard info
        this.player.load(url, "Live Streaming", "Voice of Trisma", true);

        // Hide progress bar for live stream? 
        // The AudioPlayer is generic, let's just let it be. 
        // Ideally we'd disable the seek bar UI for live streams, but that's a polish item.
    }

    startHeartbeat() {
        this.stopHeartbeat();
        this.heartbeatTimer = setInterval(() => {
            this.checkStreamHealth();
        }, HEARTBEAT_INTERVAL);
    }

    stopHeartbeat() {
        if (this.heartbeatTimer) {
            clearInterval(this.heartbeatTimer);
            this.heartbeatTimer = null;
        }
    }

    checkStreamHealth() {
        if (!this.isActive) return;

        const audio = this.player.audio;

        // If player is paused by user, don't reconnect
        if (audio.paused && !this.player.isPlaying) {
            return;
        }

        // Logic from template/0: check if currentTime is stuck
        const currentPos = audio.currentTime;
        const readyState = audio.readyState;

        // readyState < 2 means not enough data
        // or currentPos hasn't moved (and we expect it to be playing)
        if (readyState < 2 || (this.player.isPlaying && currentPos === this.lastPlayPos)) {
            console.warn("LiveStream: Detected stall/buffer, reconnecting...");
            this.performHealing();
        }

        this.lastPlayPos = currentPos;
    }

    performHealing() {
        this.retryCount++;
        const statusEl = document.getElementById('liveStatus');
        if (statusEl) statusEl.textContent = `Mengubungkan ulang... (${this.retryCount})`;

        // Reload source
        const timestamp = Date.now();
        const url = `${STREAM_URL}?t=${timestamp}`;

        // We want to keep playing, so we just swap src and play
        this.player.audio.src = url;
        this.player.audio.load();
        this.player.audio.play()
            .then(() => {
                this.player.isPlaying = true; // Update internal state if needed
                if (statusEl) statusEl.textContent = "Live OK";
            })
            .catch(e => console.error("Reconnect failed", e));
    }
}
