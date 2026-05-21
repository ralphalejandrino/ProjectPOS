"""FEATURE-039 — WiFi network management endpoints.

All tests mock the network_service boundary, so nothing here touches real
networking. The privileged state machine itself is exercised separately by the
shell dry-run; these cover the HTTP contract: admin-only gating and validation.
"""
from unittest import mock

from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User


class NetworkEndpointTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(username='ralph', password='x', role='admin')
        self.manager = User.objects.create_user(username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(username='cash', password='x', role='cashier')

    # --- gating: admin only --------------------------------------------------
    def test_status_admin_only_manager_forbidden(self):
        self.client.force_authenticate(self.manager)
        self.assertEqual(self.client.get('/api/canteen/network/').status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_status_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        self.assertEqual(self.client.get('/api/canteen/network/').status_code,
                         status.HTTP_403_FORBIDDEN)

    def test_apply_manager_forbidden(self):
        self.client.force_authenticate(self.manager)
        resp = self.client.post('/api/canteen/network/apply/',
                                {'ssid': 'X', 'password': 'abcd1234'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_403_FORBIDDEN)

    def test_unauthenticated_forbidden(self):
        resp = self.client.post('/api/canteen/network/apply/', {'ssid': 'X'}, format='json')
        self.assertIn(resp.status_code,
                      (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN))

    # --- status --------------------------------------------------------------
    @mock.patch('canteen.network_service.get_state', return_value={'status': 'pending'})
    @mock.patch('canteen.network_service.current_wifi',
                return_value={'ssid': 'GoodNet', 'device': 'wlan0'})
    def test_status_returns_current_and_pending(self, _cw, _gs):
        self.client.force_authenticate(self.admin)
        resp = self.client.get('/api/canteen/network/')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['current']['ssid'], 'GoodNet')
        self.assertEqual(resp.data['pending']['status'], 'pending')

    # --- validation (no service call needed) ---------------------------------
    def test_apply_rejects_empty_ssid(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/apply/',
                                {'ssid': '', 'password': 'abcd1234'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    def test_apply_rejects_short_password(self):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/apply/',
                                {'ssid': 'Cafe', 'password': 'short'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)

    # --- apply / confirm happy + error paths (service mocked) ----------------
    @mock.patch('canteen.network_service.get_state', return_value={'status': 'pending'})
    @mock.patch('canteen.network_service.apply', return_value=(True, 'pending'))
    def test_apply_success(self, mock_apply, _gs):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/apply/',
                                {'ssid': 'Cafe', 'password': 'abcd1234'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_apply.assert_called_once_with('Cafe', 'abcd1234')
        self.assertEqual(resp.data['pending']['status'], 'pending')

    @mock.patch('canteen.network_service.apply',
                return_value=(False, 'a network change is already pending confirmation'))
    def test_apply_service_failure_returns_400(self, _apply):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/apply/',
                                {'ssid': 'Cafe', 'password': 'abcd1234'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn('pending', resp.data['error'])

    @mock.patch('canteen.network_service.apply', return_value=(True, 'pending'))
    def test_apply_allows_open_network_no_password(self, mock_apply, ):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/apply/', {'ssid': 'OpenCafe'}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        mock_apply.assert_called_once_with('OpenCafe', '')

    @mock.patch('canteen.network_service.get_state', return_value={'status': 'confirmed'})
    @mock.patch('canteen.network_service.confirm', return_value=(True, 'confirmed'))
    def test_confirm_success(self, _confirm, _gs):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/confirm/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        self.assertEqual(resp.data['pending']['status'], 'confirmed')

    @mock.patch('canteen.network_service.confirm', return_value=(False, 'nothing pending to confirm'))
    def test_confirm_nothing_pending_returns_400(self, _confirm):
        self.client.force_authenticate(self.admin)
        resp = self.client.post('/api/canteen/network/confirm/', {}, format='json')
        self.assertEqual(resp.status_code, status.HTTP_400_BAD_REQUEST)
