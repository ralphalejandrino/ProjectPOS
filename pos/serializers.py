"""
Clean POS Serializers - No Legacy School Code
"""
from decimal import Decimal, ROUND_HALF_UP
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
    ZReport,
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
    # FEATURE-046: read-time ingredient-derived "makeable" stock. Computed
    # fields only — never a model field, never persisted (writes go through
    # ItemCreate/UpdateSerializer, which omit them, so a round-trip cannot
    # store them).
    makeable = serializers.SerializerMethodField()
    makeable_status = serializers.SerializerMethodField()
    # FEATURE-052: recipe-derived cost + live margin. A recipe item's per-unit
    # cost comes from its ingredients (single source of truth via the weighted-
    # average ingredient cost, FEATURE-051), NOT the manual purchase_price —
    # that manual field is used only for pure resale items. effective_cost picks
    # the right source; effective_margin[_pct] is the true margin off price.
    recipe_cost = serializers.SerializerMethodField()
    is_recipe_item = serializers.SerializerMethodField()
    effective_cost = serializers.SerializerMethodField()
    effective_margin = serializers.SerializerMethodField()
    effective_margin_pct = serializers.SerializerMethodField()

    def _makeable(self, obj):
        from .services import item_makeable
        return item_makeable(obj)

    def get_makeable(self, obj):
        return self._makeable(obj)[0]

    def get_makeable_status(self, obj):
        return self._makeable(obj)[1]

    def _recipe_cost(self, obj):
        # Memoize per object so the four margin fields sum the recipe only once.
        if not hasattr(obj, '_frc_cached'):
            from .services import item_recipe_cost
            obj._frc_cached = item_recipe_cost(obj)
        return obj._frc_cached

    def _effective_cost(self, obj):
        rc = self._recipe_cost(obj)
        return rc if rc is not None else obj.purchase_price

    def get_recipe_cost(self, obj):
        rc = self._recipe_cost(obj)
        return str(rc) if rc is not None else None

    def get_is_recipe_item(self, obj):
        return self._recipe_cost(obj) is not None

    def get_effective_cost(self, obj):
        c = self._effective_cost(obj)
        return str(c) if c is not None else None

    def get_effective_margin(self, obj):
        c = self._effective_cost(obj)
        if c is None:
            return None
        return str((obj.price - c).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP))

    def get_effective_margin_pct(self, obj):
        c = self._effective_cost(obj)
        if c is None or not obj.price or obj.price <= 0:
            return None
        pct = (obj.price - c) / obj.price * Decimal('100')
        return float(pct.quantize(Decimal('0.1'), rounding=ROUND_HALF_UP))

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
        # FLAG-049: resolution lives in the single canonical resolver shared
        # with the POS sale path; this serializer only shapes the output.
        from .services import resolve_effective_variant_groups
        return [
            {
                'group': VariantGroupSerializer(r['group']).data,
                'is_required': r['required'],
                'source': r['source'],
            }
            for r in resolve_effective_variant_groups(obj)
        ]

    class Meta:
        model = Item
        fields = [
            'id', 'name', 'category', 'category_name', 'price', 'purchase_price',
            'stock', 'low_stock_threshold', 'bar_code', 'bar_code_image',
            'photo', 'description', 'sku', 'expiry_date', 'is_active',
            'profit_margin', 'profit_per_unit', 'is_low_stock',
            'effective_variant_groups', 'makeable', 'makeable_status',
            # FEATURE-052: recipe-derived cost + live margin.
            'recipe_cost', 'is_recipe_item', 'effective_cost',
            'effective_margin', 'effective_margin_pct',
            'created_at', 'updated_at'
        ]


class ItemUpdateSerializer(serializers.ModelSerializer):
    """Handles PUT/PATCH with writable photo ImageField.

    `id` is returned (read-only) so the caller can act on the saved row — the
    inventory editor chains `saveProductVariantGroups(savedItem.id)` after the
    PATCH, and without an id in the response that call was silently skipped
    (variant enable/disable never persisted).
    """
    class Meta:
        model = Item
        fields = [
            'id', 'name', 'category', 'price', 'purchase_price', 'stock',
            'low_stock_threshold', 'bar_code', 'photo', 'description',
            'sku', 'expiry_date', 'is_active'
        ]
        read_only_fields = ['id']

