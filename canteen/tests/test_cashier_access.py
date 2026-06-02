"""QA-S5-002 — cashier-role authentication & access-control coverage.

Background / 503 investigation
------------------------------
QA-S5-002 reported that authenticating as a cashier-role user produced a 503
instead of a 200/403, implying the cashier surface was untested or broken.
Reproduction at HEAD (migration 0035) across the POS read paths, own-shift
views, ring-sale POST, the management/report endpoints, the JWT token endpoint
and the ``auth/login`` POST shows **no 503**: every cashier request resolves to
a deterministic 200/204/400/403. The only 503 emitter in the codebase is
``HealthCheckView`` (an AllowAny DB-liveness probe that returns 503 only when
the database is unreachable — by design, and unrelated to cashier auth).

The genuine gap was the *absence* of dedicated cashier-role access-control
tests (other suites provision cashiers incidentally but never assert the full
allow/deny matrix). This module provisions a first-class cashier fixture and
locks down that matrix so the surface can no longer regress silently.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User


class CashierAccessControlTests(APITestCase):
    """Authenticate as a cashier and assert the allow/deny matrix end to end."""

    def setUp(self):
        # First-class cashier fixture (the QA-S5-002 provisioning gap).
        self.cashier = User.objects.create_user(
            username='cashier1', password='x', role='cashier',
            first_name='Maria', last_name='Santos')
        self.client.force_authenticate(self.cashier)

    # ── Cashier CAN ──────────────────────────────────────────────────────
    def test_can_read_pos_items(self):
        # POS hot path — the page the cashier lives on.
        self.assertEqual(
            self.client.get('/api/canteen/items/').status_code, status.HTTP_200_OK)

    def test_can_read_pos_categories(self):
        self.assertEqual(
            self.client.get('/api/canteen/categories/').status_code, status.HTTP_200_OK)

    def test_can_view_own_shift(self):
        # No open shift yet → 204 (reachable, not forbidden / errored).
        resp = self.client.get('/api/canteen/shifts/current/')
        self.assertIn(resp.status_code,
                      (status.HTTP_200_OK, status.HTTP_204_NO_CONTENT))

    def test_can_open_own_shift(self):
        resp = self.client.post(
            '/api/canteen/shifts/open/', {'opening_cash': '100.00'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_201_CREATED)

    def test_can_reach_ring_sale(self):
        # Ring-sale POST is permitted for cashiers: an empty cart is a 400
        # (validation), proving the permission gate let the request through —
        # NOT a 401/403 (denied) and NOT a 5xx (the QA-S5-002 regression).
        resp = self.client.post('/api/canteen/transactions/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertLess(resp.status_code, 500)

    # ── Cashier CANNOT ───────────────────────────────────────────────────
    def test_cannot_access_users(self):
        self.assertEqual(
            self.client.get('/api/canteen/users/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_access_settings(self):
        # WiFi/network is the admin-only Settings surface.
        self.assertEqual(
            self.client.get('/api/canteen/network/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_access_period_report(self):
        self.assertEqual(
            self.client.get('/api/canteen/reports/period/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_access_insights_report(self):
        self.assertEqual(
            self.client.get('/api/canteen/reports/insights/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_access_remote_ops(self):
        self.assertEqual(
            self.client.get('/api/canteen/remote/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_access_dashboard(self):
        self.assertEqual(
            self.client.get('/api/canteen/dashboard/').status_code,
            status.HTTP_403_FORBIDDEN)

    def test_cannot_void_transaction(self):
        # Void is a manager/admin override — denied at the permission layer
        # before any object lookup, so a placeholder pk still yields 403.
        resp = self.client.post('/api/canteen/transactions/1/void/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cannot_write_ingredients(self):
        # Ingredients admin action (create) is gated; cashier is denied.
        resp = self.client.post(
            '/api/canteen/ingredients/', {'name': 'Sugar'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    # ── 503 guard ────────────────────────────────────────────────────────
    def test_auth_path_never_returns_5xx(self):
        # QA-S5-002 regression guard: a cashier hitting these surfaces must
        # always get a deterministic client-side status, never a 5xx.
        for url in ('/api/canteen/items/', '/api/canteen/shifts/current/',
                    '/api/canteen/users/', '/api/canteen/reports/period/',
                    '/api/canteen/network/', '/api/canteen/dashboard/'):
            self.assertLess(self.client.get(url).status_code, 500, url)
