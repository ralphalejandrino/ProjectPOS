"""
Clean POS Serializers - No Legacy School Code
"""
from rest_framework import serializers
from django.contrib.auth import authenticate
from django.utils.timezone import localtime
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
    PaymentGatewayConfig,
    Shift,
    VariantGroup,
    VariantOption,
    CategoryVariantGroup,
    ProductVariantGroup,
    TransactionItemVariant,
    IngredientUnit,
    Supplier,
    Ingredient,
    IngredientRestockLog,
    RecipeIngredient,
    BusinessProfile,
)


# ============================================================================
# CATEGORY SERIALIZERS
# ============================================================================

class ItemCategorySerializer(serializers.ModelSerializer):
    item_count = serializers.SerializerMethodField()
    
    class Meta:
        model = ItemCategory
        fields = ['id', 'name', 'emoji', 'description', 'is_active', 'item_count', 'created_at']
    
    def get_item_count(self, obj):
        return obj.items.filter(is_active=True).count()


# ============================================================================
# ITEM/PRODUCT SERIALIZERS
# ============================================================================

# ── Demo photo map(picsum seeds = consistent images per product) ────
DEMO_PHOTO_MAP = {
    "Espresso": "https://picsum.photos/seed/espresso/400/300",
    "Americano": "https://picsum.photos/seed/americano/400/300",
    "Cappuccino": "https://picsum.photos/seed/cappuccino/400/300",
    "Café Latte": "https://picsum.photos/seed/latte/400/300",
    "Caramel Macchiato": "https://picsum.photos/seed/macchiato/400/300",
    "Mocha": "https://picsum.photos/seed/mocha/400/300",
    "Iced Americano": "https://picsum.photos/seed/iced-americano/400/300",
    "Iced Latte": "https://picsum.photos/seed/iced-latte/400/300",
    "Iced Caramel Latte": "https://picsum.photos/seed/iced-caramel/400/300",
    "Cold Brew": "https://picsum.photos/seed/cold-brew/400/300",
    "Iced Mocha": "https://picsum.photos/seed/iced-mocha/400/300",
    "Sparkling Lemonade": "https://picsum.photos/seed/lemonade/400/300",
    "Matcha Latte": "https://picsum.photos/seed/matcha/400/300",
    "Chocolate Latte": "https://picsum.photos/seed/chocolate-latte/400/300",
    "Strawberry Shake": "https://picsum.photos/seed/strawberry/400/300",
    "Mango Shake": "https://picsum.photos/seed/mango/400/300",
    "Chamomile Tea": "https://picsum.photos/seed/chamomile/400/300",
    "Wintermelon Milk": "https://picsum.photos/seed/wintermelon/400/300",
    "Butter Croissant": "https://picsum.photos/seed/croissant/400/300",
    "Blueberry Muffin": "https://picsum.photos/seed/muffin/400/300",
    "Cheese Danish": "https://picsum.photos/seed/danish/400/300",
    "Pandesal": "https://picsum.photos/seed/pandesal/400/300",
    "Banana Bread": "https://picsum.photos/seed/banana-bread/400/300",
    "Ham & Cheese Toast": "https://picsum.photos/seed/ham-toast/400/300",
    "Clubhouse Sandwich": "https://picsum.photos/seed/clubhouse/400/300",
    "BLT Sandwich": "https://picsum.photos/seed/blt/400/300",
    "Caesar Salad": "https://picsum.photos/seed/caesar/400/300",
    "Carbonara Pasta": "https://picsum.photos/seed/carbonara/400/300",
    "Eggs Benedict": "https://picsum.photos/seed/eggs-benedict/400/300",
    "Chicken Pesto": "https://picsum.photos/seed/pesto/400/300",
    "Cheesecake Slice": "https://picsum.photos/seed/cheesecake/400/300",
    "Chocolate Lava": "https://picsum.photos/seed/lava-cake/400/300",
    "Tiramisu": "https://picsum.photos/seed/tiramisu/400/300",
    "Leche Flan": "https://picsum.photos/seed/flan/400/300",
    "Mango Crepe": "https://picsum.photos/seed/crepe/400/300",
    "Waffle with Cream": "https://picsum.photos/seed/waffle/400/300",
}