class ItemCreateSerializer(serializers.ModelSerializer):
    class Meta:
        model = Item
        fields = [
            'id', 'name', 'category', 'price', 'purchase_price', 'stock',
            'low_stock_threshold', 'bar_code', 'photo', 'description',
            'sku', 'expiry_date', 'is_active'
        ]
        read_only_fields = ['id']


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
        fields = ['id', 'name', 'selection_type', 'is_required',
                  'min_selections', 'max_selections',
                  'sort_order', 'is_active', 'options']


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
            'transaction_type', 'refund_of',
            'discount_amount', 'discount_type', 'discount_id_number'
        ]


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
    effective_pages = serializers.SerializerMethodField()

    class Meta:
        model = User
        fields = ['id', 'username', 'email', 'first_name', 'last_name', 'role', 'phone',
                  'is_active', 'full_name', 'allowed_pages', 'effective_pages']
        read_only_fields = ['id', 'full_name', 'allowed_pages', 'effective_pages']

    def get_full_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip() or obj.username

    def get_effective_pages(self, obj):
        # FEATURE-044: resolved page set (role default + per-user override).
        from .access import effective_pages
        return sorted(effective_pages(obj))


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
    # FEATURE-061: computed SERVER-side on purpose. "Has this shift crossed
    # into another business day" is a timezone question, and the server holds
    # TIME_ZONE; letting the kiosk decide invites an off-by-one-day banner.
    spans_business_date = serializers.SerializerMethodField()
    hours_open = serializers.SerializerMethodField()

    class Meta:
        model = Shift
        fields = [
            'id', 'cashier', 'cashier_name', 'opened_at', 'closed_at',
            'opening_cash', 'closing_cash', 'is_open',
            'spans_business_date', 'hours_open',
        ]
        read_only_fields = ['cashier', 'opened_at', 'closed_at', 'is_open']

    def _elapsed(self, obj):
        from django.utils import timezone as dj_tz
        end = obj.closed_at or dj_tz.now()
        return end - obj.opened_at

    def get_spans_business_date(self, obj):
        from django.utils import timezone as dj_tz
        end = obj.closed_at or dj_tz.now()
        return dj_tz.localdate(end) != dj_tz.localdate(obj.opened_at)

    def get_hours_open(self, obj):
        return round(self._elapsed(obj).total_seconds() / 3600.0, 1)


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


class PreparationComponentSerializer(serializers.ModelSerializer):
    """FEATURE-056: one component line of a preparation's batch recipe."""
    component_name = serializers.CharField(source='component.name', read_only=True)
    component_unit = serializers.CharField(
        source='component.unit.abbreviation', read_only=True)
    component_cost = serializers.DecimalField(
        source='component.cost_per_unit', max_digits=10, decimal_places=4,
        read_only=True)

    class Meta:
        from .models import PreparationComponent
        model = PreparationComponent
        fields = ['id', 'component', 'component_name', 'component_unit',
                  'component_cost', 'quantity_used']


class IngredientSerializer(serializers.ModelSerializer):
    unit_detail = IngredientUnitSerializer(source='unit', read_only=True)
    # FEATURE-056: preparation (sub-recipe / BOM) surface.
    components = PreparationComponentSerializer(many=True, read_only=True)
    batch_cost_preview = serializers.SerializerMethodField()
    batch_unit_cost_preview = serializers.SerializerMethodField()

    def _batch_cost(self, obj):
        if not obj.is_preparation:
            return None
        total = Decimal('0')
        for c in obj.components.all():
            total += (c.quantity_used or Decimal('0')) * c.component.cost_per_unit
        return total.quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)

    def get_batch_cost_preview(self, obj):
        c = self._batch_cost(obj)
        return str(c) if c is not None else None

    def get_batch_unit_cost_preview(self, obj):
        c = self._batch_cost(obj)
        if c is None or not obj.batch_yield or obj.batch_yield <= 0:
            return None
        return str((c / obj.batch_yield).quantize(
            Decimal('0.0001'), rounding=ROUND_HALF_UP))
    # FEATURE-050: purchase (package) unit detail for the restock UI.
    purchase_unit_detail = IngredientUnitSerializer(source='purchase_unit', read_only=True)
    supplier_detail = SupplierSerializer(source='supplier', read_only=True)
    is_low_stock = serializers.ReadOnlyField()

    class Meta:
        model = Ingredient
        fields = [
            'id', 'name', 'unit', 'unit_detail', 'cost_per_unit',
            'current_stock', 'par_level', 'supplier', 'supplier_detail',
            'is_active', 'is_low_stock', 'track_depletion', 'updated_at',
            # FEATURE-050: purchasing-unit layer.
            'purchase_unit', 'purchase_unit_detail', 'purchase_to_base_factor',
            'last_purchase_price',
            # FEATURE-056: preparation (sub-recipe / BOM).
            'is_preparation', 'batch_yield', 'components',
            'batch_cost_preview', 'batch_unit_cost_preview',
        ]
        read_only_fields = ['updated_at']

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # ISSUE-071: current_stock is ledger-controlled. It may be seeded at
        # creation (opening balance), but PATCH/PUT must never write it
        # directly — that produced untracked stock changes. On update the
        # field becomes read-only; the only sanctioned path to move stock is
        # POST /ingredients/{id}/adjust/ (or restock), which writes an
        # IngredientLog. self.instance is set only when updating.
        if self.instance is not None and not isinstance(self.instance, (list, tuple)):
            self.fields['current_stock'].read_only = True


