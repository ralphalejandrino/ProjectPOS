// Shared API configuration - included before all other scripts
// FLAG-068: API and frontend are served from the same nginx origin, so the
// base URL is empty and every endpoint is a relative path. This works on any
// hostname (localhost, LAN IP, Tailscale host) with no rebuild and no env
// injection — the browser resolves the path against the current origin.
const API_BASE = '/api/canteen';
const PAYMENTS_API = '/api/payments';
const AUTH_API = '/api/auth';
const API_URL = API_BASE;

// XSS defense — escape user/API string data before innerHTML injection
const escapeHtml = (str) => {
    if (str == null) return '';
    return String(str)
        .replace(/&/g, '&amp;')
        .replace(/</g, '&lt;')
        .replace(/>/g, '&gt;')
        .replace(/"/g, '&quot;')
        .replace(/'/g, '&#039;');
};

// Token Helpers
const getToken = () => localStorage.getItem('access_token');
const setToken = (access, refresh) => {
    localStorage.setItem('access_token', access);
    if (refresh) localStorage.setItem('refresh_token', refresh);
};
const clearTokens = async () => {
    const refresh = localStorage.getItem('refresh_token');
    if (refresh) {
        try {
            await fetch(`${AUTH_API}/logout/`, {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Authorization': `Bearer ${localStorage.getItem('access_token')}`
                },
                body: JSON.stringify({ refresh })
            });
        } catch (_) {}
    }
    localStorage.removeItem('access_token');
    localStorage.removeItem('refresh_token');
};

// Canonical logout — AWAIT token blacklist before navigating away (no race),
// and use location.replace so the back button cannot return to a protected page.
async function logout() {
    await clearTokens();
    window.location.replace('login.html');
}

function getUserRole() {
    const token = getToken();
    if (!token) return null;
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        return payload.role || null;
    } catch (e) {
        return null;
    }
}

// ============================================================
// FEATURE-044 — per-user page access (client side)
// Authoritative enforcement is server-side (canteen/access.py +
// HasPageAccess). These helpers drive nav visibility + page guards
// off the JWT 'pages' claim, falling back to the role default for
// tokens issued before this feature (so behaviour is unchanged
// until the user next logs in / refreshes). Keep this map in sync
// with canteen/access.py.
// ============================================================
const ROLE_DEFAULT_PAGES = {
    admin:   ['pos', 'inventory', 'ingredients', 'dashboard', 'xreport', 'zreport', 'settings', 'status', 'period', 'insights', 'remote'],
    manager: ['pos', 'inventory', 'ingredients', 'dashboard', 'xreport', 'zreport', 'status', 'period', 'insights'],
    cashier: ['pos'],
};

function getAllowedPages() {
    const token = getToken();
    if (!token) return [];
    try {
        const payload = JSON.parse(atob(token.split('.')[1]));
        if (Array.isArray(payload.pages)) return payload.pages;
        return ROLE_DEFAULT_PAGES[payload.role] || [];
    } catch (e) {
        return [];
    }
}

function canAccessPage(key) {
    return getAllowedPages().includes(key);
}

// Guard a page route. Returns true when allowed; otherwise redirects
// (login if unauthenticated, denied otherwise) and returns false.
function guardPage(key) {
    if (!getToken()) { window.location.replace('login.html'); return false; }
    if (!canAccessPage(key)) {
        window.location.replace('denied.html?from=' + encodeURIComponent(location.pathname));
        return false;
    }
    return true;
}

// Authenticated Fetch Wrapper
async function authenticatedFetch(url, options = {}) {
    const token = getToken();
    
    const headers = {
        ...options.headers
    };

    // Only set Content-Type if not FormData (browser sets it automatically for multipart)
    if (!(options.body instanceof FormData)) {
        headers['Content-Type'] = 'application/json';
    }

    if (token) {
        headers['Authorization'] = `Bearer ${token}`;
    }

    const response = await fetch(url, { ...options, headers });

    // Handle session expiry — attempt token refresh before redirecting
    if (response.status === 401) {
        const refreshToken = localStorage.getItem('refresh_token');
        if (refreshToken) {
            try {
                const refreshResponse = await fetch(`${AUTH_API}/token/refresh/`, {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ refresh: refreshToken })
                });
                if (refreshResponse.ok) {
                    const refreshData = await refreshResponse.json();
                    setToken(refreshData.access, refreshData.refresh || null);
                    // Retry original request directly with fetch() — NOT authenticatedFetch() to prevent infinite loop
                    const retryHeaders = { ...(options.headers || {}) };
                    retryHeaders['Authorization'] = `Bearer ${refreshData.access}`;
                    if (!(options.body instanceof FormData)) {
                        retryHeaders['Content-Type'] = retryHeaders['Content-Type'] || 'application/json';
                    }
                    return await fetch(url, { ...options, headers: retryHeaders });
                }
            } catch (e) {
                console.error('Token refresh failed:', e);
            }
        }
        console.warn('Session expired. Redirecting to login...');
        clearTokens();
        if (!window.location.pathname.includes('login.html')) {
            window.location.href = 'login.html';
        }
    }

    return response;
}

