/**
 * UI Utilities for Voice of Trisma
 */
const UI = {
    /**
     * Format numbers to K/M suffixes
     */
    formatNumber(n) {
        if (n >= 1000000) return (n / 1000000).toFixed(1).replace(/\.0$/, '') + 'm';
        if (n >= 1000) return (n / 1000).toFixed(1).replace(/\.0$/, '') + 'k';
        return n.toString();
    },

    /**
     * Get Bali (WITA) time
     */
    getWaktuBali() {
        const now = new Date();
        const baliDateString = now.toLocaleString("en-US", { timeZone: "Asia/Makassar" });
        const baliDate = new Date(baliDateString);
        const day = baliDate.getDay();
        const hours = String(baliDate.getHours()).padStart(2, '0');
        const minutes = String(baliDate.getMinutes()).padStart(2, '0');
        return { day, time: `${hours}:${minutes}` };
    }
};

// Global exports if needed
window.toggleSidebar = window.toggleSidebar || function() {
    document.body.classList.toggle('sidebar-toggled');
};
