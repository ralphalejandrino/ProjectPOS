"""B-UI-CLEANUP (ISSUE-116 / ISSUE-117) — nav cleanup + insights relocation.

The insights widgets moved onto the dashboard (ISSUE-116) and the Insights /
Remote View entries left the dropdown nav. Widget visibility on the dashboard
is client-gated by canAccessPage('insights'); the authoritative rule stays the
existing /reports/insights/ permission (IsManagerOrAbove) — asserted here for
both the permitted and non-permitted user. Cashier 403s are also covered in
test_cashier_access.py.

Static assets are not served by Django, so the nav/page assertions read the
frontend sources directly.
"""

from pathlib import Path

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User

FRONTEND = Path(__file__).resolve().parents[2] / 'frontend' / 'public'


class InsightsRelocationTests(APITestCase):

    def test_insights_data_available_to_permitted_user(self):
        """Manager (has 'insights' in effective pages) gets widget data."""
        manager = User.objects.create_user(
            username='mgr-ins', password='x', role='manager'
        )
        self.client.force_authenticate(manager)
        resp = self.client.get('/api/canteen/reports/insights/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        data = resp.json()
        self.assertIn('peak_hours', data)
        self.assertIn('cashiers', data)

    def test_insights_data_denied_to_non_permitted_user(self):
        """Cashier (no 'insights' page) is denied the widget data source."""
        cashier = User.objects.create_user(
            username='csh-ins', password='x', role='cashier'
        )
        self.client.force_authenticate(cashier)
        resp = self.client.get('/api/canteen/reports/insights/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)


class NavCleanupTests(APITestCase):

    def test_insights_page_removed(self):
        """insights.html is deleted and no longer precached or linked."""
        self.assertFalse((FRONTEND / 'insights.html').exists())
        # Quoted form = the ASSETS precache entry (the SW changelog comment
        # legitimately mentions the file's removal).
        self.assertNotIn("'insights.html'", (FRONTEND / 'sw.js').read_text())
        nav = (FRONTEND / 'components' / 'nav.js').read_text()
        self.assertNotIn("href: 'insights.html'", nav)

    def test_remote_view_removed_from_nav_but_page_kept(self):
        """ISSUE-117: dropdown loses Remote View; remote.html itself stays
        (owner direct-bookmarks it until the dedicated PWA replaces it)."""
        nav = (FRONTEND / 'components' / 'nav.js').read_text()
        self.assertNotIn("href: 'remote.html'", nav)
        self.assertTrue((FRONTEND / 'remote.html').exists())

    def test_dashboard_hosts_relocated_widgets(self):
        """Dashboard contains both relocated widgets, gated on 'insights'."""
        dash = (FRONTEND / 'dashboard.html').read_text()
        self.assertIn('id="peak-hours"', dash)
        self.assertIn('id="cashier-body"', dash)
        self.assertIn("canAccessPage('insights')", dash)


class DashboardInsightsInitTests(APITestCase):
    """ISSUE-120 — insights widgets must populate on every page load.

    The init regression: initInsights() sat at the end of the awaited
    DOMContentLoaded chain, so a stalled upstream fetch starved the widgets,
    and bfcache restores (where DOMContentLoaded does not refire) showed the
    page in whatever state it was navigated away in. Static assets are not
    served by Django, so these assert on the frontend source directly.
    """

    def test_insights_init_precedes_awaited_chain(self):
        """initInsights() runs before the first await of the init handler."""
        dash = (FRONTEND / 'dashboard.html').read_text()
        init_pos = dash.index('initInsights();')
        chain_pos = dash.index('await loadDashboardData(')
        self.assertLess(
            init_pos, chain_pos,
            'initInsights() must be called before the awaited dashboard '
            'data chain so a stalled fetch cannot starve the widgets.',
        )

    def test_insights_repopulate_on_bfcache_restore(self):
        """pageshow + e.persisted rewires the widgets after bfcache restore."""
        dash = (FRONTEND / 'dashboard.html').read_text()
        self.assertIn("window.addEventListener('pageshow'", dash)
        self.assertIn('if (e.persisted) initInsights();', dash)

    def test_insights_gate_kept(self):
        """The FEATURE-044 canAccessPage('insights') gate is intact."""
        dash = (FRONTEND / 'dashboard.html').read_text()
        self.assertIn("canAccessPage('insights')", dash)
