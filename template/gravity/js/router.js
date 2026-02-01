/* js/router.js */

const routes = {
    'home': { view: 'home', title: 'Beranda' },
    'repo': { view: 'repo', title: 'Repository Audio' },
    '404': { view: '404', title: 'Halaman Tidak Ditemukan' }
};

export class Router {
    constructor(routeCallback) {
        this.callback = routeCallback;
        window.addEventListener('hashchange', () => this.handleRoute());
        window.addEventListener('load', () => this.handleRoute());
    }

    handleRoute() {
        // Get hash, remove #/
        let path = window.location.hash.slice(1) || '/';
        if (path.startsWith('/')) path = path.slice(1);
        if (path === '') path = 'home';

        // Support sub-routes like repo/2023/08 (split by /)
        const parts = path.split('/');
        const mainRoute = parts[0];

        // Pass params (rest of parts) to the view
        const params = parts.slice(1);

        const routeConfig = routes[mainRoute] || routes['404'];

        // Update Title
        document.title = `${routeConfig.title} - Voice of Trisma`;

        // Update Active Nav
        document.querySelectorAll('.nav-item').forEach(el => {
            el.classList.remove('active');
            if (el.dataset.view === mainRoute) {
                el.classList.add('active');
            }
        });

        // Callback to render
        this.callback(mainRoute, params, routeConfig);
    }

    navigate(path) {
        window.location.hash = `/${path}`;
    }
}
