from django.urls import path
from . import views

urlpatterns = [
    path('gcash/', views.process_gcash_payment, name='process_gcash'),
    path('maya/', views.process_maya_payment, name='process_maya'),
    path('card/', views.process_card_payment, name='process_card'),
    path('config/', views.get_payment_config, name='get_payment_config'),
    path('config/update/', views.update_payment_config, name='update_payment_config'),
    path('config/<str:gateway>/qr/', views.upload_payment_qr, name='upload_payment_qr'),
    path('config/<str:gateway>/terminal-status/', views.get_terminal_credential_status, name='get_terminal_credential_status'),
    path('config/<str:gateway>/', views.get_gateway_qr_config, name='get_gateway_qr_config'),
]

