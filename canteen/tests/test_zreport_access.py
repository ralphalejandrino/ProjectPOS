"""ISSUE-108 — zreport.html role-gate behavior.

The role guard in frontend/public/zreport.html is client-side JS.
These tests pin the JS gating logic so the cashier close-shift exception
(?close=1 only) cannot regress, and manager/admin full access stays intact.
"""

from pathlib import Path

from django.test import SimpleTestCase


ZREPORT_HTML = (
    Path(__file__).resolve().parent.parent.parent
    / 'frontend' / 'public' / 'zreport.html'
)


class ZReportAccessGateTests(SimpleTestCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.src = ZREPORT_HTML.read_text(encoding='utf-8')

    # --- Early FLAG-028 guard ---

    def test_cashier_can_load_zreport_with_close_param(self):
        # The early guard reads ?close=1 and lets cashier through.
        self.assertIn("get('close') === '1'", self.src)
        self.assertIn("role === 'cashier' && !closeOnly", self.src)

    def test_cashier_blocked_from_zreport_without_close_param(self):
        # Without closeOnly, cashier hits denied.html in the early guard.
        idx = self.src.index("role === 'cashier' && !closeOnly")
        tail = self.src[idx:idx + 400]
        self.assertIn("denied.html", tail)

    def test_cashier_blocked_from_zreport_with_z_param(self):
        # ?z=N has no special-case for cashier — only ?close=1 unlocks.
        # The DOMContentLoaded handler routes cashier only when closeOnly
        # is true, then returns early (never reaches loadDetail for ?z=).
        self.assertIn("role === 'cashier' && closeOnly", self.src)
        guarded = self.src.split("role === 'cashier' && closeOnly", 1)[1]
        # The cashier branch must return before loadDetail / loadList run.
        cashier_branch = guarded.split('return;', 1)[0]
        self.assertNotIn('loadDetail', cashier_branch)
        self.assertNotIn('loadList', cashier_branch)

    # --- Manager / admin unchanged ---

    def test_manager_full_access_unchanged(self):
        self.assertIn("role !== 'manager' && role !== 'admin'", self.src)
        # Manager/admin path still calls loadList / loadDetail.
        self.assertIn('loadList()', self.src)
        self.assertIn('loadDetail(z)', self.src)

    def test_admin_full_access_unchanged(self):
        # Admin shares the manager branch; print button and ?z= detail
        # remain accessible (not behind a cashier-only restriction).
        self.assertIn("printZ()", self.src)
        self.assertIn("zreport.html?z=", self.src)

    # --- Post-close redirect split ---

    def test_cashier_redirects_to_pos_index_after_close(self):
        self.assertIn("if (role === 'cashier') {", self.src)
        # Cashier redirect target is POS index, not Z detail.
        idx = self.src.index("if (role === 'cashier') {")
        # Cashier branch is the block before the `else` — assert POS-index
        # redirect lives inside it, Z-detail redirect lives outside it.
        branch = self.src[idx:self.src.index('} else {', idx)]
        self.assertIn("window.location.href = '/'", branch)
        self.assertNotIn('zreport.html?z=', branch)