class IngredientRestockLogSerializer(serializers.ModelSerializer):
    ingredient_name = serializers.CharField(source='ingredient.name', read_only=True)
    recorded_by_name = serializers.CharField(source='recorded_by.username', read_only=True)
    # FEATURE-058: correction audit surface. A voided entry stays in the history
    # (struck through in the UI) with who/when; an edited entry carries the
    # corrected-by stamp. All read-only — corrections go through the dedicated
    # /restock-logs/{id}/void|edit|reattribute endpoints, never a bare PATCH.
    voided_by_name = serializers.CharField(source='voided_by.username', read_only=True)
    corrected_by_name = serializers.CharField(source='corrected_by.username', read_only=True)
    # FEATURE-050: package-based restock entry. When the manager buys whole
    # packages ("2 sacks @ ₱1,250"), she enters ``packages`` (+ optional
    # ``package_price``) and the POS converts to base units + per-base-unit cost
    # via the ingredient's purchase_to_base_factor — she never divides a sack
    # into grams. Both are write-only; the legacy base-unit path
    # (quantity_added [+ cost_per_unit]) still works unchanged.
    packages = serializers.DecimalField(
        max_digits=12, decimal_places=4, write_only=True, required=False,
    )
    package_price = serializers.DecimalField(
        max_digits=12, decimal_places=2, write_only=True, required=False,
    )

    class Meta:
        model = IngredientRestockLog
        fields = [
            'id', 'ingredient', 'ingredient_name', 'quantity_added',
            'cost_per_unit', 'date', 'notes', 'recorded_by', 'recorded_by_name',
            'packages', 'package_price',
            'is_voided', 'voided_at', 'voided_by_name',
            'corrected_at', 'corrected_by_name', 'correction_note',
        ]
        read_only_fields = [
            'ingredient', 'recorded_by', 'date',
            'is_voided', 'voided_at', 'corrected_at', 'correction_note',
        ]
        # FEATURE-050: quantity_added/cost_per_unit are no longer client-required
        # because they can be derived from packages. validate() enforces that one
        # of the two entry modes is fully provided.
        extra_kwargs = {
            'quantity_added': {'required': False},
            'cost_per_unit': {'required': False},
        }

    def validate_quantity_added(self, value):
        # ISSUE-113: restock must add stock; the model save() increments
        # current_stock by this amount unconditionally.
        if value <= 0:
            raise serializers.ValidationError(
                'Restock quantity must be greater than 0 '
                "(in the ingredient's unit)."
            )
        return value

    def validate_packages(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                'Number of packages must be greater than 0.'
            )
        return value

    def validate(self, attrs):
        # FEATURE-050: resolve the entry mode. Package mode wins when
        # ``packages`` is present; otherwise fall back to the base-unit path.
        ingredient = self.context.get('ingredient')
        packages = attrs.get('packages')

        if packages is not None:
            if ingredient is None or not ingredient.purchase_to_base_factor:
                raise serializers.ValidationError(
                    'This ingredient has no purchase unit / conversion set. Add '
                    'a purchase unit and how many base units it holds before '
                    'restocking by the package.'
                )
            factor = ingredient.purchase_to_base_factor
            attrs['quantity_added'] = packages * factor

            package_price = attrs.get('package_price')
            if package_price is None:
                package_price = ingredient.last_purchase_price
            if package_price is None:
                raise serializers.ValidationError(
                    'Enter the price per package (no previous price on file to '
                    'reuse).'
                )
            # Store the per-base-unit cost on the log — that is what the cost
            # math and the weekly restock report read. FEATURE-051 rolls this
            # into a weighted-average cost_per_unit on the ingredient.
            attrs['cost_per_unit'] = (
                Decimal(package_price) / Decimal(factor)
            ).quantize(Decimal('0.0001'))
            attrs['package_price'] = package_price
        else:
            if attrs.get('quantity_added') is None:
                raise serializers.ValidationError(
                    'Provide either "packages" (with a purchase unit set on the '
                    'ingredient) or "quantity_added" in the base unit.'
                )
            if attrs.get('cost_per_unit') is None:
                # Default to the ingredient's current known cost.
                if ingredient is None:
                    raise serializers.ValidationError(
                        '"cost_per_unit" is required.'
                    )
                attrs['cost_per_unit'] = ingredient.cost_per_unit
        return attrs

    def create(self, validated_data):
        # ``packages``/``package_price`` are entry-only, not model fields.
        packages = validated_data.pop('packages', None)
        package_price = validated_data.pop('package_price', None)
        log = super().create(validated_data)
        # Remember the latest package price so the next restock can prefill it.
        if packages is not None and package_price is not None:
            ing = log.ingredient
            ing.last_purchase_price = package_price
            ing.save(update_fields=['last_purchase_price'])
        return log


