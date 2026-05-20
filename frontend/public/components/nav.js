// FEATURE-029: canonical top nav for all 7 pages.
// Single source of truth for tagline, POS emoji, link set, role gating,
// menu toggle, live clock, and user info population.

(function () {
  const TAGLINE = 'AI Powered Point of Sale System';

  const LINKS = [
    { key: 'pos',         href: 'index.html',       emoji: '🛒', label: 'POS',         roles: ['cashier', 'manager', 'admin'] },
    { key: 'inventory',   href: 'inventory.html',   emoji: '📦', label: 'Inventory',   roles: ['manager', 'admin'] },
    { key: 'ingredients', href: 'ingredients.html', emoji: '🧪', label: 'Ingredients', roles: ['manager', 'admin'] },
    { key: 'dashboard',   href: 'dashboard.html',   emoji: '📊', label: 'Dashboard',   roles: ['manager', 'admin'] },
    { key: 'xreport',     href: 'xreport.html',     emoji: '📈', label: 'X-Report',    roles: ['manager', 'admin'] },
    { key: 'zreport',     href: 'zreport.html',     emoji: '📉', label: 'Z-Report',    roles: ['manager', 'admin'] },
    { key: 'settings',    href: 'settings.html',    emoji: '⚙️', label: 'Settings',    roles: ['admin'] },
  ];

  function readTokenPayload() {
    try {
      const t = localStorage.getItem('access_token');
      if (!t) return null;
      return JSON.parse(atob(t.split('.')[1]));
    } catch (e) { return null; }
  }

  function resolveRole(explicit) {
    if (explicit) return explicit;
    if (typeof getUserRole === 'function') return getUserRole();
    const p = readTokenPayload();
    return p ? (p.role || null) : null;
  }

  function visibleLinks(role) {
    if (!role) return LINKS;
    return LINKS.filter(l => l.roles.includes(role));
  }

  function applyCachedBranding() {
    try {
      const cached = localStorage.getItem('biz_profile');
      if (!cached) return;
      const d = JSON.parse(cached);
      const n = document.getElementById('site-name');
      const t = document.getElementById('site-tagline');
      if (n && d.business_name) n.textContent = d.business_name;
      if (t && d.tagline) t.textContent = d.tagline;
    } catch (e) {}
  }

  function tickClock() {
    let prefs = {};
    try { prefs = JSON.parse(localStorage.getItem('clock_prefs') || '{}'); } catch (e) {}
    const use12 = prefs.format !== '24h';
    const tz = prefs.timezone || Intl.DateTimeFormat().resolvedOptions().timeZone;
    const el = document.getElementById('live-clock');
    if (el) {
      el.textContent = new Date().toLocaleTimeString('en-PH', {
        hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: use12, timeZone: tz
      });
    }
  }

  function renderNav(opts) {
    opts = opts || {};
    const mount = document.getElementById('nav-mount');
    if (!mount) return;

    const role = resolveRole(opts.role);
    const activePage = opts.activePage
      || (document.body && document.body.dataset && document.body.dataset.page)
      || null;
    const printSafe = opts.printSafe === true;

    const links = visibleLinks(role);
    const linksHTML = links.map((l, idx) => {
      const isActive = l.key === activePage;
      const base = isActive
        ? 'block px-4 py-3 text-blue-600 bg-blue-50 font-bold'
        : 'block px-4 py-3 text-gray-700 hover:bg-gray-100';
      const radius = idx === 0 ? ' rounded-t-lg' : '';
      return `<a href="${l.href}" class="${base}${radius}">${l.emoji} ${l.label}</a>`;
    }).join('');

    mount.innerHTML = `
      <nav class="nav-bar-gradient sticky top-0${printSafe ? ' no-print' : ''}" style="z-index: var(--z-sticky);">
        <div class="container mx-auto px-4 py-3 flex justify-between items-center">
          <div class="flex items-center space-x-3">
            <span class="text-3xl">🍽️</span>
            <div>
              <h1 class="text-xl font-bold text-white" id="site-name">TarsierPOS</h1>
              <p class="text-blue-200 text-sm" id="site-tagline">${TAGLINE}</p>
            </div>
          </div>
          <div class="flex items-center gap-4">
            <span id="live-clock" class="text-sm font-mono tabular-nums text-blue-100 hidden sm:block"></span>
            <div class="text-right hidden sm:block border-r border-blue-400 pr-4 mr-2">
              <p class="text-sm font-bold text-white leading-tight" id="userName"></p>
              <p class="text-[10px] text-blue-200 uppercase tracking-widest font-black leading-tight" id="userRole"></p>
            </div>
            <!-- FEATURE-036 / ISSUE-110 / FEATURE-037: shift status pill — own state +
                 tap to reveal cross-account open-shifts floor panel. -->
            <div class="relative">
              <span id="shift-indicator" role="button" tabindex="0" aria-haspopup="true" aria-expanded="false"
                class="inline-flex items-center font-semibold rounded-lg cursor-pointer select-none"
                style="min-height: 48px; padding: 0 var(--space-3); background: rgba(255,255,255,0.12); color: #fff; font-size: var(--text-sm);">
                <span id="shift-indicator-text">…</span>
              </span>
              <div id="shift-panel" class="hidden absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-xl" style="z-index: var(--z-dropdown);">
                <div id="shift-panel-body" class="text-sm text-gray-700"></div>
              </div>
            </div>
            <div class="relative">
              <button id="menu-button" type="button" aria-label="Menu" aria-expanded="false"
                class="bg-blue-700 hover:bg-blue-800 px-4 py-2 rounded-lg font-medium transition flex items-center space-x-2">
                <span>☰ Menu</span>
                <span id="menu-arrow">▼</span>
              </button>
              <div id="dropdown-menu" class="hidden absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-xl" style="z-index: var(--z-dropdown);">
                ${linksHTML}
                <a href="#" id="nav-open-shift" class="hidden block px-4 py-3 text-green-700 hover:bg-green-50 border-t border-gray-100">🔓 Open Shift</a>
                <a href="#" id="nav-close-shift" class="hidden block px-4 py-3 text-orange-700 hover:bg-orange-50 border-t border-gray-100">🔒 Close Shift</a>
                <a href="#" id="nav-logout" class="block px-4 py-3 text-red-600 hover:bg-red-50 rounded-b-lg">🚪 Logout</a>
              </div>
            </div>
          </div>
        </div>
      </nav>
    `;

    // Populate user info from JWT
    const payload = readTokenPayload();
    if (payload) {
      const uEl = document.getElementById('userName');
      const rEl = document.getElementById('userRole');
      if (uEl) uEl.textContent = payload.username || 'User';
      if (rEl) rEl.textContent = (payload.role || '').toUpperCase();
    }

    // Override default tagline/site-name with cached business profile if present
    applyCachedBranding();

    // Logout
    const logoutLink = document.getElementById('nav-logout');
    if (logoutLink) {
      logoutLink.addEventListener('click', (e) => {
        e.preventDefault();
        if (typeof logout === 'function') {
          logout();
        } else {
          localStorage.removeItem('access_token');
          localStorage.removeItem('refresh_token');
          window.location.replace('login.html');
        }
      });
    }

    // Menu toggle
    const btn = document.getElementById('menu-button');
    const dd = document.getElementById('dropdown-menu');
    const arrow = document.getElementById('menu-arrow');
    if (btn && dd) {
      btn.addEventListener('click', (e) => {
        e.stopPropagation();
        closeShiftPanel();   // FEATURE-037: only one overlay open at a time
        dd.classList.toggle('hidden');
        const open = !dd.classList.contains('hidden');
        if (arrow) arrow.textContent = open ? '▲' : '▼';
        btn.setAttribute('aria-expanded', open ? 'true' : 'false');
      });
      document.addEventListener('click', (e) => {
        if (!dd.contains(e.target) && !btn.contains(e.target)) {
          dd.classList.add('hidden');
          if (arrow) arrow.textContent = '▼';
          btn.setAttribute('aria-expanded', 'false');
        }
        const panel = document.getElementById('shift-panel');
        const ind = document.getElementById('shift-indicator');
        if (panel && ind && !panel.contains(e.target) && !ind.contains(e.target)) {
          closeShiftPanel();
        }
      });
    }

    // Live clock
    tickClock();
    if (window.__navClockInterval) clearInterval(window.__navClockInterval);
    window.__navClockInterval = setInterval(tickClock, 1000);

    // FEATURE-036: shift indicator + dropdown Close Shift wiring
    wireShiftSurface();
    fetchAndRenderShift();
  }

  // --- FEATURE-036: shift surface (header indicator + dropdown item) ---

  function _fmtTime(iso) {
    try {
      return new Date(iso).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }

  function renderShiftIndicator(shift) {
    const ind = document.getElementById('shift-indicator');
    const txt = document.getElementById('shift-indicator-text');
    const openItem = document.getElementById('nav-open-shift');
    const closeItem = document.getElementById('nav-close-shift');
    if (!ind || !txt) return;
    if (shift && shift.id) {
      txt.textContent = `🟢 Shift #${shift.id} · ${_fmtTime(shift.opened_at)}`;
      ind.style.background = 'rgba(34,197,94,0.25)';
      ind.dataset.state = 'open';
      if (openItem) openItem.classList.add('hidden');
      if (closeItem) closeItem.classList.remove('hidden');
    } else {
      txt.textContent = '⊘ No active shift';
      ind.style.background = 'rgba(255,255,255,0.12)';
      ind.dataset.state = 'closed';
      if (openItem) openItem.classList.remove('hidden');
      if (closeItem) closeItem.classList.add('hidden');
      // FEATURE-037: surface floor state on the inactive pill without tapping.
      refreshFloorBadge();
    }
  }

  // --- FEATURE-037: cross-account open-shifts floor view ---

  const _ROLE_ICON = { admin: '👑', manager: '👔', cashier: '🧑‍🍳' };
  function _roleIcon(role) { return _ROLE_ICON[role] || '👤'; }

  function _esc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
    }[c]));
  }

  async function fetchActiveShifts() {
    if (typeof authenticatedFetch !== 'function' || typeof API_BASE === 'undefined') return [];
    try {
      const res = await authenticatedFetch(`${API_BASE}/shifts/active/`);
      if (!res.ok) return [];
      const data = await res.json().catch(() => []);
      return Array.isArray(data) ? data : [];
    } catch (e) { return []; }
  }

  async function refreshFloorBadge() {
    const ind = document.getElementById('shift-indicator');
    const txt = document.getElementById('shift-indicator-text');
    if (!ind || !txt || ind.dataset.state !== 'closed') return;
    const list = await fetchActiveShifts();
    if (ind.dataset.state !== 'closed') return;   // own shift opened mid-flight
    txt.textContent = list.length
      ? `⊘ No active shift · ${list.length} open`
      : '⊘ No active shift';
  }

  function closeShiftPanel() {
    const panel = document.getElementById('shift-panel');
    const ind = document.getElementById('shift-indicator');
    if (panel) panel.classList.add('hidden');
    if (ind) ind.setAttribute('aria-expanded', 'false');
  }

  async function openShiftPanel() {
    const panel = document.getElementById('shift-panel');
    const body = document.getElementById('shift-panel-body');
    const ind = document.getElementById('shift-indicator');
    if (!panel || !body) return;
    const dd = document.getElementById('dropdown-menu');   // only one overlay
    if (dd) dd.classList.add('hidden');
    body.innerHTML = '<div class="px-4 py-3 text-gray-400">Loading…</div>';
    panel.classList.remove('hidden');
    if (ind) ind.setAttribute('aria-expanded', 'true');
    const list = await fetchActiveShifts();
    if (panel.classList.contains('hidden')) return;
    if (!list.length) {
      body.innerHTML = '<div class="px-4 py-3 text-gray-500">No open shifts</div>';
      return;
    }
    body.innerHTML = list.map((s, idx) => {
      const radius = idx === 0 ? ' rounded-t-lg' : '';
      const last = idx === list.length - 1 ? ' rounded-b-lg' : ' border-b border-gray-100';
      return `<div class="px-4 py-3${radius}${last}">${_roleIcon(s.cashier_role)} ${_esc(s.cashier_username)} · Shift #${s.shift_number} · opened ${_fmtTime(s.opened_at)}</div>`;
    }).join('');
  }

  function toggleShiftPanel() {
    const panel = document.getElementById('shift-panel');
    if (!panel) return;
    if (panel.classList.contains('hidden')) openShiftPanel();
    else closeShiftPanel();
  }

  function wireShiftSurface() {
    // FEATURE-037: tap the pill to reveal the open-shifts floor panel
    // (NOT the open-shift modal — opening a shift lives in the dropdown).
    const ind = document.getElementById('shift-indicator');
    if (ind) {
      ind.addEventListener('click', (e) => { e.stopPropagation(); toggleShiftPanel(); });
      ind.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); toggleShiftPanel(); }
      });
    }
    const openItem = document.getElementById('nav-open-shift');
    if (openItem) {
      openItem.addEventListener('click', (e) => {
        e.preventDefault();
        // On the POS page the modal is present — open it directly without a
        // reload. Elsewhere, route to index.html which auto-opens it.
        if (typeof window.showOpenShiftModal === 'function' && document.getElementById('shift-modal')) {
          window.showOpenShiftModal();
        } else {
          window.location.href = 'index.html?open=1';
        }
      });
    }
    const closeItem = document.getElementById('nav-close-shift');
    if (closeItem) {
      closeItem.addEventListener('click', (e) => {
        e.preventDefault();
        window.location.href = 'zreport.html?close=1';
      });
    }
  }

  async function fetchAndRenderShift() {
    // If app.js already fetched and stashed the shift, use it.
    if (window.currentShift !== undefined && window.currentShift !== null) {
      renderShiftIndicator(window.currentShift);
      return;
    }
    try {
      if (typeof authenticatedFetch !== 'function' || typeof API_BASE === 'undefined') {
        renderShiftIndicator(null);
        return;
      }
      const res = await authenticatedFetch(`${API_BASE}/shifts/current/`);
      if (res.status === 204) {
        window.currentShift = null;
        renderShiftIndicator(null);
        return;
      }
      if (res.ok) {
        const shift = await res.json().catch(() => null);
        const s = (shift && shift.id) ? shift : null;
        window.currentShift = s;
        renderShiftIndicator(s);
      } else {
        renderShiftIndicator(null);
      }
    } catch (e) {
      renderShiftIndicator(null);
    }
  }

  window.renderShiftIndicator = renderShiftIndicator;

  window.renderNav = renderNav;
})();
