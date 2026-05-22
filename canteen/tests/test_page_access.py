"""FEATURE-044 — per-user page-access tests.

Covers the canonical access logic, the set_page_access guardrail (no escalation),
deploy-safety (defaults exactly match the historical role gate), the JWT claim,
and live server-side enforcement on the page-mapped viewsets.
"""
from rest_framework import status
from rest_framework.test import APITestCase

from canteen import access
from canteen.models import User
from canteen.auth_views import CustomTokenObtainPairSerializer


class AccessLogicTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='a', password='x', role='admin')
        self.manager = User.objects.create_user(username='m', password='x', role='manager')
        self.cashier = User.objects.create_user(username='c', password='x', role='cashier')

    def test_defaults_match_role_gate(self):
        # Deploy safety: no override → exactly the historical role mapping.
        self.assertEqual(
            access.effective_pages(self.admin),
            {'pos', 'status', 'inventory', 'ingredients', 'dashboard', 'xreport', 'zreport', 'settings'},
        )
        self.assertEqual(
            access.effective_pages(self.manager),
            {'pos', 'status', 'inventory', 'ingredients', 'dashboard', 'xreport', 'zreport'},
        )
        self.assertEqual(access.effective_pages(self.cashier), {'pos'})

    def test_override_expands_and_restricts(self):
        self.cashier.allowed_pages = ['dashboard']
        self.assertEqual(access.effective_pages(self.cashier), {'pos', 'dashboard'})
        self.manager.allowed_pages = ['inventory']  # revoke everything but inventory
        eff = access.effective_pages(self.manager)
        self.assertIn('inventory', eff)
        self.assertNotIn('zreport', eff)
        self.assertIn('pos', eff)      # pos always present
        self.assertIn('status', eff)   # non-gateable follows role default

    def test_grantable_excludes_sensitive_for_manager(self):
        self.assertNotIn('settings', access.grantable_pages(self.manager))
        self.assertIn('settings', access.grantable_pages(self.admin))

    def test_resolve_preserves_non_manageable_bits(self):
        # A manager cannot grant settings even if they request it; the target's
        # current settings bit is preserved untouched.
        target = self.cashier
        new = access.resolve_page_access(self.manager, target, ['dashboard', 'settings'])
        self.assertIn('dashboard', new)
        self.assertNotIn('settings', new)

    def test_resolve_returns_none_when_equals_role_default(self):
        # Setting a manager back to exactly their role default stores NULL.
        default = sorted(access.role_default_gateable('manager'))
        self.assertIsNone(access.resolve_page_access(self.admin, self.manager, default))


class JWTClaimTests(APITestCase):
    def test_token_carries_effective_pages(self):
        cashier = User.objects.create_user(username='c', password='x', role='cashier')
        cashier.allowed_pages = ['dashboard']
        cashier.save()
        token = CustomTokenObtainPairSerializer.get_token(cashier)
        self.assertEqual(set(token['pages']), {'pos', 'dashboard'})


class SetPageAccessEndpointTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='a', password='x', role='admin')
        self.manager = User.objects.create_user(username='m', password='x', role='manager')
        self.cashier = User.objects.create_user(username='c', password='x', role='cashier')

    def _url(self, u):
        return f'/api/canteen/users/{u.pk}/page-access/'

    def test_admin_grants_cashier_dashboard(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'pages': ['dashboard']}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertIn('dashboard', access.effective_pages(self.cashier))

    def test_manager_cannot_grant_settings(self):
        # Escalation attempt → 403, nothing persisted.
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(self._url(self.cashier), {'pages': ['settings']}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.cashier.refresh_from_db()
        self.assertNotIn('settings', access.effective_pages(self.cashier))

    def test_unknown_page_rejected(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'pages': ['hackpage']}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_catalog_grantable_differs_by_role(self):
        self.client.force_authenticate(self.admin)
        admin_cat = self.client.get('/api/canteen/users/page-access-catalog/').json()
        self.assertIn('settings', admin_cat['grantable'])
        self.client.force_authenticate(self.manager)
        mgr_cat = self.client.get('/api/canteen/users/page-access-catalog/').json()
        self.assertNotIn('settings', mgr_cat['grantable'])


class ServerEnforcementTests(APITestCase):
    """Per-user access takes effect immediately on the page-mapped APIs."""

    def setUp(self):
        self.admin = User.objects.create_user(username='a', password='x', role='admin')
        self.cashier = User.objects.create_user(username='c', password='x', role='cashier')
        self.manager = User.objects.create_user(username='m', password='x', role='manager')

    def test_cashier_denied_dashboard_by_default(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/dashboard/')
        self.assertIn(resp.status_code, (status.HTTP_403_FORBIDDEN, status.HTTP_404_NOT_FOUND))

    def test_granted_cashier_reaches_dashboard(self):
        self.cashier.allowed_pages = ['dashboard']
        self.cashier.save()
        self.client.force_authenticate(self.cashier)
        resp = self.client.get('/api/canteen/dashboard/')
        self.assertNotIn(resp.status_code, (status.HTTP_403_FORBIDDEN,))

    def test_revoked_manager_denied_zreport(self):
        self.manager.allowed_pages = ['inventory']  # zreport revoked
        self.manager.save()
        self.client.force_authenticate(self.manager)
        resp = self.client.get('/api/canteen/z-reports/')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_cashier_pos_reads_still_work(self):
        # Gating inventory writes must not break the POS read path.
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get('/api/canteen/items/').status_code, status.HTTP_200_OK)
        self.assertEqual(self.client.get('/api/canteen/categories/').status_code, status.HTTP_200_OK)
