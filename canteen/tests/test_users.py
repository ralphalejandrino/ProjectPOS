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
