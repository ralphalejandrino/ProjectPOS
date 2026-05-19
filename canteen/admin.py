from django.contrib import admin
from .models import (
    ItemCategory,
    Item,
    ItemLog,
    OfficialReceiptCounter,
    PosTransaction,
    PosTransactionItem,
    Cart,
    CartItem,
    User,
    EmployeeProfile,
    Attendance,
    PaymentGatewayConfig,
    BusinessProfile,
    ZReport,
    ZCounter,
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

@admin.register(OfficialReceiptCounter)
class OfficialReceiptCounterAdmin(admin.ModelAdmin):
    list_display = ['date', 'counter', 'updated_at']
    readonly_fields = ['date', 'counter', 'updated_at']

    def has_add_permission(self, request):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_change_permission(self, request, obj=None):
        return False


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


@admin.register(BusinessProfile)
class BusinessProfileAdmin(admin.ModelAdmin):
    list_display = ['business_name', 'tin', 'currency', 'updated_at']

    fieldsets = (
        ('Business Information', {
            'fields': (
                'business_name', 'tagline', 'logo', 'contact_number',
                'email', 'address', 'tin',
            ),
        }),
        ('Receipt', {
            'fields': ('receipt_header', 'receipt_footer'),
        }),
        ('Printer', {
            'fields': (
                'printer_enabled', 'printer_ip', 'printer_port',
            ),
        }),
        ('Rate Configuration', {
            'fields': (
                'currency', 'vat_enabled', 'vat_rate', 'vat_inclusive',
                'sc_discount_enabled', 'sc_discount_rate',
                'pwd_discount_enabled', 'pwd_discount_rate',
                'promo_discount_enabled', 'discounts_enabled',
            ),
        }),
        ('BIR Compliance', {
            'description': 'BIR-issued machine and accreditation identifiers '
                           '(display-only on receipts/Z reports).',
            'fields': (
                'machine_identification_number',
                'machine_serial_number',
                'pos_accreditation_number',
                'pos_permit_number',
                'pos_accreditation_valid_until',
            ),
        }),
        ('Inventory & Misc', {
            'fields': (
                'track_inventory', 'low_stock_threshold', 'color_scheme',
            ),
        }),
    )


@admin.register(ZReport)
class ZReportAdmin(admin.ModelAdmin):
    list_display = ['z_counter', 'business_date', 'finalized_at', 'cashier',
                    'gross_sales', 'net_sales']
    list_filter = ['business_date', 'cashier']
    readonly_fields = [f.name for f in ZReport._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False


@admin.register(ZCounter)
class ZCounterAdmin(admin.ModelAdmin):
    list_display = ['id', 'z_counter', 'reset_counter', 'grand_total']
    readonly_fields = [f.name for f in ZCounter._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
