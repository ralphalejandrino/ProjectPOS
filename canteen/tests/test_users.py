from decimal import Decimal

from django.core.cache import cache
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User, Shift


class SetRoleEndpointTests(APITestCase):
    """ISSUE-089 — admin-only PATCH /users/<id>/role/ verification gates."""

    def setUp(self):
        self.admin = User.objects.create_user(username='ralph', password='x', role='admin')
        self.manager = User.objects.create_user(username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(username='cash', password='x', role='cashier')

    def _url(self, user):
        return f'/api/canteen/users/{user.pk}/role/'

    def test_admin_can_promote_cashier_to_manager(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'role': 'manager'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.role, 'manager')

    def test_unknown_role_rejected_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'role': 'superuser'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_admin_self_demotion_rejected_403(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.admin), {'role': 'manager'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.role, 'admin')

    def test_non_admin_forbidden_403(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(self._url(self.cashier), {'role': 'manager'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_rejected_401(self):
        resp = self.client.patch(self._url(self.cashier), {'role': 'manager'}, format='json')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class ResetPasswordEndpointTests(APITestCase):
    """FEATURE-006 — PATCH /users/<id>/reset-password/ gates and behavior."""

    def setUp(self):
        self.admin = User.objects.create_user(username='ralph', password='x', role='admin')
        self.manager = User.objects.create_user(username='mgr', password='oldpass', role='manager')
        self.cashier = User.objects.create_user(username='cash', password='oldpass', role='cashier')

    def _url(self, user):
        return f'/api/canteen/users/{user.pk}/reset-password/'

    def test_admin_resets_cashier_password(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'password': 'newpass123'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.check_password('newpass123'))

    def test_manager_resets_cashier_password(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(self._url(self.cashier), {'password': 'newpass123'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.check_password('newpass123'))

    def test_reset_does_not_change_role(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier),
                                 {'password': 'newpass123', 'role': 'admin'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.role, 'cashier')  # role payload ignored — no escalation

    def test_short_password_rejected_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'password': 'abc'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.cashier.refresh_from_db()
        self.assertTrue(self.cashier.check_password('oldpass'))  # unchanged

    def test_cashier_forbidden_403(self):
        self.client.force_authenticate(self.cashier)
        resp = self.client.patch(self._url(self.manager), {'password': 'newpass123'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_admin_target_not_resettable_404(self):
        # Admins are outside the managed queryset, so they cannot be targeted —
        # blocks lateral takeover of an admin account via this flow.
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(self._url(self.admin), {'password': 'newpass123'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)

    def test_unauthenticated_rejected(self):
        resp = self.client.patch(self._url(self.cashier), {'password': 'newpass123'}, format='json')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class QuickLoginGridTests(APITestCase):
    """ISSUE-113 — the pre-login avatar/PIN grid must render for unauthenticated
    LAN callers. The regression was get_permissions() overriding the action's
    AllowAny and 401'ing the anonymous fetch, collapsing the grid to the
    standard-form fallback."""

    URL = '/api/canteen/users/quick-login/'

    def setUp(self):
        cache.clear()  # QuickLoginRateThrottle is 10/min — keep tests isolated
        User.objects.create_user(username='cash', password='x', role='cashier')
        User.objects.create_user(username='mgr', password='x', role='manager')
        User.objects.create_user(username='boss', password='x', role='admin')
        User.objects.create_user(username='gone', password='x', role='cashier', is_active=False)

    def test_unauthenticated_lan_caller_gets_grid(self):
        # The actual ISSUE-113 path: anonymous client, private/loopback IP.
        resp = self.client.get(self.URL, REMOTE_ADDR='127.0.0.1')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {u['username'] for u in resp.data}
        self.assertIn('cash', names)
        self.assertIn('mgr', names)

    def test_grid_excludes_admin_and_inactive(self):
        resp = self.client.get(self.URL, REMOTE_ADDR='192.168.1.20')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        names = {u['username'] for u in resp.data}
        self.assertNotIn('boss', names)   # admins not enumerated
        self.assertNotIn('gone', names)   # inactive excluded

    def test_public_caller_gets_404(self):
        # Enumeration guard (FLAG-039) stays intact — genuine fallback case.
        resp = self.client.get(self.URL, REMOTE_ADDR='8.8.8.8')
        self.assertEqual(resp.status_code, status.HTTP_404_NOT_FOUND)


class RenameEndpointTests(APITestCase):
    """FEATURE-041 — admin-only PATCH /users/<id>/rename/."""

    def setUp(self):
        self.admin = User.objects.create_user(username='ralph', password='x', role='admin')
        self.manager = User.objects.create_user(username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(username='cash', password='x', role='cashier')

    def _url(self, user):
        return f'/api/canteen/users/{user.pk}/rename/'

    def test_admin_renames_cashier(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier),
                                 {'username': 'cashier_renamed', 'first_name': 'Cash', 'last_name': 'Yer'},
                                 format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.username, 'cashier_renamed')
        self.assertEqual(self.cashier.first_name, 'Cash')

    def test_duplicate_username_rejected_409(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'username': 'mgr'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.cashier.refresh_from_db()
        self.assertEqual(self.cashier.username, 'cash')  # unchanged

    def test_duplicate_username_case_insensitive_rejected(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'username': 'MGR'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)

    def test_blank_username_rejected_400(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.patch(self._url(self.cashier), {'username': '   '}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_manager_forbidden_403(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.patch(self._url(self.cashier), {'username': 'whatever'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_rejected(self):
        resp = self.client.patch(self._url(self.cashier), {'username': 'whatever'}, format='json')
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))


class DeleteUserEndpointTests(APITestCase):
    """FEATURE-041 — admin-only hard delete DELETE /users/<id>/ with audit
    safeguards (self / last-admin / has-history all blocked)."""

    def setUp(self):
        self.admin = User.objects.create_user(username='ralph', password='x', role='admin')
        self.admin2 = User.objects.create_user(username='ralph2', password='x', role='admin')
        self.manager = User.objects.create_user(username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(username='cash', password='x', role='cashier')

    def _url(self, user):
        return f'/api/canteen/users/{user.pk}/'

    def test_admin_deletes_clean_user(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self._url(self.cashier))
        self.assertEqual(resp.status_code, status.HTTP_204_NO_CONTENT)
        self.assertFalse(User.objects.filter(pk=self.cashier.pk).exists())

    def test_cannot_delete_self(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self._url(self.admin))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_cannot_delete_last_active_admin(self):
        # Make admin the sole *active* admin; admin2 goes inactive and acts (an
        # inactive admin can't really log in, but force_authenticate exercises the
        # guard directly — defense in depth). Deleting the last active admin is blocked.
        self.admin2.is_active = False
        self.admin2.save(update_fields=['is_active'])
        self.client.force_authenticate(self.admin2)
        resp = self.client.delete(self._url(self.admin))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.admin.pk).exists())

    def test_cannot_delete_user_with_shift_history(self):
        Shift.objects.create(cashier=self.cashier, opening_cash=Decimal('100'), is_open=False)
        self.client.force_authenticate(self.admin)
        resp = self.client.delete(self._url(self.cashier))
        self.assertEqual(resp.status_code, status.HTTP_409_CONFLICT)
        self.assertTrue(User.objects.filter(pk=self.cashier.pk).exists())

    def test_manager_forbidden_403(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.delete(self._url(self.cashier))
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)
        self.assertTrue(User.objects.filter(pk=self.cashier.pk).exists())

    def test_unauthenticated_rejected(self):
        resp = self.client.delete(self._url(self.cashier))
        self.assertIn(resp.status_code, (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))
