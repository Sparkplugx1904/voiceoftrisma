/**
 * API Service for Voice of Trisma
 */
const API = {
    STATS_URL: 'https://voiceoftrisma-stream-stats.anandapradnyana68.workers.dev?t=',
    JADWAL_URL: (typeof base !== 'undefined' ? base : './') + 'jadwal.json',

    /**
     * Fetch radio stream status and listener count
     */
    async getRadioStatus() {
        const controller = new AbortController();
        const timeoutId = setTimeout(() => controller.abort(), 5000);
        try {
            const response = await fetch(this.STATS_URL + Date.now(), { signal: controller.signal });
            clearTimeout(timeoutId);
            if (!response.ok) throw new Error('HTTP ' + response.status);
            return await response.json();
        } catch (error) {
            console.error("API Error (Stats):", error);
            return null;
        }
    },

    /**
     * Fetch broadcast schedule
     */
    async getSchedule() {
        try {
            const response = await fetch(this.JADWAL_URL);
            return await response.json();
        } catch (error) {
            console.error("API Error (Schedule):", error);
            return null;
        }
    }
};
