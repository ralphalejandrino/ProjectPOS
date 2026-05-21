"""FEATURE-020 — local-status MVP role gating and shape."""

from unittest.mock import patch

from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from canteen.models import User


class LocalStatusRoleGateTests(APITestCase):
    def setUp(self):
        self.admin = User.objects.create_user(
            username='adm', password='x', role='admin')
        self.manager = User.objects.create_user(
            username='mgr', password='x', role='manager')
        self.cashier = User.objects.create_user(
            username='csh', password='x', role='cashier')
        self.status_url = reverse('local-status')
        self.snap_url = reverse('local-status-snapshot')

    def test_get_status_admin_ok(self):
        self.client.force_authenticate(self.admin)
        r = self.client.get(self.status_url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)
        self.assertIn('backup', r.data)
        self.assertIn('disk', r.data)
        self.assertIn('server_time', r.data)
        disk = r.data['disk']
        for key in ('total_bytes', 'used_bytes', 'available_bytes', 'used_percent'):
            self.assertIn(key, disk)

    def test_get_status_manager_ok(self):
        self.client.force_authenticate(self.manager)
        r = self.client.get(self.status_url)
        self.assertEqual(r.status_code, status.HTTP_200_OK)

    def test_get_status_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        r = self.client.get(self.status_url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)

    def test_snapshot_admin_ok(self):
        self.client.force_authenticate(self.admin)
        with patch('canteen.views.subprocess.run') as run_mock, \
             patch('canteen.views._latest_backup_info',
                   return_value={'filename': 'db_20260521_120000.sqlite3',
                                 'size_bytes': 2048,
                                 'mtime': '2026-05-21T12:00:00+00:00'}):
            run_mock.return_value.returncode = 0
            run_mock.return_value.stderr = ''
            r = self.client.post(self.snap_url)
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)
        self.assertEqual(r.data['filename'], 'db_20260521_120000.sqlite3')
        self.assertEqual(r.data['size_bytes'], 2048)

    def test_snapshot_manager_ok(self):
        self.client.force_authenticate(self.manager)
        with patch('canteen.views.subprocess.run') as run_mock, \
             patch('canteen.views._latest_backup_info',
                   return_value={'filename': 'db_20260521_120001.sqlite3',
                                 'size_bytes': 4096,
                                 'mtime': '2026-05-21T12:00:01+00:00'}):
            run_mock.return_value.returncode = 0
            run_mock.return_value.stderr = ''
            r = self.client.post(self.snap_url)
        self.assertEqual(r.status_code, status.HTTP_200_OK, r.data)

    def test_snapshot_cashier_forbidden(self):
        self.client.force_authenticate(self.cashier)
        r = self.client.post(self.snap_url)
        self.assertEqual(r.status_code, status.HTTP_403_FORBIDDEN)
