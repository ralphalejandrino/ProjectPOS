from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from rest_framework_simplejwt.views import TokenRefreshView
from canteen.auth_views import CustomTokenObtainPairView, logout_view
from canteen.views import HealthCheckView

urlpatterns = [
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
