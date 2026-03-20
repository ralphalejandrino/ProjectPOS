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
router.register(r'variant-groups', views.VariantGroupViewSet, basename='variant-groups')

urlpatterns = [
    path('', include(router.urls)),
    path('auth/login/', auth_views.login_view, name='login'),
    path('auth/logout/', auth_views.logout_view, name='logout'),
    path('auth/me/', auth_views.current_user, name='current_user'),
    path('business/', views.get_business_profile, name='get_business_profile'),
    path('business/update/', views.update_business_profile, name='update_business_profile'),
    path('business/logo/', views.upload_business_logo, name='upload-business-logo'),
    # Nested: variant options under variant groups
    path('variant-groups/<uuid:group_pk>/options/', views.VariantOptionViewSet.as_view({'get': 'list', 'post': 'create'}), name='variant-options-list'),
    path('variant-groups/<uuid:group_pk>/options/<uuid:pk>/', views.VariantOptionViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='variant-options-detail'),
    # Nested: variant groups under categories
    path('categories/<uuid:category_pk>/variant-groups/', views.CategoryVariantGroupViewSet.as_view({'get': 'list', 'post': 'create'}), name='category-variant-groups-list'),
    path('categories/<uuid:category_pk>/variant-groups/<int:pk>/', views.CategoryVariantGroupViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='category-variant-groups-detail'),
    # Nested: variant groups under items/products
    path('items/<uuid:product_pk>/variant-groups/', views.ProductVariantGroupViewSet.as_view({'get': 'list', 'post': 'create'}), name='product-variant-groups-list'),
    path('items/<uuid:product_pk>/variant-groups/<int:pk>/', views.ProductVariantGroupViewSet.as_view({'get': 'retrieve', 'put': 'update', 'patch': 'partial_update', 'delete': 'destroy'}), name='product-variant-groups-detail'),
]
