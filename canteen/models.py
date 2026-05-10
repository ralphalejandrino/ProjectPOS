import io
import uuid
from decimal import Decimal
from barcode import Code128
from barcode.writer import ImageWriter
from django.core.files.base import ContentFile
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.utils import timezone as dj_tz
from djmoney.models.fields import MoneyField


# ============================================================================
# BASE MODELS & UTILITIES
# ============================================================================

class BaseModelWithUUID(models.Model):
    """Abstract base model with UUID primary key and timestamps"""
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        abstract = True


def name_path_file(directory, filename):
    """Generate unique filename for uploads"""
    ext = filename.split('.')[-1]
    filename = f"{uuid.uuid4()}.{ext}"
    return f"{directory}/{filename}"


def upload_to_item(instance, filename):
    return name_path_file('images/canteen/item', filename)


def upload_to_item_barcode(instance, filename):
    return name_path_file('images/canteen/item/barcode', filename)


# ============================================================================
# PRODUCT MANAGEMENT
# ============================================================================

class ItemCategory(BaseModelWithUUID):
    """Product categories (Food, Beverages, Snacks, etc.)"""
    name = models.CharField(max_length=255, verbose_name="Item Category")
    emoji = models.CharField(max_length=10, blank=True, default='', verbose_name="Category Emoji")
    description = models.TextField(blank=True, null=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        verbose_name_plural = "Item Categories"
        ordering = ['name']

    def __str__(self):
        return self.name


class VariantGroup(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    name = models.CharField(max_length=100)
    selection_type = models.CharField(
        max_length=10,
        choices=[('single', 'Single'), ('multi', 'Multi')],
        default='single',
    )
    is_required = models.BooleanField(default=False)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return self.name


class VariantOption(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    group = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name='options')
    name = models.CharField(max_length=100)
    price_modifier = models.DecimalField(max_digits=8, decimal_places=2, default=0)
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def __str__(self):
        return f"{self.group.name} — {self.name}"


class CategoryVariantGroup(models.Model):
    category = models.ForeignKey('ItemCategory', on_delete=models.CASCADE, related_name='variant_groups')
    group = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name='category_assignments')
    is_required_override = models.BooleanField(null=True, blank=True)

    class Meta:
        unique_together = [('category', 'group')]

    def __str__(self):
        return f"{self.category.name} → {self.group.name}"


class ProductVariantGroup(models.Model):
    product = models.ForeignKey('Item', on_delete=models.CASCADE, related_name='variant_group_overrides')
    group = models.ForeignKey(VariantGroup, on_delete=models.CASCADE, related_name='product_overrides')
    enabled = models.BooleanField(default=True)
    is_required_override = models.BooleanField(null=True, blank=True)

    class Meta:
        unique_together = [('product', 'group')]

    def __str__(self):
        return f"{self.product.name} → {self.group.name} ({'on' if self.enabled else 'off'})"


class Item(BaseModelWithUUID):
    """Products/Items for sale"""
    category = models.ForeignKey(
        to=ItemCategory,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        verbose_name="Item Category",
        related_name="items"
    )
    name = models.CharField(max_length=255, null=True, blank=True, verbose_name="Item Name")
    bar_code = models.CharField(max_length=255, null=True, blank=True, verbose_name="Product Bar Code")
    bar_code_image = models.ImageField(
        null=True,
        blank=True,
        upload_to=upload_to_item_barcode,
        verbose_name="Item Barcode"
    )
    price = models.DecimalField(max_digits=10, decimal_places=2, default=0, verbose_name="Selling Price")
    purchase_price = models.DecimalField(
        decimal_places=2,
        max_digits=10,
        null=True,
        blank=True,
        verbose_name="Cost Price"
    )
    stock = models.PositiveIntegerField(default=0, verbose_name="Current Stock")
    low_stock_threshold = models.PositiveIntegerField(default=10, verbose_name="Low Stock Alert Level")
    photo = models.ImageField(blank=True, upload_to=upload_to_item, verbose_name="Item Photo")
    description = models.TextField(blank=True, null=True, verbose_name="Item Description")
    sku = models.CharField(max_length=100, blank=True, null=True, verbose_name="SKU Code")
    expiry_date = models.DateField(blank=True, null=True, verbose_name="Expiry Date")
    is_active = models.BooleanField(default=True, verbose_name="Active")

    @property
    def profit_margin(self):
        """Returns profit margin percentage"""
        if self.purchase_price and self.purchase_price > 0:
            return ((float(self.price) - float(self.purchase_price)) / float(self.purchase_price)) * 100
        return 0

    @property
    def profit_per_unit(self):
        """Returns profit per unit"""
        if self.purchase_price:
            return float(self.price) - float(self.purchase_price)
        return float(self.price)

    @property
    def is_low_stock(self):
        """Check if item is below low stock threshold"""
        return self.stock <= self.low_stock_threshold

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name}"

    def save(self, *args, **kwargs):
        # Auto-generate barcode image if bar_code is provided
        if self.bar_code:
            buffer = io.BytesIO()
            barcode_obj = Code128(self.bar_code, writer=ImageWriter())
            barcode_obj.write(buffer)
            buffer.seek(0)
            filename = f"{self.bar_code}.png"
            self.bar_code_image.save(filename, ContentFile(buffer.read()), save=False)

        super().save(*args, **kwargs)


