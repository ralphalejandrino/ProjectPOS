from django.contrib import admin
from .models import (
    ItemCategory, 
    Item, 
    ItemLog, 
    PosTransaction, 
    PosTransactionItem, 
    Cart, 
    CartItem,
    User,
    EmployeeProfile,
    Attendance,
    PaymentGatewayConfig
)

@admin.register(ItemCategory)
class ItemCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'created_at']
    search_fields = ['name']

@admin.register(Item)
class ItemAdmin(admin.ModelAdmin):
    list_display = ['name', 'category', 'price', 'stock', 'created_at']
    list_filter = ['category']
    search_fields = ['name']

@admin.register(ItemLog)
class ItemLogAdmin(admin.ModelAdmin):
    list_display = ['item', 'action', 'created_at']

@admin.register(PosTransaction)
class PosTransactionAdmin(admin.ModelAdmin):
    list_display = ['transaction_no', 'total_amount', 'void', 'created_at']

@admin.register(PosTransactionItem)
class PosTransactionItemAdmin(admin.ModelAdmin):
    list_display = ['id', 'created_at']

@admin.register(Cart)
class CartAdmin(admin.ModelAdmin):
    list_display = ['user', 'created_at']

@admin.register(CartItem)
class CartItemAdmin(admin.ModelAdmin):
    list_display = ['cart', 'item', 'quantity']