class ItemSerializer(serializers.ModelSerializer):
    category_name = serializers.CharField(source='category.name', read_only=True)
    profit_margin = serializers.ReadOnlyField()
    profit_per_unit = serializers.ReadOnlyField()
    is_low_stock = serializers.ReadOnlyField()
    photo = serializers.SerializerMethodField()
    effective_variant_groups = serializers.SerializerMethodField()

    def get_photo(self, obj):
        request = self.context.get('request')
        # Use uploaded photo if it exists
        if obj.photo and hasattr(obj.photo, 'url'):
            try:
                photo_url = obj.photo.url
                if request is not None:
                    return request.build_absolute_uri(photo_url)
                return photo_url
            except Exception:
                pass
        # Fall back to curated demo photo
        return DEMO_PHOTO_MAP.get(obj.name, "https://picsum.photos/seed/cafe-default/400/300")

    def get_effective_variant_groups(self, obj):
        # Category-assigned groups
        cat_groups = {}
        if obj.category_id:
            for cvg in obj.category.variant_groups.select_related('group').prefetch_related('group__options'):
                cat_groups[str(cvg.group.id)] = {
                    'group': VariantGroupSerializer(cvg.group).data,
                    'is_required': cvg.is_required_override if cvg.is_required_override is not None else cvg.group.is_required,
                    'source': 'category',
                }
        # Product overrides
        for pvg in obj.variant_group_overrides.select_related('group').prefetch_related('group__options'):
            gid = str(pvg.group.id)
            if not pvg.enabled:
                cat_groups.pop(gid, None)
            else:
                cat_groups[gid] = {
                    'group': VariantGroupSerializer(pvg.group).data,
                    'is_required': pvg.is_required_override if pvg.is_required_override is not None else pvg.group.is_required,
                    'source': 'product',
                }
        return list(cat_groups.values())

    class Meta:
        model = Item
        fields = [
            'id', 'name', 'category', 'category_name', 'price', 'purchase_price',
            'stock', 'low_stock_threshold', 'bar_code', 'bar_code_image',
            'photo', 'description', 'sku', 'expiry_date', 'is_active',
            'profit_margin', 'profit_per_unit', 'is_low_stock',
            'effective_variant_groups',
            'created_at', 'updated_at'
        ]


class ItemUpdateSerializer(serializers.ModelSerializer):
    """Handles PUT/PATCH with writable photo ImageField."""
    class Meta:
        model = Item
        fields = [
            'name', 'category', 'price', 'purchase_price', 'stock',
            'low_stock_threshold', 'bar_code', 'photo', 'description',
            'sku', 'expiry_date', 'is_active'
        ]

class ItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = [
            'name', 'category', 'price', 'purchase_price', 'stock',
            'low_stock_threshold', 'bar_code', 'photo', 'description',
            'sku', 'expiry_date', 'is_active'
        ]


class ItemLogSerializer(serializers.ModelSerializer):
    created_by_name = serializers.SerializerMethodField()
    created_at = serializers.SerializerMethodField()

    class Meta:
        model = ItemLog
        fields = [
            'id', 'created_at', 'action', 'quantity', 'current_stock',
            'remarks', 'created_by_name'
        ]

    def get_created_by_name(self, instance):
        return instance.created_by.username if instance.created_by else 'system'

    def get_created_at(self, instance):
        return localtime(instance.created_at).strftime('%Y-%m-%d %H:%M')


# ============================================================================
# VARIANT SERIALIZERS
# ============================================================================

class VariantOptionSerializer(serializers.ModelSerializer):
    class Meta:
        model = VariantOption
        fields = ['id', 'name', 'price_modifier', 'sort_order', 'is_active']


class VariantGroupSerializer(serializers.ModelSerializer):
    options = VariantOptionSerializer(many=True, read_only=True)

    class Meta:
        model = VariantGroup
        fields = ['id', 'name', 'selection_type', 'is_required', 'sort_order', 'is_active', 'options']


class CategoryVariantGroupSerializer(serializers.ModelSerializer):
    group = VariantGroupSerializer(read_only=True)
    group_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = CategoryVariantGroup
        fields = ['id', 'group', 'group_id', 'is_required_override']


class ProductVariantGroupSerializer(serializers.ModelSerializer):
    group = VariantGroupSerializer(read_only=True)
    group_id = serializers.UUIDField(write_only=True)

    class Meta:
        model = ProductVariantGroup
        fields = ['id', 'group', 'group_id', 'enabled', 'is_required_override']


class TransactionItemVariantSerializer(serializers.ModelSerializer):
    class Meta:
        model = TransactionItemVariant
        fields = ['id', 'group_name', 'option_name', 'price_modifier']


# ============================================================================
# TRANSACTION SERIALIZERS
# ============================================================================

class PosTransactionItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    variant_selections = TransactionItemVariantSerializer(many=True, read_only=True)

    class Meta:
        model = PosTransactionItem
        fields = [
            'id', 'item', 'item_name', 'quantity', 'unit_price',
            'purchase_price', 'subtotal', 'base_price', 'final_price',
            'variant_selections', 'remarks'
        ]