class ItemLog(BaseModelWithUUID):
    """Inventory tracking log - records all stock changes"""
    item = models.ForeignKey(to=Item, on_delete=models.CASCADE, verbose_name="Item", related_name="logs")
    price = models.DecimalField(max_digits=10, decimal_places=2, null=True, blank=True, verbose_name="Price")
    purchase_price = models.DecimalField(
        decimal_places=2,
        max_digits=10,
        null=True,
        blank=True,
        verbose_name="Purchase Price"
    )
    quantity = models.IntegerField(verbose_name="Quantity Change")  # Can be negative
    current_stock = models.IntegerField(null=True, verbose_name="Stock After Change")
    action = models.CharField(
        max_length=255,
        verbose_name="Action",
        choices=[
            ('restock', 'Restock'),
            ('sale', 'Sale'),
            ('adjustment', 'Manual Adjustment'),
            ('damage', 'Damaged/Expired'),
            ('return', 'Customer Return'),
        ]
    )
    remarks = models.TextField(help_text="Action remarks", null=True, blank=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='item_logs'
    )

    class Meta:
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.item.name} - {self.action} ({self.quantity})"


# ============================================================================
# TRANSACTIONS
# ============================================================================

class OfficialReceiptCounter(models.Model):
    """Atomic per-day OR number counter. One row per calendar day (PHT)."""
    date = models.DateField(unique=True)
    counter = models.PositiveIntegerField(default=0)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        indexes = [
            models.Index(fields=['date']),
        ]

    def __str__(self):
        return f"OR counter for {self.date}: {self.counter}"


class Transaction(BaseModelWithUUID):
    """Base transaction model"""
    transaction_no = models.CharField(
        max_length=255,
        null=True,
        blank=True,
        unique=True,
        verbose_name="Transaction Number"
    )
    void = models.BooleanField(default=False)
    purpose_of_void = models.TextField(help_text="Purpose of void", null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)
    total_amount = MoneyField(max_digits=10, decimal_places=2, default=0)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='created_transactions'
    )

    class Meta:
        abstract = True
        ordering = ['-created_at']

    def __str__(self):
        raise NotImplementedError(f"__str__() should be defined for {self.__class__.__name__}")


