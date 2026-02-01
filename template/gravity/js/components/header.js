/* js/components/header.js */

export function initHeader() {
    // Theme Toggle
    const themeBtn = document.getElementById('themeToggle');
    const drawerThemeBtn = document.getElementById('themeToggleFromDrawer');
    const themeIcon = document.getElementById('themeIcon');
    const drawerThemeIcon = document.getElementById('drawerThemeIcon');

    // Check saved preference
    const savedTheme = localStorage.getItem('vot-theme');
    const systemDark = window.matchMedia('(prefers-color-scheme: dark)').matches;

    if (savedTheme === 'dark' || (!savedTheme && systemDark)) {
        document.body.classList.add('dark-mode');
        updateIcons(true);
    }

    function toggleTheme() {
        const isDark = document.body.classList.toggle('dark-mode');
        localStorage.setItem('vot-theme', isDark ? 'dark' : 'light');
        updateIcons(isDark);
    }

    function updateIcons(isDark) {
        const iconName = isDark ? 'light_mode' : 'dark_mode'; // Icon to show "switch to..."
        themeIcon.textContent = iconName;
        drawerThemeIcon.textContent = iconName;
    }

    themeBtn?.addEventListener('click', toggleTheme);
    drawerThemeBtn?.addEventListener('click', toggleTheme);

    // Sidebar / Drawer
    const menuBtn = document.getElementById('menuBtn');
    const navDrawer = document.getElementById('navDrawer');
    const scrim = document.getElementById('scrim');

    function openDrawer() {
        navDrawer.classList.add('open');
        scrim.classList.add('visible');
    }

    function closeDrawer() {
        navDrawer.classList.remove('open');
        scrim.classList.remove('visible');
    }

    menuBtn?.addEventListener('click', openDrawer);
    scrim?.addEventListener('click', closeDrawer);

    // Close drawer on nav item click (mobile mostly)
    document.querySelectorAll('.nav-item').forEach(link => {
        link.addEventListener('click', () => {
            if (window.innerWidth <= 900) {
                closeDrawer();
            }
        });
    });

    // Clock
    function updateClock() {
        const now = new Date();
        // Force WITA (UTC+8)
        const utc = now.getTime() + (now.getTimezoneOffset() * 60000);
        const wita = new Date(utc + (3600000 * 8));

        const h = String(wita.getHours()).padStart(2, '0');
        const m = String(wita.getMinutes()).padStart(2, '0');

        const el = document.getElementById('clockDisplay');
        if (el) el.textContent = `${h}:${m} WITA`;
    }
    setInterval(updateClock, 1000);
    updateClock();
}
