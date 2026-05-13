// Shared API configuration - included before all other scripts
const isNginx = window.location.port === '' || window.location.port === '443';
const _base = `${window.location.protocol}//${window.location.hostname}`;
const API_BASE = isNginx ? `${_base}/api/canteen` : `${_base}:9000/api/canteen`;
const PAYMENTS_API = isNginx ? `${_base}/api/payments` : `${_base}:9000/api/payments`;
const AUTH_API = isNginx ? `${_base}/api/auth` : `${_base}:9000/api/auth`;
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

// Register Service Worker for PWA
if ('serviceWorker' in navigator) {
    window.addEventListener('load', () => {
        navigator.serviceWorker.register('sw.js')
            .then(() => {})
            .catch(err => console.error('Service Worker registration failed', err));
    });
}

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
            vat_rate: data.vat_rate || 12,
            sc_discount_enabled: data.sc_discount_enabled !== false,
            sc_discount_rate: data.sc_discount_rate || 20,
            pwd_discount_enabled: data.pwd_discount_enabled !== false,
            pwd_discount_rate: data.pwd_discount_rate || 20,
            promo_discount_enabled: !!data.promo_discount_enabled,
            track_inventory: data.track_inventory !== false
        }));
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