class PosTransaction(Transaction):
    """Point of Sale transaction"""

    TRANSACTION_STATUS = [
        ('completed', 'Completed'),
        ('pending', 'Pending'),
        ('void', 'Void'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('gcash', 'GCash'),
        ('maya', 'Maya'),
        ('card', 'Card'),
    ]

    # Payment details
    payment_method = models.CharField(
        max_length=20,
        choices=PAYMENT_METHOD_CHOICES,
        default='cash',
        verbose_name="Payment Method"
    )
    
    # Cash payment fields
    cash_received = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Amount of cash received from customer"
    )
    change_given = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        null=True,
        blank=True,
        help_text="Change given to customer"
    )
    
    # GCash payment fields
    gcash_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="GCash transaction reference number"
    )
    
    # Maya payment fields
    maya_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Maya transaction reference number"
    )
    
    # Card payment fields
    card_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        help_text="Card transaction reference number"
    )
    card_type = models.CharField(
        max_length=50,
        null=True,
        blank=True,
        help_text="Card type (Visa, Mastercard, etc.)"
    )
    
    # Transaction metadata
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='cashier_transactions',
        verbose_name="Cashier"
    )
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='voided_transactions',
        verbose_name="Voided By"
    )
    status = models.CharField(
        max_length=20,
        choices=TRANSACTION_STATUS,
        default='completed',
        verbose_name="Status"
    )
    
    # Barcode for receipt
    bar_code = models.CharField(max_length=255, null=True, blank=True, verbose_name="Transaction Bar Code")
    bar_code_image = models.ImageField(
        null=True,
        blank=True,
        upload_to=upload_to_item_barcode,
        verbose_name="Transaction Barcode"
    )
    
    # Customer info (optional)
    customer_name = models.CharField(max_length=255, blank=True, null=True, verbose_name="Customer Name")
    customer_phone = models.CharField(max_length=20, blank=True, null=True, verbose_name="Customer Phone")

    # Discount fields (restored — migration 0009)
    discount_amount = models.DecimalField(
        decimal_places=2,
        default=0,
        max_digits=10,
        help_text='Discount applied to this transaction'
    )
    discount_type = models.CharField(
        blank=True,
        choices=[
            ('', 'None'),
            ('fixed', 'Fixed Amount'),
            ('percentage', 'Percentage'),
            ('sc', 'Senior Citizen (20%)'),
            ('pwd', 'PWD (20%)'),
            ('promo', 'Promo'),
        ],
        default='',
        help_text='Type of discount applied',
        max_length=20,
    )
    discount_id_number = models.CharField(
        blank=True,
        default='',
        help_text='SC/PWD ID number for audit trail',
        max_length=50
    )
    vat_exempt = models.BooleanField(
        default=False,
        help_text='True when transaction is VAT-exempt (SC/PWD under RA 9994 / RA 10754)',
    )
    vat_amount = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='VAT amount removed from transaction total (0.00 for non-exempt or VAT-disabled)',
    )
    shift = models.ForeignKey(
        'Shift',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='transactions'
    )
    voided_at = models.DateTimeField(blank=True, null=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['transaction_no']),
            models.Index(fields=['payment_method']),
            models.Index(fields=['status'], name='canteen_pos_status_idx'),
            models.Index(fields=['shift'], name='canteen_pos_shift_idx'),
        ]

    def __str__(self):
        return f"POS Transaction {self.transaction_no}"

    def save(self, *args, **kwargs):
        if not self.transaction_no:
            self.transaction_no = self._generate_or_number()
        super().save(*args, **kwargs)

    @staticmethod
    def _generate_or_number():
        from django.db import transaction as db_tx
        from datetime import timezone as dt_tz, timedelta
        from django.utils import timezone as dj_tz
        PHT = dt_tz(timedelta(hours=8))
        today = dj_tz.now().astimezone(PHT).date()
        with db_tx.atomic():
            counter_obj, _ = OfficialReceiptCounter.objects.select_for_update().get_or_create(
                date=today,
                defaults={'counter': 0},
            )
            counter_obj.counter += 1
            counter_obj.save(update_fields=['counter', 'updated_at'])
            return f'OR-{today.strftime("%Y%m%d")}-{counter_obj.counter:04d}'


