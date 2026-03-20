import io
import uuid
from barcode import Code128
from barcode.writer import ImageWriter
from django.core.files.base import ContentFile
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
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


class ItemVariant(BaseModelWithUUID):
    """Size/flavor/option variants for an Item — each has its own selling price"""
    item = models.ForeignKey(
        to=Item,
        on_delete=models.CASCADE,
        related_name='variants',
        verbose_name="Item"
    )
    name = models.CharField(max_length=100, verbose_name="Variant Name")  # e.g. "Small", "Large", "Hot"
    price = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Variant Price")
    is_active = models.BooleanField(default=True)
    sort_order = models.PositiveSmallIntegerField(default=0, verbose_name="Display Order")

    class Meta:
        ordering = ['sort_order', 'name']
        unique_together = [['item', 'name']]

    def __str__(self):
        return f"{self.item.name} — {self.name} (₱{self.price})"


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
        # Auto-generate transaction number if not provided
        if not self.transaction_no:
            from django.utils import timezone as _tz
            timestamp = _tz.localtime(_tz.now()).strftime('%Y%m%d%H%M%S')
            self.transaction_no = f"TXN-{timestamp}-{uuid.uuid4().hex[:6].upper()}"
        super().save(*args, **kwargs)


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
    variant = models.ForeignKey(
        'ItemVariant',
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='transaction_items',
        verbose_name="Variant"
    )
    variant_name = models.CharField(
        max_length=100,
        blank=True,
        default='',
        verbose_name="Variant Name (snapshot)"
    )
    remarks = models.TextField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-calculate subtotal
        if not self.subtotal:
            self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item.name} x{self.quantity} = ₱{self.subtotal}"


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
