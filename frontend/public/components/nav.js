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
      <nav class="nav-bar-gradient sticky top-0 z-50${printSafe ? ' no-print' : ''}">
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
            <div class="relative">
              <button id="menu-button" type="button" aria-label="Menu" aria-expanded="false"
                class="bg-blue-700 hover:bg-blue-800 px-4 py-2 rounded-lg font-medium transition flex items-center space-x-2">
                <span>☰ Menu</span>
                <span id="menu-arrow">▼</span>
              </button>
              <div id="dropdown-menu" class="hidden absolute right-0 mt-2 w-48 bg-white rounded-lg shadow-xl z-50">
                ${linksHTML}
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
      });
    }

    // Live clock
    tickClock();
    if (window.__navClockInterval) clearInterval(window.__navClockInterval);
    window.__navClockInterval = setInterval(tickClock, 1000);
  }

  window.renderNav = renderNav;
})();