class PosTransactionItem(BaseModelWithUUID):
    """Line items in a POS transaction"""
    pos_transaction = models.ForeignKey(
        to=PosTransaction,
        on_delete=models.CASCADE,
        verbose_name="Transaction",
        related_name="items"
    )
    item = models.ForeignKey(to=Item, on_delete=models.PROTECT, verbose_name="Item")
    quantity = models.PositiveIntegerField(default=1, verbose_name="Quantity")
    unit_price = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Unit Price"
    )
    purchase_price = models.DecimalField(
        decimal_places=2,
        max_digits=10,
        null=True,
        blank=True,
        verbose_name="Cost Price"
    )
    subtotal = models.DecimalField(
        max_digits=10,
        decimal_places=2,
        verbose_name="Subtotal"
    )
    base_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    final_price = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-calculate subtotal
        if self.subtotal is None:
            self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item.name} x{self.quantity} = ₱{self.subtotal}"


class TransactionItemVariant(models.Model):
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transaction_item = models.ForeignKey(
        'PosTransactionItem', on_delete=models.CASCADE, related_name='variant_selections'
    )
    group_name = models.CharField(max_length=100)
    option_name = models.CharField(max_length=100)
    price_modifier = models.DecimalField(max_digits=8, decimal_places=2)

    def __str__(self):
        return f"{self.group_name}: {self.option_name}"


# ============================================================================
# SHOPPING CART (for building orders)
# ============================================================================

class Cart(BaseModelWithUUID):
    """Shopping cart for building orders before checkout"""
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        verbose_name="User/Cashier",
        related_name="cart"
    )
    session_id = models.CharField(max_length=255, blank=True, null=True, verbose_name="Session ID")

    def __str__(self):
        return f"Cart - {self.user.username}"

    @property
    def total(self):
        return sum(item.subtotal for item in self.cart_items.all())

    @property
    def item_count(self):
        return sum(item.quantity for item in self.cart_items.all())


class CartItem(BaseModelWithUUID):
    """Items in a shopping cart"""
    cart = models.ForeignKey(to=Cart, on_delete=models.CASCADE, related_name='cart_items')
    item = models.ForeignKey(to=Item, on_delete=models.CASCADE, verbose_name="Item")
    quantity = models.PositiveIntegerField(default=1)

    class Meta:
        unique_together = ['cart', 'item']

    @property
    def price(self):
        return self.item.price if self.item else 0

    @property
    def subtotal(self):
        return self.price * self.quantity

    def __str__(self):
        return f"{self.item.name} x{self.quantity} = ₱{self.subtotal}"


# ============================================================================
# PAYMENT CONFIGURATION
# ============================================================================

class PaymentGatewayConfig(models.Model):
    """Payment gateway configuration - stores API credentials"""

    GATEWAY_CHOICES = [
        ('gcash', 'GCash'),
        ('maya', 'Maya'),
    ]

    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, unique=True)
    is_active = models.BooleanField(default=True)
    use_mock_mode = models.BooleanField(default=True)  # Toggle between mock and real API

    # API Credentials
    merchant_id = models.CharField(max_length=255, blank=True, null=True)
    api_key = models.CharField(max_length=255, blank=True, null=True)
    api_secret = models.CharField(max_length=255, blank=True, null=True)
    webhook_url = models.CharField(max_length=500, blank=True, null=True)

    # Maya Terminal specific
    enable_terminal = models.BooleanField(default=False)
    terminal_id = models.CharField(max_length=255, blank=True, null=True)

    # QR Code image
    qr_image = models.ImageField(upload_to='qr/', blank=True, null=True)

    # Metadata
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Payment Gateway Config'
        verbose_name_plural = 'Payment Gateway Configs'

    def __str__(self):
        mode = "Mock" if self.use_mock_mode else "Real"
        status = "Active" if self.is_active else "Inactive"
        return f"{self.get_gateway_display()} - {mode} ({status})"