class PosTransactionSerializer(serializers.ModelSerializer):
    items = PosTransactionItemSerializer(many=True, read_only=True)
    cashier_name = serializers.CharField(source='cashier.username', read_only=True)
    
    class Meta:
        model = PosTransaction
        fields = [
            'id', 'transaction_no', 'total_amount', 'payment_method',
            'cash_received', 'change_given', 'gcash_reference',
            'maya_reference', 'card_reference', 'card_type',
            'status', 'cashier', 'cashier_name', 'customer_name',
            'customer_phone', 'items', 'created_at', 'void',
            'purpose_of_void', 'remarks',
            'discount_amount', 'discount_type', 'discount_id_number'
        ]


class PosTransactionCreateSerializer(serializers.ModelSerializer):
    items = PosTransactionItemSerializer(many=True)
    
    class Meta:
        model = PosTransaction
        fields = [
            'payment_method', 'cash_received', 'gcash_reference',
            'maya_reference', 'card_reference', 'customer_name',
            'customer_phone', 'remarks', 'items'
        ]
    
    def create(self, validated_data):
        from djmoney.money import Money
        from decimal import Decimal
        
        items_data = validated_data.pop('items')
        
        # 1. Calculate the total from items first
        calculated_total = Decimal('0.00')
        for item in items_data:
            # subtotal = price * quantity
            qty = item.get('quantity', 0)
            price = item.get('unit_price', 0)
            calculated_total += Decimal(str(price)) * Decimal(str(qty))
        
        # 2. Create the transaction with the correct total_amount
        validated_data['total_amount'] = Money(calculated_total, 'PHP')
        transaction = PosTransaction.objects.create(**validated_data)
        
        # 3. Create the transaction line items
        for item_data in items_data:
            PosTransactionItem.objects.create(
                pos_transaction=transaction,
                **item_data
            )
        
        # 4. Calculate change for cash payments based on final total
        if transaction.payment_method == 'cash' and transaction.cash_received:
            transaction.change_given = Decimal(str(transaction.cash_received)) - calculated_total
            transaction.save()

        return transaction

    def update(self, instance, validated_data):
        from djmoney.money import Money
        from decimal import Decimal

        items_data = validated_data.pop('items', None)

        # Update parent transaction fields
        if items_data is not None:
            calculated_total = Decimal('0.00')
            for item in items_data:
                qty = item.get('quantity', 0)
                price = item.get('unit_price', 0)
                calculated_total += Decimal(str(price)) * Decimal(str(qty))
            validated_data['total_amount'] = Money(calculated_total, 'PHP')

        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()

        # Full replace of nested line items (and their variant rows via cascade).
        # Acceptable in this offline-first, single-cashier POS context.
        if items_data is not None:
            instance.items.all().delete()
            for item_data in items_data:
                PosTransactionItem.objects.create(
                    pos_transaction=instance,
                    **item_data
                )

            if instance.payment_method == 'cash' and instance.cash_received:
                instance.change_given = (
                    Decimal(str(instance.cash_received)) - calculated_total
                )
                instance.save()

        return instance


# ============================================================================
# CART SERIALIZERS
# ============================================================================

class CartItemSerializer(serializers.ModelSerializer):
    item_name = serializers.CharField(source='item.name', read_only=True)
    price = serializers.ReadOnlyField()
    subtotal = serializers.ReadOnlyField()
    
    class Meta:
        model = CartItem
        fields = ['id', 'item', 'item_name', 'quantity', 'price', 'subtotal']


class CartSerializer(serializers.ModelSerializer):
    cart_items = CartItemSerializer(many=True, read_only=True)
    total = serializers.ReadOnlyField()
    item_count = serializers.ReadOnlyField()
    
    class Meta:
        model = Cart
        fields = ['id', 'user', 'cart_items', 'total', 'item_count']


# ============================================================================
# USER & EMPLOYEE SERIALIZERS
# ============================================================================

class UserSerializer(serializers.ModelSerializer):
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'phone', 'is_active', 'full_name']
        read_only_fields = ['id', 'full_name']

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username


class EmployeeProfileSerializer(serializers.ModelSerializer):
    username = serializers.CharField(source='user.username', read_only=True)
    role = serializers.CharField(source='user.role', read_only=True)
    full_name = serializers.SerializerMethodField()

    class Meta:
        model = EmployeeProfile
        fields = [
            'id', 'user', 'username', 'full_name', 'role', 'employee_id',
            'phone', 'address', 'hire_date', 'hourly_rate', 'is_active'
        ]

    def get_full_name(self, obj):
        return obj.user.get_full_name() or obj.user.username


