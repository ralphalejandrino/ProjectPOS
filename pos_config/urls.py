from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView
from canteen.auth_views import CustomTokenObtainPairView, logout_view
from canteen.views import HealthCheckView, admin_lockdown

urlpatterns = [
    # FIX-PENDING-20: explicit 404 — must precede the admin/ include so it is
    # not swallowed by the admin namespace (or the PWA catch-all downstream).
    path('admin/lockdown/', admin_lockdown, name='admin_lockdown'),
    path('admin/', admin.site.urls),
    path('api/health/', HealthCheckView.as_view(), name='health-check'),
    path('api/canteen/', include('canteen.urls')),

    # Auth endpoints
    path('api/auth/token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('api/auth/token/refresh/', TokenRefreshView.as_view(), name='token_refresh'),
    path('api/auth/logout/', logout_view, name='auth_logout'),
    
    # Payment endpoints
    path('api/payments/', include('canteen.payment_urls')),
]

# Serve media files in all environments (LAN-only SQLite deployment — no nginx)
urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)

# Dev-only: serve the static PWA frontend (frontend/public) at the SAME origin
# as the API so `manage.py runserver` can present the whole app at
# localhost:8000. The frontend calls the API via relative paths (config.js:
# API_BASE='/api/canteen'), so it must share the API's origin. In production
# nginx serves frontend/public and proxies /api to gunicorn — this block is
# DEBUG-gated and never active there. Registered last so /api, /admin, /media
# always match first.
if settings.DEBUG:
    from django.urls import re_path
    from django.views.static import serve as _serve_frontend

    _FRONTEND_ROOT = settings.BASE_DIR / 'frontend' / 'public'
    urlpatterns += [
        re_path(r'^$', _serve_frontend,
                {'path': 'index.html', 'document_root': _FRONTEND_ROOT}),
        re_path(r'^(?P<path>.+)$', _serve_frontend,
                {'document_root': _FRONTEND_ROOT}),
    ]