# ============================================================================
# EMPLOYEE MANAGEMENT
# ============================================================================

class EmployeeProfile(models.Model):
    """Extended employee information"""
    
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('cashier', 'Cashier'),
    ]

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='employee_profile'
    )
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    employee_id = models.CharField(max_length=50, unique=True)
    phone = models.CharField(max_length=20, blank=True)
    address = models.TextField(blank=True)
    hire_date = models.DateField(auto_now_add=True)
    hourly_rate = models.DecimalField(max_digits=8, decimal_places=2, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} - {self.role}"


class Attendance(models.Model):
    """Employee attendance tracking"""
    employee = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='attendances'
    )
    date = models.DateField(auto_now_add=True)
    time_in = models.DateTimeField(auto_now_add=True)
    time_out = models.DateTimeField(blank=True, null=True)

    class Meta:
        unique_together = ['employee', 'date']
        ordering = ['-date', '-time_in']

    def __str__(self):
        return f"{self.employee.username} - {self.date}"

    @property
    def hours_worked(self):
        """Calculate hours worked"""
        if self.time_out:
            delta = self.time_out - self.time_in
            return round(delta.total_seconds() / 3600, 2)
        return 0


# ============================================================================
# CUSTOM USER MODEL
# ============================================================================

class User(AbstractUser):
    """Custom user model with role field"""
    
    ROLE_CHOICES = [
        ('admin', 'Admin'),
        ('manager', 'Manager'),
        ('cashier', 'Cashier'),
    ]

    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='cashier')
    phone = models.CharField(max_length=20, blank=True)

    class Meta:
        db_table = 'users'

    def __str__(self):
        return f"{self.username} ({self.get_role_display()})"


# ============================================================================
# SHIFT MODEL
# ============================================================================

class BusinessProfile(models.Model):
    business_name = models.CharField(max_length=100, default='My Store')
    tagline = models.CharField(max_length=200, default='Point of Sale System')
    logo = models.ImageField(upload_to='business/', blank=True, null=True)
    contact_number = models.CharField(max_length=20, blank=True, default='')
    email = models.EmailField(blank=True, default='')
    address = models.TextField(blank=True, default='')
    receipt_header = models.CharField(max_length=200, blank=True, default='Thank you for your purchase!')
    receipt_footer = models.CharField(max_length=200, blank=True, default='Please come again!')
    low_stock_threshold = models.PositiveIntegerField(default=10, verbose_name='Low Stock Alert Level')
    printer_ip = models.GenericIPAddressField(blank=True, null=True, verbose_name='Receipt Printer IP')
    printer_port = models.PositiveIntegerField(default=9100, verbose_name='Printer Port')
    printer_enabled = models.BooleanField(default=False, verbose_name='Printer Enabled')
    color_scheme = models.CharField(max_length=7, default='#1d4ed8')
    updated_at = models.DateTimeField(auto_now=True)
    # Migration 0002
    tin = models.CharField(max_length=30, blank=True, default='', verbose_name='TIN')
    # Migration 0003
    discounts_enabled = models.BooleanField(default=False, verbose_name='Discounts Enabled')
    track_inventory = models.BooleanField(default=True, verbose_name='Track Inventory')
    vat_enabled = models.BooleanField(default=False, verbose_name='VAT Enabled')
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=12.0, verbose_name='VAT Rate (%)')
    # Per-type discount configuration (restored — migration 0009)
    sc_discount_enabled = models.BooleanField(default=True, verbose_name='SC Discount Enabled')
    sc_discount_rate = models.DecimalField(
        decimal_places=2, default=20.0, max_digits=5, verbose_name='SC Discount Rate (%)'
    )
    pwd_discount_enabled = models.BooleanField(default=True, verbose_name='PWD Discount Enabled')
    pwd_discount_rate = models.DecimalField(
        decimal_places=2, default=20.0, max_digits=5, verbose_name='PWD Discount Rate (%)'
    )
    promo_discount_enabled = models.BooleanField(default=False, verbose_name='Promo Discount Enabled')

    @classmethod
    def get_instance(cls):
        instance = cls.objects.first()
        if not instance:
            instance = cls.objects.create()
        return instance

    class Meta:
        db_table = 'business_profile'

    def __str__(self):
        return self.business_name