# ---------------------------------------------------------------------------
# FEATURE-058: correction inputs. Thin validation shells over the services —
# they never write anything themselves (void/edit/reattribute_restock own the
# transaction, stock delta, ledger row, and cost recompute).
# ---------------------------------------------------------------------------

class RestockVoidInputSerializer(serializers.Serializer):
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='')


class RestockEditInputSerializer(serializers.Serializer):
    quantity_added = serializers.DecimalField(
        max_digits=10, decimal_places=4, required=False)
    cost_per_unit = serializers.DecimalField(
        max_digits=10, decimal_places=4, required=False)
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='')

    def validate_quantity_added(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                'Corrected quantity must be greater than 0 — void the entry '
                'instead if the restock never happened.')
        return value

    def validate_cost_per_unit(self, value):
        # A zero price would silently re-create the zero-cost-snapshot disease
        # (#10): the line "has a cost" but contributes nothing to COGS.
        if value <= 0:
            raise serializers.ValidationError(
                'Corrected price must be greater than 0.')
        return value

    def validate(self, attrs):
        if attrs.get('quantity_added') is None and attrs.get('cost_per_unit') is None:
            raise serializers.ValidationError(
                'Nothing to edit — provide a corrected quantity and/or price.')
        return attrs


class RestockReattributeInputSerializer(serializers.Serializer):
    # Active-only on purpose: re-attributing onto a retired duplicate copy would
    # re-create the split-record tangle the 2026-07-18 structural fix undid.
    # Preparations excluded too (v1 scope) — their cost is production-derived,
    # not purchase-derived; services carry the same guard for the source side.
    target_ingredient = serializers.PrimaryKeyRelatedField(
        queryset=Ingredient.objects.filter(is_active=True, is_preparation=False))
    quantity_added = serializers.DecimalField(max_digits=10, decimal_places=4)
    cost_per_unit = serializers.DecimalField(max_digits=10, decimal_places=4)
    reason = serializers.CharField(
        required=False, allow_blank=True, max_length=255, default='')

    def validate_quantity_added(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                "Quantity must be greater than 0, in the target ingredient's "
                'own unit.')
        return value

    def validate_cost_per_unit(self, value):
        if value <= 0:
            raise serializers.ValidationError(
                'Price must be greater than 0.')
        return value