function applyColorSchemeSync(hex) {
    if (!hex) return;
    document.documentElement.style.setProperty('--primary-color', hex);
    document.documentElement.style.setProperty('--primary-hover', hex);
    // Compute a slightly darker shade for hover states
    const darken = (h) => {
        const n = parseInt(h.slice(1), 16);
        const r = Math.max(0, (n >> 16) - 30);
        const g = Math.max(0, ((n >> 8) & 0xff) - 30);
        const b = Math.max(0, (n & 0xff) - 30);
        return '#' + [r,g,b].map(x => x.toString(16).padStart(2,'0')).join('');
    };
    const dark = darken(hex);
    const light = hex + '18'; // ~10% opacity for bg-blue-50/bg-blue-100
    let styleEl = document.getElementById('biz-color-scheme');
    if (!styleEl) {
        styleEl = document.createElement('style');
        styleEl.id = 'biz-color-scheme';
        document.head.appendChild(styleEl);
    }
    styleEl.textContent = `
        /* Nav gradient */
        nav { background: linear-gradient(to right, ${hex}, ${dark}) !important; }
        /* Primary buttons */
        .bg-blue-600 { background-color: ${hex} !important; }
        .bg-blue-700 { background-color: ${dark} !important; }
        .bg-blue-800 { background-color: ${dark} !important; }
        .hover\\:bg-blue-700:hover { background-color: ${dark} !important; }
        .hover\\:bg-blue-800:hover { background-color: ${dark} !important; }
        /* Gradient nav */
        .from-blue-600 { --tw-gradient-from: ${hex} !important; }
        .to-blue-800 { --tw-gradient-to: ${dark} !important; }
        /* Text accents */
        .text-blue-600 { color: ${hex} !important; }
        .text-blue-800 { color: ${dark} !important; }
        /* Borders */
        .border-blue-500 { border-color: ${hex} !important; }
        .border-blue-400 { border-color: ${hex} !important; }
        .border-l-4.border-blue-500 { border-left-color: ${hex} !important; }
        /* Active nav link */
        .bg-blue-50 { background-color: ${light} !important; }
        .text-blue-600 { color: ${hex} !important; }
        /* Light badge bg (cart count, GCash badge — intentionally lighter) */
        .bg-blue-100 { background-color: ${light} !important; }
        /* Focus rings */
        .focus\\:ring-blue-500:focus { --tw-ring-color: ${hex} !important; }
        .focus\\:border-blue-500:focus { border-color: ${hex} !important; }
        /* Progress bar, spinner, detail modal header */
        .bg-blue-600.h-2 { background-color: ${hex} !important; }
        .border-b-4.border-blue-600 { border-bottom-color: ${hex} !important; }
        .border-b-2.border-blue-600 { border-bottom-color: ${hex} !important; }
        /* Toggle switch checked state */
        .peer-checked\\:bg-blue-600:has(+ *) { background-color: ${hex} !important; }
        /* Category active button (applied inline via JS — cover both) */
        button.bg-blue-600 { background-color: ${hex} !important; }
    `;
}
function applyColorScheme(hex) { applyColorSchemeSync(hex); }

function applyLogoToHeader(logoUrl) {
    if (!logoUrl) return;
    const nameEl = document.getElementById('site-name');
    if (!nameEl) return;
    // Hide the default emoji span (sibling of nameEl's parent div)
    const wrapper = nameEl.parentNode; // the <div> containing h1 + p
    const container = wrapper.parentNode; // the flex div containing emoji + wrapper
    const emojiSpan = container.querySelector('span');
    if (emojiSpan) emojiSpan.style.display = 'none';
    // Insert or update logo img
    let logoEl = document.getElementById('site-logo');
    if (!logoEl) {
        logoEl = document.createElement('img');
        logoEl.id = 'site-logo';
        logoEl.className = 'w-10 h-10 object-contain rounded mr-1';
        container.insertBefore(logoEl, wrapper);
    }
    logoEl.src = logoUrl;
}