class Shift(models.Model):
    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='shifts'
    )
    opened_at = models.DateTimeField(auto_now_add=True)
    closed_at = models.DateTimeField(blank=True, null=True)
    opening_cash = models.DecimalField(max_digits=10, decimal_places=2, default=0)
    closing_cash = models.DecimalField(max_digits=10, decimal_places=2, blank=True, null=True)
    is_open = models.BooleanField(default=True)

    def __str__(self):
        return f"Shift {self.id} — {self.cashier.username} ({'open' if self.is_open else 'closed'})"


# ============================================================================
# INGREDIENT INVENTORY
# ============================================================================

class IngredientUnit(models.Model):
    """Units of measurement for ingredients (g, kg, ml, l, pcs, etc.)"""
    name = models.CharField(max_length=50, unique=True)
    abbreviation = models.CharField(max_length=10, unique=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return f"{self.name} ({self.abbreviation})"


class Supplier(models.Model):
    """Suppliers for ingredients"""
    name = models.CharField(max_length=255)
    contact_person = models.CharField(max_length=255, blank=True)
    phone = models.CharField(max_length=50, blank=True)
    address = models.TextField(blank=True)
    notes = models.TextField(blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class Ingredient(models.Model):
    """Ingredients used in recipes and food preparation"""
    name = models.CharField(max_length=255)
    unit = models.ForeignKey(IngredientUnit, on_delete=models.PROTECT)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=4)
    current_stock = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    par_level = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['name']

    @property
    def is_low_stock(self):
        """Check if ingredient is below par level"""
        return self.current_stock <= self.par_level

    def __str__(self):
        return f"{self.name} ({self.unit.abbreviation})"


class IngredientRestockLog(models.Model):
    """Log of ingredient restocking events"""
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name='restock_logs')
    quantity_added = models.DecimalField(max_digits=10, decimal_places=4)
    cost_per_unit = models.DecimalField(max_digits=10, decimal_places=4)
    date = models.DateTimeField(default=dj_tz.now)
    notes = models.TextField(blank=True)
    recorded_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL)

    class Meta:
        ordering = ['-date']

    def save(self, *args, **kwargs):
        # Update ingredient stock when saving restock log
        super().save(*args, **kwargs)
        self.ingredient.current_stock += self.quantity_added
        self.ingredient.save(update_fields=['current_stock'])

    def __str__(self):
        return f"+{self.quantity_added} {self.ingredient.unit.abbreviation} of {self.ingredient.name}"


class RecipeIngredient(models.Model):
    """Ingredients used in recipes for menu items"""
    item = models.ForeignKey('Item', null=True, blank=True, on_delete=models.CASCADE, related_name='recipe_ingredients')
    variant = models.ForeignKey('VariantOption', null=True, blank=True, on_delete=models.CASCADE, related_name='recipe_ingredients')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name='recipes')
    quantity_used = models.DecimalField(max_digits=10, decimal_places=4)

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=(
                    (models.Q(item__isnull=False) & models.Q(variant__isnull=True)) |
                    (models.Q(item__isnull=True) & models.Q(variant__isnull=False))
                ),
                name='recipe_item_or_variant_not_both'
            )
        ]

    def __str__(self):
        return f"{self.ingredient.name} x{self.quantity_used} for {self.item or self.variant}"