class RecipeIngredientSerializer(serializers.ModelSerializer):
    ingredient_detail = IngredientSerializer(source='ingredient', read_only=True)

    class Meta:
        model = RecipeIngredient
        fields = ['id', 'item', 'variant', 'ingredient', 'ingredient_detail',
                  'quantity_used', 'depletion_mode',
                  # FEATURE-046 / ISSUE-122
                  'entry_unit', 'entry_quantity']
        # quantity_used is DERIVED whenever entry_* are supplied (model.save),
        # so it must stay writable for direct base-unit entry but is never the
        # thing the client sends alongside an entry unit.
        extra_kwargs = {
            'entry_unit': {'required': False, 'allow_null': True},
            'entry_quantity': {'required': False, 'allow_null': True},
        }

    def validate_quantity_used(self, value):
        # ISSUE-113: a zero/negative per-serving quantity silently disables or
        # inverts depletion. quantity_used is ALWAYS in the ingredient's base
        # unit — FEATURE-046 lets the user TYPE another unit, but conversion
        # happens on the way in so this field's meaning never changed.
        if value <= 0:
            raise serializers.ValidationError(
                'Quantity per serving must be greater than 0 '
                "(in the ingredient's unit)."
            )
        return value

    def validate(self, attrs):
        """FEATURE-046: surface a missing conversion as a 400 with a usable
        message, instead of letting the model raise and become a 500."""
        unit = attrs.get('entry_unit')
        qty = attrs.get('entry_quantity')
        if (unit is None) != (qty is None):
            raise serializers.ValidationError(
                'entry_unit and entry_quantity must be supplied together.'
            )
        if unit is not None:
            ingredient = attrs.get('ingredient') or getattr(
                self.instance, 'ingredient', None)
            if ingredient is not None:
                from .services import convert_to_base_units, UnitConversionError
                try:
                    convert_to_base_units(ingredient, qty, unit)
                except UnitConversionError as e:
                    raise serializers.ValidationError({'entry_unit': str(e)})
        return attrs

    def validate(self, attrs):
        # BUG-013: every recipe line is owned by an Item (recipe_item_required).
        # ``variant`` null = the item's base recipe; ``variant`` set = a recipe
        # specific to that item + variant option. Requiring the item is what
        # keeps variant recipes per-item: the VariantOption is shared across
        # products, so without the owning item a variant line bled onto every
        # item that shared the option (supersedes BUG-001's item/variant XOR).
        # On update, fall back to the existing instance when the payload omits
        # ``item``. Returns 400 (not an uncaught IntegrityError → 500).
        item = attrs.get('item') if 'item' in attrs else getattr(self.instance, 'item', None)
        if not item:
            raise serializers.ValidationError(
                'A recipe ingredient must belong to an "item". Set "item"; '
                'add "variant" too to scope the line to a specific variant.'
            )

        # FLAG-050: reject a variant recipe that would make the same ingredient
        # resolve from two different variant groups co-occurring on one sale
        # (their quantities would otherwise silently add at depletion time).
        variant = attrs.get('variant') or getattr(self.instance, 'variant', None)
        ingredient = attrs.get('ingredient') or getattr(self.instance, 'ingredient', None)
        if variant and ingredient:
            from .services import variant_ingredient_conflict
            conflict = variant_ingredient_conflict(
                variant, ingredient,
                exclude_pk=self.instance.pk if self.instance else None,
            )
            if conflict is not None:
                raise serializers.ValidationError(
                    f"'{ingredient.name}' is already used by variant option "
                    f"'{conflict.group.name} — {conflict.name}', which can be "
                    f"selected on the same item. Two variant groups contributing "
                    f"the same ingredient would double-count it at sale time."
                )
        return attrs


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
            # ISSUE-099: printer transport + paper calibration. ModelSerializer
            # validates each against the model's choices automatically.
            'printer_mode',
            'paper_width',
            'printer_font',
        ]


# ============================================================================
# Z-REPORT SERIALIZER (FEATURE-011-C — read-only snapshot)
# ============================================================================

class ZReportSerializer(serializers.ModelSerializer):
    """Read-only. ZReport is immutable; Decimals serialize as strings so
    no float precision is lost. Currency formatting is a presentation
    concern (Session D), not done here."""

    cashier_username = serializers.CharField(
        source='cashier.username', read_only=True
    )
    # FEATURE-008: per-ingredient sold/voided for the closed shift, read live
    # from the IngredientLog ledger at serialize time — no ZReport schema change.
    stock_movements = serializers.SerializerMethodField()

    class Meta:
        model = ZReport
        fields = '__all__'
        read_only_fields = [f.name for f in ZReport._meta.fields]

    def get_stock_movements(self, obj):
        from .services import stock_movements_for_shift
        return stock_movements_for_shift(getattr(obj, 'shift', None))