// Sync apply cached branding before any async fetch
(function applyCachedBranding() {
    const cached = localStorage.getItem('biz_profile');
    if (!cached) return;
    try {
        const d = JSON.parse(cached);
        // First pass — may be overridden by Tailwind CDN loading after us
        const nameEl = document.getElementById('site-name');
        const tagEl = document.getElementById('site-tagline');
        if (nameEl && d.business_name) nameEl.textContent = d.business_name;
        if (tagEl && d.tagline) tagEl.textContent = d.tagline;
        if (d.color_scheme) applyColorSchemeSync(d.color_scheme);
        if (d.logo) applyLogoToHeader(d.logo);
        // Second pass — after DOM + Tailwind are fully loaded, re-inject to win
        document.addEventListener('DOMContentLoaded', () => {
            if (d.business_name) {
                const n = document.getElementById('site-name');
                const t = document.getElementById('site-tagline');
                if (n) n.textContent = d.business_name;
                if (t) t.textContent = d.tagline;
            }
            if (d.color_scheme) applyColorSchemeSync(d.color_scheme);
            if (d.logo) applyLogoToHeader(d.logo);
        });
    } catch(e) {}
})();

// FEATURE-030: PWA UX — Service Worker registration + install prompt, offline
// banner, and update notification. Banners use only CSS classes already
// present in styles.css / shared-styles.css. All banner text is static (no
// user data), so innerHTML/XSS is not a concern; built via DOM APIs anyway.
(function pwaUX() {
    const isStandalone = () =>
        window.matchMedia('(display-mode: standalone)').matches ||
        window.navigator.standalone === true;

    function bannerHost() {
        let host = document.getElementById('pwa-banners');
        if (!host) {
            host = document.createElement('div');
            host.id = 'pwa-banners';
            host.className = 'fixed bottom-0 left-0 right-0 z-50 flex flex-col';
            document.body.appendChild(host);
        }
        return host;
    }

    function removeBanner(id) {
        const el = document.getElementById(id);
        if (el) el.remove();
    }

    // actions: [{ text, btnClass, onClick }]
    function showBanner(id, bgClass, label, actions) {
        removeBanner(id);
        const bar = document.createElement('div');
        bar.id = id;
        bar.className = `${bgClass} text-white p-3 flex items-center justify-between gap-4 shadow-lg`;
        const msg = document.createElement('span');
        msg.className = 'text-sm font-medium';
        msg.textContent = label;
        const btns = document.createElement('div');
        btns.className = 'flex items-center gap-2';
        (actions || []).forEach(a => {
            const b = document.createElement('button');
            b.className = a.btnClass;
            b.textContent = a.text;
            b.addEventListener('click', a.onClick);
            btns.appendChild(b);
        });
        bar.appendChild(msg);
        bar.appendChild(btns);
        bannerHost().appendChild(bar);
    }

    // 1) Install prompt — only when not already installed.
    let deferredPrompt = null;
    window.addEventListener('beforeinstallprompt', (e) => {
        e.preventDefault();
        deferredPrompt = e;
        if (isStandalone() || sessionStorage.getItem('pwa_install_dismissed')) return;
        showBanner('pwa-install', 'bg-blue-600', 'Add TarsierPOS to home screen', [
            { text: 'Install',
              btnClass: 'bg-white text-blue-700 px-4 py-2 rounded font-medium text-sm cursor-pointer',
              onClick: async () => {
                  removeBanner('pwa-install');
                  if (!deferredPrompt) return;
                  deferredPrompt.prompt();
                  try { await deferredPrompt.userChoice; } catch (_) {}
                  deferredPrompt = null;
              } },
            { text: 'Dismiss',
              btnClass: 'text-white px-4 py-2 rounded text-sm cursor-pointer',
              onClick: () => {
                  sessionStorage.setItem('pwa_install_dismissed', '1');
                  removeBanner('pwa-install');
              } },
        ]);
    });
    window.addEventListener('appinstalled', () => {
        removeBanner('pwa-install');
        deferredPrompt = null;
    });

    // 2) Offline detection — persistent banner while offline, cleared on reconnect.
    function syncNetworkBanner() {
        if (navigator.onLine) {
            removeBanner('pwa-offline');
        } else {
            showBanner('pwa-offline', 'bg-gray-800',
                'No network — POS running on local data', []);
        }
    }
    window.addEventListener('online', syncNetworkBanner);
    window.addEventListener('offline', syncNetworkBanner);
    if (!navigator.onLine) syncNetworkBanner();

    // 3) Service Worker registration + update notification.
    if ('serviceWorker' in navigator) {
        // A new SW that called skipWaiting takes control → controllerchange.
        // Guard the first install (no prior controller) so we only prompt on
        // a genuine update.
        let hadController = !!navigator.serviceWorker.controller;
        navigator.serviceWorker.addEventListener('controllerchange', () => {
            if (!hadController) { hadController = true; return; }
            showBanner('pwa-update', 'bg-blue-700',
                'Update available — reload to apply', [
                { text: 'Reload',
                  btnClass: 'bg-white text-blue-700 px-4 py-2 rounded font-medium text-sm cursor-pointer',
                  onClick: () => window.location.reload() },
            ]);
        });
        window.addEventListener('load', () => {
            navigator.serviceWorker.register('sw.js')
                .then(() => {})
                .catch(err => console.error('Service Worker registration failed', err));
        });
    }
})();

