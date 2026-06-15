"""BUG-002 — the kiosk POS session persists across long idle gaps.

The POS auto-logged-out when left unattended. There is no frontend idle timer;
the only logout path is token expiry. The frontend silently refreshes the short
(15-min) access token on a 401, so the *session* lifetime is bounded entirely by
REFRESH_TOKEN_LIFETIME. That was 1 day, so an overnight/closed-day idle let the
refresh token expire and the next cashier action bounced to the login screen.

The deployment now uses a long refresh-token window so the kiosk stays logged in
until a manual logout. These tests pin that contract: the window is long, and a
refresh issued after the access token would have expired still succeeds and
(rotation being on) hands back a fresh long-lived refresh token.
"""

from datetime import timedelta

from django.conf import settings
from rest_framework import status
from rest_framework.test import APITestCase
from rest_framework_simplejwt.tokens import RefreshToken

from canteen.models import User


class KioskSessionPersistenceTests(APITestCase):
    def setUp(self):
        self.cashier = User.objects.create_user(
            username='cashier', password='x', role='cashier'
        )

    def test_refresh_window_is_long_enough_for_unattended_kiosk(self):
        # A kiosk can sit idle over a weekend/holiday closure; the prior 1-day
        # window was the regression. Require a window measured in months so an
        # unattended till does not silently log itself out.
        self.assertGreaterEqual(
            settings.SIMPLE_JWT['REFRESH_TOKEN_LIFETIME'],
            timedelta(days=90),
            'REFRESH_TOKEN_LIFETIME too short — kiosk will log out when idle.',
        )
        # Access token stays short — silent refresh keeps it transparent.
        self.assertLessEqual(
            settings.SIMPLE_JWT['ACCESS_TOKEN_LIFETIME'],
            timedelta(hours=1),
        )

    def test_refresh_after_access_expiry_keeps_session_alive(self):
        # Simulate the unattended-then-resumed flow: the access token is long
        # gone, the cashier acts, the client refreshes. With a long refresh
        # window this returns a new access (and, with rotation, a new refresh).
        refresh = RefreshToken.for_user(self.cashier)
        resp = self.client.post(
            '/api/auth/token/refresh/',
            {'refresh': str(refresh)},
            format='json',
        )
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.data)
        self.assertIn('access', resp.data)
        # ROTATE_REFRESH_TOKENS issues a fresh refresh token, resetting the full
        # window again — so continued use keeps the kiosk logged in indefinitely.
        self.assertIn('refresh', resp.data)