class AttendanceSerializer(serializers.ModelSerializer):
    employee_name = serializers.CharField(source='employee.username', read_only=True)
    hours_worked = serializers.ReadOnlyField()
    
    class Meta:
        model = Attendance
        fields = [
            'id', 'employee', 'employee_name', 'date', 'time_in',
            'time_out', 'hours_worked'
        ]
        read_only_fields = ['date', 'time_in']


# ============================================================================
# AUTH SERIALIZERS
# ============================================================================

class LoginSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField(write_only=True)
    
    def validate(self, data):
        user = authenticate(**data)
        if user and user.is_active:
            return user
        raise serializers.ValidationError("Invalid credentials")

class UserCreateSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True)
    
    class Meta:
        model = User
        fields = ['username', 'email', 'password', 'first_name', 'last_name', 'role', 'phone']
    
    def create(self, validated_data):
        user = User.objects.create_user(**validated_data)
        return user

class PaymentGatewayConfigSerializer(serializers.ModelSerializer):
    """Read-side serializer — never echoes the raw decrypted credentials.

    The three secret fields are reported only as `*_is_set` booleans so the UI
    can render a status badge without ever transporting the plaintext back to
    the browser.
    """
    merchant_id_is_set = serializers.SerializerMethodField()
    api_key_is_set = serializers.SerializerMethodField()
    api_secret_is_set = serializers.SerializerMethodField()

    class Meta:
        model = PaymentGatewayConfig
        fields = [
            'id', 'gateway', 'is_active', 'use_mock_mode',
            'webhook_url', 'enable_terminal', 'terminal_id',
            'merchant_id_is_set', 'api_key_is_set', 'api_secret_is_set',
            'created_at', 'updated_at',
        ]

    def get_merchant_id_is_set(self, obj):
        return bool(obj.merchant_id)

    def get_api_key_is_set(self, obj):
        return bool(obj.api_key)

    def get_api_secret_is_set(self, obj):
        return bool(obj.api_secret)

# ============================================================================
# SHIFT SERIALIZERS
# ============================================================================

class ShiftSerializer(serializers.ModelSerializer):
    cashier_name = serializers.CharField(source='cashier.username', read_only=True)

    class Meta:
        model = Shift
        fields = [
            'id', 'cashier', 'cashier_name', 'opened_at', 'closed_at',
            'opening_cash', 'closing_cash', 'is_open'
        ]
        read_only_fields = ['cashier', 'opened_at', 'closed_at', 'is_open']


# ============================================================================
# INGREDIENT SERIALIZERS
# ============================================================================

class IngredientUnitSerializer(serializers.ModelSerializer):
    class Meta:
        model = IngredientUnit
        fields = ['id', 'name', 'abbreviation', 'is_active']


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = ['id', 'name', 'contact_person', 'phone', 'address', 'notes', 'is_active']


class IngredientSerializer(serializers.ModelSerializer):
    unit_detail = IngredientUnitSerializer(source='unit', read_only=True)
    supplier_detail = SupplierSerializer(source='supplier', read_only=True)
    is_low_stock = serializers.ReadOnlyField()

    class Meta:
        model = Ingredient
        fields = [
            'id', 'name', 'unit', 'unit_detail', 'cost_per_unit',
            'current_stock', 'par_level', 'supplier', 'supplier_detail',
            'is_active', 'is_low_stock'
        ]


class IngredientRestockLogSerializer(serializers.ModelSerializer):
    ingredient_name = serializers.CharField(source='ingredient.name', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.username', read_only=True)

    class Meta:
        model = IngredientRestockLog
        fields = [
            'id', 'ingredient', 'ingredient_name', 'quantity_added',
            'cost_per_unit', 'date', 'notes', 'recorded_by', 'recorded_by_name'
        ]
        read_only_fields = ['ingredient', 'recorded_by', 'date']


class RecipeIngredientSerializer(serializers.ModelSerializer):
    ingredient_detail = IngredientSerializer(source='ingredient', read_only=True)

    class Meta:
        model = RecipeIngredient
        fields = ['id', 'item', 'variant', 'ingredient', 'ingredient_detail', 'quantity_used']


# ============================================================================
# BUSINESS PROFILE SERIALIZER
# ============================================================================

class BusinessProfileSerializer(serializers.ModelSerializer):
    """FEATURE-011-B: read + write for BusinessProfile, including the BIR
    machine/accreditation identity fields. No special validation yet —
    Session C/D consume these for receipt/Z rendering."""

    class Meta:
        model = BusinessProfile
        fields = [
            'id', 'business_name', 'tin',
            # BIR identity (Session B)
            'machine_identification_number',
            'machine_serial_number',
            'pos_accreditation_number',
            'pos_permit_number',
            'pos_accreditation_valid_until',
        ]