async function loadSiteName() {
    try {
        const response = await authenticatedFetch(`${API_BASE}/business/`);
        const data = await response.json();
        const nameEl = document.getElementById('site-name');
        const tagEl = document.getElementById('site-tagline');
        if (nameEl && data.business_name) nameEl.textContent = data.business_name;
        if (tagEl && data.tagline) tagEl.textContent = data.tagline;
        if (data.color_scheme) applyColorSchemeSync(data.color_scheme);
        if (data.logo) applyLogoToHeader(data.logo);
        // Cache for sync flash prevention
        localStorage.setItem('biz_profile', JSON.stringify({
            business_name: data.business_name,
            tagline: data.tagline,
            color_scheme: data.color_scheme,
            logo: data.logo || null,
            vat_enabled: !!data.vat_enabled,
            vat_rate: (data.vat_rate !== undefined && data.vat_rate !== null && !isNaN(parseFloat(data.vat_rate))) ? parseFloat(data.vat_rate) : 12,
            sc_discount_enabled: data.sc_discount_enabled !== false,
            sc_discount_rate: data.sc_discount_rate || 20,
            pwd_discount_enabled: data.pwd_discount_enabled !== false,
            pwd_discount_rate: data.pwd_discount_rate || 20,
            promo_discount_enabled: !!data.promo_discount_enabled,
            track_inventory: data.track_inventory !== false,
            currency: data.currency || 'PHP',
            vat_inclusive: !!data.vat_inclusive,
            receipt_header: data.receipt_header || ''
        }));
        window.__currency = (data.currency || 'PHP').toUpperCase();
    } catch (e) {}
}

async function loadColorScheme() { await loadSiteName(); }

// ============================================================
// Shared timestamp formatting — PHT + clock_prefs-aware
// ============================================================

/**
 * formatTS(isoStr, extraOpts)
 * Returns a TIME string in Asia/Manila respecting clock_prefs.format.
 */
function formatTS(isoStr, extraOpts = {}) {
    if (!isoStr) return 'N/A';
    const prefs = (() => {
        try { return JSON.parse(localStorage.getItem('clock_prefs') || '{}'); }
        catch (e) { return {}; }
    })();
    const use12 = prefs.format !== '24h';
    const tz = prefs.timezone || 'Asia/Manila';
    const defaults = {
        hour: '2-digit',
        minute: '2-digit',
        hour12: use12,
        timeZone: tz,
    };
    return new Date(isoStr).toLocaleTimeString('en-PH', { ...defaults, ...extraOpts });
}

/**
 * formatDT(isoStr)
 * Returns a DATE + TIME string in Asia/Manila respecting clock_prefs.format.
 */
function formatDT(isoStr) {
    if (!isoStr) return 'N/A';
    const prefs = (() => {
        try { return JSON.parse(localStorage.getItem('clock_prefs') || '{}'); }
        catch (e) { return {}; }
    })();
    const use12 = prefs.format !== '24h';
    const tz = prefs.timezone || 'Asia/Manila';
    return new Date(isoStr).toLocaleString('en-PH', {
        dateStyle: 'medium',
        timeStyle: 'short',
        hour12: use12,
        timeZone: tz,
    });
}
