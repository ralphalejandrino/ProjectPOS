from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from . import auth_views

router = DefaultRouter()
router.register(r'categories', views.ItemCategoryViewSet, basename='category')
router.register(r'items', views.ItemViewSet, basename='item')
router.register(r'transactions', views.PosTransactionViewSet, basename='transaction')
router.register(r'shifts', views.ShiftViewSet, basename='shift')
router.register(r'dashboard', views.DashboardViewSet, basename='dashboard')
router.register(r'users', auth_views.UserViewSet, basename='user')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/login/', auth_views.login_view, name='login'),
    path('auth/logout/', auth_views.logout_view, name='logout'),
    path('auth/me/', auth_views.current_user, name='current_user'),
    path('business/', views.get_business_profile, name='get_business_profile'),
    path('business/update/', views.update_business_profile, name='update_business_profile'),
    path('business/logo/', views.upload_business_logo, name='upload-business-logo'),
    # Item variants — nested under items
    path('items/<uuid:item_pk>/variants/', views.ItemVariantViewSet.as_view({'get': 'list', 'post': 'create'}), name='item-variants-list'),
    path('items/<uuid:item_pk>/variants/<uuid:pk>/', views.ItemVariantViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='item-variants-detail'),
]
