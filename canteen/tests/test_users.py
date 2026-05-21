from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User


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
