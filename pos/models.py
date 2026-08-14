import io
import uuid
from decimal import Decimal, ROUND_HALF_UP
from barcode import Code128
from barcode.writer import ImageWriter
from django.core.files.base import ContentFile
from django.db import models
from django.contrib.auth.models import AbstractUser
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MinValueValidator
from django.utils import timezone as dj_tz
from djmoney.models.fields import MoneyField

from .fields import FernetEncryptedField
from .utils.currency import format_currency

from .validators import (
    validate_non_negative_price,
    validate_non_negative_quantity,
)


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
    return name_path_file('images/pos/item', filename)


def upload_to_item_barcode(instance, filename):
    return name_path_file('images/pos/item/barcode', filename)


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
    # FEATURE-010: multi-select cardinality. Both nullable — null means
    # unconstrained. Only enforced when selection_type == 'multi' (see
    # services.create_pos_transaction and clean()).
    min_selections = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Min selections required (multi only)",
    )
    max_selections = models.PositiveIntegerField(
        null=True, blank=True,
        help_text="Max selections allowed (multi only)",
    )
    sort_order = models.PositiveSmallIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ['sort_order', 'name']

    def clean(self):
        # FEATURE-010: an impossible window (min > max) is rejected at the
        # model level so it can never be persisted via admin/form/full_clean.
        super().clean()
        if (
            self.min_selections is not None
            and self.max_selections is not None
            and self.min_selections > self.max_selections
        ):
            raise ValidationError(
                "min_selections cannot be greater than max_selections."
            )

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
    price = models.DecimalField(
        max_digits=10, decimal_places=2, default=0, verbose_name="Selling Price",
        validators=[validate_non_negative_price],
    )
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
    # FEATURE-034: VAT-exempt (zero-rated) item. When True, the line's full
    # sale amount is booked to PosTransaction.zero_rated_sales and carries no
    # output VAT (it is removed from the VAT-able base in
    # services.create_pos_transaction). Used for unprocessed food and other
    # zero-rated goods under the NIRC.
    zero_rated = models.BooleanField(
        default=False,
        verbose_name="Zero-Rated (VAT-exempt)",
        help_text="VAT-exempt item. Sale amount goes to zero_rated_sales; VAT is 0.",
    )

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
    """Atomic OR number counter, one row per calendar day (PHT) for audit/history.

    FLAG-057: the ``counter`` value is installation-wide *monotonic* — it is NOT
    reset to 0 on a new day. Each new day's row continues from the global
    high-water mark, so the NNNN suffix of an OR number never repeats across the
    lifetime of the installation (required for BIR Form 1900 / CSET evaluation).
    The human-readable format stays ``OR-YYYYMMDD-NNNN``; only the date segment
    rolls over daily, NNNN keeps climbing.
    """
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
        ('refunded', 'Refunded'),
    ]

    # FEATURE-015: a refund is distinct from a void. A void cancels a sale in
    # the same shift (fully reverses, mutates the original's void bookkeeping).
    # A refund leaves the original untouched and immutable and posts a NEW
    # negative transaction to the current shift. transaction_type is the
    # canonical discriminator; the legacy `void` flag and `status` are kept for
    # backward compatibility with existing code and queries.
    TRANSACTION_TYPE_CHOICES = [
        ('sale', 'Sale'),
        ('void', 'Void'),
        ('refund', 'Refund'),
    ]

    PAYMENT_METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('gcash', 'GCash'),
        ('maya', 'Maya'),
        ('card', 'Card'),
        # FEATURE-059: see PaymentLine.METHOD_CHOICES.
        ('credit', 'Credit / Unpaid'),
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
            ('none', 'None'),
            ('fixed', 'Fixed Amount'),
            ('percentage', 'Percentage'),
            ('sc', 'Senior Citizen (20%)'),
            ('pwd', 'PWD (20%)'),
            ('promo', 'Promo'),
        ],
        default='none',
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
    # FEATURE-012: frozen totals — single source of truth from migration 0023
    # forward. Written once at commit in services.create_pos_transaction();
    # never mutated afterwards (the void path must not touch these).
    gross_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Frozen pre-discount total (VAT-inclusive when BusinessProfile.vat_inclusive)',
    )
    discount_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Frozen discount applied to this transaction',
    )
    vat_exempt_amount = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Frozen VAT-exempt sales (net of removed VAT for SC/PWD)',
    )
    vatable_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Frozen VAT-exclusive sales subject to output VAT',
    )
    zero_rated_sales = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        default=Decimal('0.00'),
        help_text='Frozen zero-rated sales (no zero-rated item flag exists yet — always 0.00)',
    )
    net_total = models.DecimalField(
        max_digits=12,
        decimal_places=2,
        null=True,
        blank=True,
        help_text='Frozen final charged amount (equals total_amount at commit)',
    )
    shift = models.ForeignKey(
        'Shift',
        blank=True,
        null=True,
        on_delete=models.SET_NULL,
        related_name='transactions'
    )
    voided_at = models.DateTimeField(blank=True, null=True)
    # FLAG-047: demo/seed quarantine. True marks a transaction created by the
    # seed_demo fixture (never a real sale). Seed rows are excluded from every
    # live money query — X/Z reports, dashboard totals, and the IngredientLog
    # stock-movement feed — so demo data never contaminates BIR-grade figures.
    is_seed = models.BooleanField(default=False)
    # FEATURE-015: refund accounting. transaction_type defaults to 'sale';
    # refund rows carry 'refund' and link back to the original via refund_of
    # (the original is never mutated). related_name='refunds' lets a sale
    # report whether it has already been refunded.
    transaction_type = models.CharField(
        max_length=10,
        choices=TRANSACTION_TYPE_CHOICES,
        default='sale',
    )
    refund_of = models.ForeignKey(
        'self',
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name='refunds',
    )

    class Meta:
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['-created_at']),
            models.Index(fields=['transaction_no']),
            models.Index(fields=['payment_method']),
            models.Index(fields=['status'], name='pos_pos_status_idx'),
            models.Index(fields=['shift'], name='pos_pos_shift_idx'),
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
        from django.db.models import Max
        from datetime import timezone as dt_tz, timedelta
        from django.utils import timezone as dj_tz
        PHT = dt_tz(timedelta(hours=8))
        today = dj_tz.now().astimezone(PHT).date()
        with db_tx.atomic():
            counter_obj, created = OfficialReceiptCounter.objects.select_for_update().get_or_create(
                date=today,
                defaults={'counter': 0},
            )
            if created:
                # FLAG-057: OR numbering is installation-wide monotonic. A brand
                # new day's row must continue from the global high-water mark
                # rather than resetting to 0001, so NNNN is never reused across
                # days. Past-day rows are immutable, so this Max read is stable.
                highest = (
                    OfficialReceiptCounter.objects
                    .exclude(pk=counter_obj.pk)
                    .aggregate(m=Max('counter'))['m']
                    or 0
                )
                counter_obj.counter = highest
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
    quantity = models.PositiveIntegerField(
        default=1, verbose_name="Quantity",
        validators=[validate_non_negative_quantity],
    )
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
    # FEATURE-054: cost-at-sale snapshot (option B) — the effective per-unit COGS
    # at the moment of sale: the recipe-derived cost for recipe items, else the
    # item's manual purchase_price. Frozen here so COGS/margin reporting stays
    # historically accurate even as ingredient costs drift later. Distinct from
    # purchase_price, which is the item's manual cost field. Null on legacy rows.
    unit_cost = models.DecimalField(max_digits=10, decimal_places=4, null=True, blank=True)
    remarks = models.TextField(null=True, blank=True)

    def save(self, *args, **kwargs):
        # Auto-calculate subtotal
        if self.subtotal is None:
            self.subtotal = self.unit_price * self.quantity
        super().save(*args, **kwargs)

    def __str__(self):
        return f"{self.item.name} x{self.quantity} = {format_currency(self.subtotal)}"


class PaymentLine(models.Model):
    """FEATURE-016: one tendered amount per payment method on a transaction.

    A transaction may be split across methods (e.g. ₱50 cash + ₱100 GCash).
    The amounts represent actual tender and sum to the transaction's charged
    total (net_total — equal to gross_total when there is no discount). The
    X/Z payment breakdown and cash reconciliation aggregate over these rows
    rather than the single PosTransaction.payment_method, which is retained as
    the "primary method" (the method of the largest line) for backward compat.
    """
    METHOD_CHOICES = [
        ('cash', 'Cash'),
        ('card', 'Card'),
        ('gcash', 'GCash'),
        ('maya', 'Maya'),
        # FEATURE-059: goods handed over, payment owed. A credit tender is a
        # real tender (it settles the sale so the transaction balances) but it
        # is NOT cash, so it must never reach `cash_expected`. That falls out
        # for free because the reconciliation reads _pl('cash') only.
        ('credit', 'Credit / Unpaid'),
    ]

    transaction = models.ForeignKey(
        PosTransaction, on_delete=models.CASCADE, related_name='payment_lines'
    )
    method = models.CharField(max_length=10, choices=METHOD_CHOICES)
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    # FEATURE-059-FU: who owes it / what it was for.
    #
    # A credit tender without attribution is barely better than the bug it
    # replaces: the shop knows ₱312 is owed but not BY WHOM, and an unnamed
    # receivable is one nobody ever collects. PROD's actual case is exactly
    # this — a clinic tab and, separately, "yung sa food ni doc", which the
    # cashier had been reconciling in her head because the POS gave her
    # nowhere to write it down.
    #
    # Deliberately shaped like `CashMovement.reason` (same max_length, same
    # blank default) because it is the same idea on the other side of the
    # ledger: the free-text that explains a non-cash drawer event. Kept on
    # the tender rather than the transaction so a split sale can attribute
    # only its credit portion, and so the credit lines ARE the receivables
    # ledger without a join.
    note = models.CharField(max_length=200, blank=True, default='')

    # FEATURE-065: is this money actually coming back?
    #
    # Ralph, 2026-08-14: PROD's clinic tab will almost certainly never be paid —
    # the family that runs the clinic owns the cafe. A debt that will never be
    # collected is not a receivable, it is CONSUMPTION, and calling it a
    # receivable corrupts three things at once: "Credit Extended" accumulates
    # forever and never clears (so the owner learns to ignore it), net sales
    # carries revenue that will never arrive, and — because the milk and cups
    # really were used — COGS is real while the matching revenue is not, so
    # the margin reads better than it is.
    #
    # KIND_HOUSE is the default (Ralph's call), resolved from
    # BusinessProfile.default_credit_kind rather than hardcoded: defaulting to
    # "house" is PROD's business rule, and on a shop that runs genuine
    # collectible tabs the same default would silently write off real debts.
    #
    # Blank on every non-credit tender — a cash line has no such concept.
    KIND_HOUSE = 'house'
    KIND_CHARGE = 'charge'
    CREDIT_KIND_CHOICES = [
        (KIND_HOUSE, 'House (owner / family / staff)'),
        (KIND_CHARGE, 'Charge (to be settled)'),
    ]
    credit_kind = models.CharField(
        max_length=8, blank=True, default='', choices=CREDIT_KIND_CHOICES
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name='payline_positive'
            ),
        ]

    def __str__(self):
        return f"{self.method}: {format_currency(self.amount)}"


class TransactionItemVariant(models.Model):
    # NOTE: group_name and option_name are stored as plain CharFields (snapshot at transaction time).
    # This is intentional for receipt immutability — variant renames/deletes do not affect historical records.
    # Verified: services.py:317 writes from rv['group'].name / rv['option'].name at create time, and
    # TransactionItemVariantSerializer (serializers.py:217) reads only these stored fields — no live
    # lookup against ProductVariant. If live referential integrity is needed in future, add FK to
    # ProductVariant with on_delete=PROTECT (Variants 2.0).
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
        return f"{self.item.name} x{self.quantity} = {format_currency(self.subtotal)}"


# ============================================================================
# PAYMENT CONFIGURATION
# ============================================================================

class PaymentGatewayConfig(models.Model):
    """Payment gateway configuration - stores API credentials"""

    GATEWAY_CHOICES = [
        ('gcash', 'GCash'),
        ('maya', 'Maya'),
        ('card', 'Credit/Debit Card'),
    ]

    gateway = models.CharField(max_length=20, choices=GATEWAY_CHOICES, unique=True)
    is_active = models.BooleanField(default=True)
    use_mock_mode = models.BooleanField(default=True)  # Toggle between mock and real API

    # API Credentials — FLAG-009 Option B: encrypted at rest via custom Fernet field
    # (pos.fields.FernetEncryptedField). max_length=500 to accommodate Fernet
    # token overhead (~100 bytes base64 on top of the plaintext).
    merchant_id = FernetEncryptedField(max_length=500, blank=True, null=True)
    api_key = FernetEncryptedField(max_length=500, blank=True, null=True)
    api_secret = FernetEncryptedField(max_length=500, blank=True, null=True)
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
    # FEATURE-044: per-user page-access override. NULL = follow role default
    # (the historical role gate); a list of gateable page keys overrides it.
    # See pos/access.py for the canonical page definitions and resolution.
    allowed_pages = models.JSONField(null=True, blank=True, default=None)

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
    # ISSUE-099: transport mode supersedes the printer_enabled boolean and the
    # IP-as-enable-flag workaround. 'disabled' = no receipts; 'usb' uses the
    # local /dev/usb/lp* device (no IP needed); 'network' uses printer_ip:port.
    printer_mode = models.CharField(
        max_length=10,
        choices=[('usb', 'USB'), ('network', 'Network'), ('disabled', 'Disabled')],
        default='disabled', verbose_name='Printer Mode',
    )
    paper_width = models.CharField(
        max_length=10,
        choices=[('58mm', '58mm'), ('80mm', '80mm')],
        default='58mm', verbose_name='Paper Width',
    )
    printer_font = models.CharField(
        max_length=2,
        choices=[('A', 'Font A (wide)'), ('B', 'Font B (narrow)')],
        # FEATURE-040 follow-up: default to Font A (12x24, legible). Font B is the
        # narrow 9x17 condensed font and prints too small as a default body. A/B
        # remain selectable + distinct; this only changes the zero-config default.
        default='A', verbose_name='Printer Font',
    )
    color_scheme = models.CharField(max_length=7, default='#1d4ed8')
    updated_at = models.DateTimeField(auto_now=True)
    # Migration 0002
    tin = models.CharField(max_length=30, blank=True, default='', verbose_name='TIN')
    # Migration 0003
    discounts_enabled = models.BooleanField(default=False, verbose_name='Discounts Enabled')
    track_inventory = models.BooleanField(default=True, verbose_name='Track Inventory')
    # FEATURE-057: hide the whole ingredient/recipe/COGS system for businesses
    # that only sell finished goods (e.g. a shoe or apparel shop). Pure UI/read
    # gate — item-level stock (Item.stock) and manual cost/margin keep working;
    # no sale/costing math changes. Default on (café/restaurant default).
    ingredient_management_enabled = models.BooleanField(
        default=True, verbose_name='Ingredient & Recipe Management'
    )
    # FEATURE-065: which way an unpaid sale leans by default.
    #
    # At PROD the answer is 'house' — the clinic taking drinks is the owner's
    # own family, so the money is not coming back. That is a property of THIS
    # shop, not of the product: on a business that runs genuine collectible
    # tabs the same default would silently write off real debts. So it lives
    # here as per-shop config rather than as a constant in the register.
    default_credit_kind = models.CharField(
        max_length=8, default='house',
        choices=[
            ('house', 'House (owner / family / staff)'),
            ('charge', 'Charge (to be settled)'),
        ],
        verbose_name='Default Credit Kind',
    )
    vat_enabled = models.BooleanField(default=False, verbose_name='VAT Enabled')
    vat_rate = models.DecimalField(max_digits=5, decimal_places=2, default=12.0, verbose_name='VAT Rate (%)')
    vat_inclusive = models.BooleanField(default=True, verbose_name='VAT Inclusive Pricing')
    currency = models.CharField(max_length=10, default='PHP', verbose_name='Currency Code')
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

    # FEATURE-011-B: BIR machine/accreditation identity (display-only on
    # receipts/Z later — Session C/D consume these; no business logic here).
    machine_identification_number = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Machine Identification Number (MIN)',
        help_text='BIR-issued Machine Identification Number',
    )
    machine_serial_number = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='Machine Serial Number',
    )
    pos_accreditation_number = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='POS Accreditation Number',
    )
    pos_permit_number = models.CharField(
        max_length=64, blank=True, default='',
        verbose_name='POS Permit Number',
    )
    pos_accreditation_valid_until = models.DateField(
        null=True, blank=True,
        verbose_name='POS Accreditation Valid Until',
    )

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

    class Meta:
        constraints = [
            # ISSUE-104: at most one open shift per cashier (per-cashier
            # model, futureproof for multi-cashier). Partial unique — closed
            # shifts are unconstrained, so close-then-reopen always works.
            models.UniqueConstraint(
                fields=['cashier'],
                condition=models.Q(is_open=True),
                name='one_open_shift_per_cashier',
            ),
        ]

    def __str__(self):
        return f"Shift {self.id} — {self.cashier.username} ({'open' if self.is_open else 'closed'})"


class CashMovement(models.Model):
    """FEATURE-059: money that enters or leaves the drawer WITHOUT being a sale.

    Before this model the drawer reconciliation knew only two things: cash
    collected on sales (in) and cash refunded (out). Anything else that touched
    the drawer was invisible to `cash_expected`, so it surfaced as an
    unexplained over/short against whoever happened to close the shift.

    Three real cases at PROD, all previously unrepresentable:
      * the owner collecting cash mid-shift          -> KIND_DROP
      * a restock paid out of the drawer (FLAG-081)  -> KIND_PAYOUT
      * a credit sale later settled in cash          -> KIND_SETTLEMENT

    Rows are append-only by convention: a mistake is corrected by recording the
    opposite movement, never by editing or deleting, so the ledger always
    reconstructs how the drawer got to its closing figure.
    """

    KIND_DROP = 'drop'
    KIND_PAYOUT = 'payout'
    KIND_CASH_IN = 'cash_in'
    KIND_SETTLEMENT = 'settlement'

    KIND_CHOICES = [
        (KIND_DROP, 'Cash drop / collection'),
        (KIND_PAYOUT, 'Cash payout'),
        (KIND_CASH_IN, 'Cash added'),
        (KIND_SETTLEMENT, 'Credit settlement'),
    ]

    # Movements that REMOVE cash from the drawer. Everything else adds.
    OUTFLOW_KINDS = frozenset({KIND_DROP, KIND_PAYOUT})

    shift = models.ForeignKey(
        Shift, on_delete=models.CASCADE, related_name='cash_movements'
    )
    kind = models.CharField(max_length=12, choices=KIND_CHOICES)
    # Always a POSITIVE magnitude. Direction is derived from `kind` so a sign
    # error cannot silently invert a movement.
    amount = models.DecimalField(max_digits=10, decimal_places=2)
    reason = models.CharField(max_length=200, blank=True, default='')
    # For KIND_SETTLEMENT: which credit sale this pays off.
    settles = models.ForeignKey(
        'PosTransaction',
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name='settlements',
    )
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='cash_movements',
    )

    class Meta:
        constraints = [
            models.CheckConstraint(
                check=models.Q(amount__gt=0), name='cashmovement_positive'
            ),
        ]
        indexes = [models.Index(fields=['shift', 'kind'])]

    @property
    def signed_amount(self):
        """+ adds to the drawer, - removes from it."""
        return -self.amount if self.kind in self.OUTFLOW_KINDS else self.amount

    def __str__(self):
        return f"{self.get_kind_display()}: {format_currency(self.amount)}"


# ============================================================================
# FEATURE-011-C: Z-REPORT (BIR end-of-shift finalization)
# ============================================================================

class ZCounter(models.Model):
    """Singleton (pk=1) for gapless Z numbering + BIR running grand total."""
    z_counter = models.PositiveIntegerField(default=0)
    reset_counter = models.PositiveIntegerField(default=0)  # wraps at 9999
    grand_total = models.DecimalField(max_digits=16, decimal_places=2, default=0)
    # FEATURE-035: post-accreditation reset audit trail. The single ZCounter
    # row carries the accreditation event itself — no separate model needed.
    # When the café receives BIR accreditation the official Z-series restarts
    # at #1 (z_counter -> 0, grand_total -> 0); pre-accreditation ZReports stay
    # immutable but are flagged is_official=False.
    accredited_at = models.DateTimeField(
        null=True, blank=True,
        help_text="When the BIR accreditation reset was applied (null = not yet accredited).",
    )
    reset_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True, blank=True,
        on_delete=models.SET_NULL,
        related_name='accreditation_resets',
        help_text="User who applied the accreditation reset.",
    )

    class Meta:
        constraints = [
            models.CheckConstraint(check=models.Q(pk=1), name='zcounter_singleton'),
        ]

    def __str__(self):
        return f"ZCounter(z={self.z_counter}, reset={self.reset_counter})"


class ZReport(models.Model):
    """Immutable end-of-shift Z snapshot. Built once by
    services.close_shift_and_finalize_z from frozen PosTransaction columns;
    never updated (see save())."""

    # FEATURE-035: z_counter is unique WITHIN a reset series, not globally. A
    # post-accreditation reset (and the 9999 wrap) starts a new series with an
    # incremented reset_counter, so z_counter=1 can legitimately recur in a
    # later series while the immutable pre-reset Z-1 is retained.
    z_counter = models.PositiveIntegerField()
    reset_counter = models.PositiveIntegerField(default=0)
    business_date = models.DateField()                   # PHT-localdate of opened_at
    started_at = models.DateTimeField()                  # = shift.opened_at
    finalized_at = models.DateTimeField(auto_now_add=True)
    shift = models.OneToOneField(
        'Shift', on_delete=models.PROTECT, related_name='z_report'
    )

    # Frozen identity — copied from BusinessProfile at finalize time
    business_name = models.CharField(max_length=255)
    business_tin = models.CharField(max_length=64, blank=True, default='')
    business_address = models.CharField(max_length=512, blank=True, default='')
    machine_identification_number = models.CharField(max_length=64, blank=True, default='')
    machine_serial_number = models.CharField(max_length=64, blank=True, default='')
    pos_accreditation_number = models.CharField(max_length=64, blank=True, default='')
    pos_permit_number = models.CharField(max_length=64, blank=True, default='')

    # OR range (within-shift)
    first_or_number = models.CharField(max_length=32, blank=True, default='')
    last_or_number = models.CharField(max_length=32, blank=True, default='')
    voided_or_numbers = models.JSONField(default=list)

    # Counts
    transaction_count = models.PositiveIntegerField(default=0)
    voided_count = models.PositiveIntegerField(default=0)
    # FEATURE-015: refunds processed in this shift, shown as a separate BIR
    # deduction line (refund_total is a positive magnitude; refunds also reduce
    # net revenue via their negative transactions posted to this shift).
    refund_count = models.PositiveIntegerField(default=0)
    refund_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Sales totals (sum of PosTransaction frozen columns over non-voided rows)
    gross_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    net_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vatable_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    vat_exempt_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    zero_rated_sales = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    output_vat = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Discount breakdown
    sc_discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    pwd_discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    promo_discount_total = models.DecimalField(max_digits=12, decimal_places=2, default=0)

    # Payment breakdown — fixes ISSUE-081 (card payments dropped)
    payment_breakdown = models.JSONField(default=dict)

    # Cash reconciliation — fixes ISSUE-078 (opening float exclusion)
    opening_cash = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_collected = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # FEATURE-059: non-sale drawer movements, frozen onto the Z so the report
    # explains its own expected figure instead of leaving a bare discrepancy.
    # Both are POSITIVE magnitudes; direction is carried by the field name.
    cash_paid_out = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_paid_in = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # Credit extended during this shift (goods out, money owed) and credit
    # settled in cash during it. Reported, never folded into cash_expected —
    # extended credit is a receivable, and its settlement is already counted
    # once via cash_paid_in.
    # FEATURE-065: credit_extended now counts CHARGE tenders only — money the
    # shop actually expects back. House consumption (owner / family / staff)
    # is a separate figure below, because it is a cost, not an asset: rolling
    # it into a receivable makes A/R grow forever and never clear.
    credit_extended = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    credit_settled = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    # FEATURE-065: goods consumed by the house. Reported so the owner can see
    # a cost he currently cannot see at all; never a receivable, and never in
    # cash_expected (nothing was tendered either way).
    house_consumption = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_expected = models.DecimalField(max_digits=12, decimal_places=2, default=0)
    cash_counted = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    # FEATURE-061: WHEN the drawer was actually counted, which is not the same
    # as business_date. PROD shifts routinely run 14-31 hours and are closed the
    # next day, so a Z stamped 2026-08-11 could carry a count taken on 08-12
    # after the cash had already been collected. Recording it makes a late
    # count visible instead of quietly misleading.
    counted_at = models.DateTimeField(null=True, blank=True)
    over_short = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)

    grand_total_sales = models.DecimalField(max_digits=16, decimal_places=2, default=0)

    # Currency snapshot — frozen at finalize time. ZReports are 10-year
    # retained; reading currency from the live BusinessProfile at print
    # time would silently rewrite historical Z output if currency ever
    # changes. Display-only, but kept immutable for self-containment.
    currency = models.CharField(max_length=8, default='PHP')

    # ISSUE-105: False when finalized without a BusinessProfile MIN
    # (pre-BIR-accreditation). Drives the UNOFFICIAL stamp on print and
    # the conditional identity rows in the HTML/thermal Z.
    is_official = models.BooleanField(default=False)

    cashier = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='z_reports'
    )

    class Meta:
        ordering = ['-reset_counter', '-z_counter']
        indexes = [
            models.Index(fields=['business_date']),
            models.Index(fields=['finalized_at']),
        ]
        constraints = [
            # FEATURE-035: gapless numbering is per reset series.
            models.UniqueConstraint(
                fields=['reset_counter', 'z_counter'],
                name='zreport_unique_series_counter',
            ),
        ]

    def __str__(self):
        return f"Z-{self.z_counter:04d} ({self.business_date})"

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError("ZReport is immutable")
        super().save(*args, **kwargs)


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
    # FLAG-048: declarative non-negative guard for form/admin/full_clean input.
    # The sale-depletion path (services._deplete_ingredients) writes via F()
    # UPDATE which bypasses field validators by design — ISSUE-069 deliberately
    # lets stock go negative as an owner-investigate signal, never blocking a
    # sale. This validator only protects manual/declarative edits.
    current_stock = models.DecimalField(
        max_digits=10, decimal_places=4, default=0,
        validators=[MinValueValidator(Decimal('0'))],
    )
    par_level = models.DecimalField(max_digits=10, decimal_places=4, default=0)
    supplier = models.ForeignKey(Supplier, null=True, blank=True, on_delete=models.SET_NULL)
    is_active = models.BooleanField(default=True)
    # FEATURE-050: purchasing-unit layer. Recipes consume the base ``unit``
    # (g/ml/pcs), but the manager buys in packages (a box/sack/bottle) — she
    # should never have to divide a sack into grams by hand. ``purchase_unit``
    # names the package, ``purchase_to_base_factor`` is how many base units one
    # package holds (e.g. 1 sack = 25000 g → factor 25000), and
    # ``last_purchase_price`` remembers the most recent price PER PACKAGE so the
    # restock form can prefill it. All nullable: an ingredient without a
    # purchase unit still restocks in base units exactly as before (backward
    # compatible; nothing is required).
    purchase_unit = models.ForeignKey(
        IngredientUnit, null=True, blank=True, on_delete=models.SET_NULL,
        related_name='purchased_ingredients',
    )
    purchase_to_base_factor = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    last_purchase_price = models.DecimalField(
        max_digits=12, decimal_places=2, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0'))],
    )
    # FLAG-046: depletion gate independent of Item.track_inventory. Ingredient
    # stock is only depleted on sale/void when this is True.
    track_depletion = models.BooleanField(default=True)
    # FEATURE-056 (sub-recipe / preparation / BOM): an ingredient that is
    # ITSELF made in-house from other ingredients (e.g. simple syrup, cold-brew
    # concentrate). Its component list lives in PreparationComponent; a "prep a
    # batch" action (services.produce_batch) deducts the components and rolls
    # the batch cost into this ingredient's weighted-average cost_per_unit —
    # exactly like a restock, so the cost flows up into any menu recipe that
    # uses it. batch_yield = how many base units one batch produces.
    is_preparation = models.BooleanField(default=False)
    batch_yield = models.DecimalField(
        max_digits=12, decimal_places=4, null=True, blank=True,
        validators=[MinValueValidator(Decimal('0.0001'))],
    )
    # FLAG-048: surfaces last-touched time for stock/audit reconciliation.
    updated_at = models.DateTimeField(auto_now=True)

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
    # FEATURE-058: restock corrections. A mistaken entry is soft-voided (kept for
    # the audit trail, never deleted); an edited entry stamps corrected_by/at. The
    # cost/stock effects are recomputed in services (see void/edit/reattribute).
    is_voided = models.BooleanField(default=False)
    voided_at = models.DateTimeField(null=True, blank=True)
    voided_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    corrected_at = models.DateTimeField(null=True, blank=True)
    corrected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='+')
    correction_note = models.TextField(blank=True)
    # FEATURE-058 v2: snapshot of the ingredient's cost_per_unit BEFORE this
    # purchase rolled into it. Lets a void of the ingredient's ONLY remaining
    # purchase restore the honest pre-purchase cost instead of keeping the
    # polluted blend (the roll otherwise destroys the prior value). NULL on
    # rows recorded before this column existed — those fall back to keeping
    # the current cost.
    cost_before = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True)

    class Meta:
        ordering = ['-date']

    def save(self, *args, **kwargs):
        # Only apply stock/cost effects on initial insert; editing an existing
        # log must not double-count.
        # FLAG-078: a genuine restock also appends an IngredientLog(action=
        # 'restock') row so the append-only movement ledger is complete (it
        # previously recorded sales/voids/adjustments but never restocks).
        # ``log_movement=False`` suppresses that row for a caller that writes
        # its own ledger entry for the SAME movement — reattribute_restock
        # records the target purchase as a 'correction', so without this the
        # target would be double-logged.
        log_movement = kwargs.pop('log_movement', True)
        is_new = self._state.adding
        super().save(*args, **kwargs)
        if is_new:
            # FEATURE-051: roll this purchase into the ingredient's weighted
            # moving-average cost and add the quantity to stock — atomically
            # under a row lock so a concurrent sale/restock can't clobber the
            # read-modify-write (the depletion path also locks the same row).
            from django.db import transaction as _tx
            with _tx.atomic():
                ing = Ingredient.objects.select_for_update().get(pk=self.ingredient_id)
                old_qty = ing.current_stock
                old_cost = ing.cost_per_unit
                bought_qty = self.quantity_added
                new_price = self.cost_per_unit
                new_qty = old_qty + bought_qty
                if old_qty <= 0 or new_qty <= 0:
                    # Oversold/negative/empty prior stock: the old cost applies
                    # to stock that isn't really there (or the denominator is
                    # <=0), so the weighted average is meaningless — adopt the
                    # new purchase price outright.
                    new_cost = new_price
                else:
                    new_cost = (
                        (old_qty * old_cost + bought_qty * new_price) / new_qty
                    ).quantize(Decimal('0.0001'), rounding=ROUND_HALF_UP)
                ing.current_stock = new_qty
                ing.cost_per_unit = new_cost
                ing.save(update_fields=['current_stock', 'cost_per_unit'])
                # FEATURE-058 v2: stamp the pre-roll cost on this row (the row
                # was inserted before the lock read old_cost, so update it in
                # place inside the same transaction).
                type(self).objects.filter(pk=self.pk).update(cost_before=old_cost)
                self.cost_before = old_cost
                # FLAG-078: complete the append-only ledger. The roll above
                # moved stock old_qty -> new_qty; record it as a 'restock' row
                # (positive quantity_change) inside this same transaction so the
                # log commits atomically with the stock/cost update — a failure
                # here rolls back the whole restock rather than leaving an
                # unlogged movement. Guarded by is_new (edits use update_fields
                # and never reach this branch) and log_movement (reattribute
                # writes its own 'correction' row for the same movement).
                if log_movement:
                    IngredientLog.objects.create(
                        ingredient_id=self.ingredient_id, action='restock',
                        quantity_change=bought_qty,
                        stock_before=old_qty, stock_after=new_qty,
                        performed_by=self.recorded_by,
                        notes=f'restock #{self.pk}',
                    )
            # Keep the in-memory ingredient consistent for the caller.
            self.ingredient.current_stock = new_qty
            self.ingredient.cost_per_unit = new_cost

    def __str__(self):
        return f"+{self.quantity_added} {self.ingredient.unit.abbreviation} of {self.ingredient.name}"


class IngredientUnitConversion(models.Model):
    """FEATURE-046: how many BASE units one alternate unit is, per ingredient.

    Conversions cannot be global. A scoop of matcha powder and a scoop of sugar
    are different masses, so the factor belongs to the (ingredient, unit) pair —
    "1 scoop of Matcha = 2.5 g", not "1 scoop = 2.5 g".

    This is the recipe-entry sibling of the FEATURE-050 purchasing layer
    (Ingredient.purchase_unit / purchase_to_base_factor). Purchasing asks "how
    many grams in the sack I buy"; this asks "how many grams in the scoop I
    cook with". Same shape, different question, deliberately not shared.
    """
    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.CASCADE, related_name='unit_conversions'
    )
    unit = models.ForeignKey(IngredientUnit, on_delete=models.PROTECT)
    # How many of the ingredient's BASE units one `unit` equals.
    to_base_factor = models.DecimalField(max_digits=12, decimal_places=6)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=['ingredient', 'unit'], name='uniq_ingredient_unit_conv'
            ),
            models.CheckConstraint(
                check=models.Q(to_base_factor__gt=0),
                name='unit_conv_factor_positive',
            ),
        ]
        ordering = ['ingredient__name', 'unit__abbreviation']

    def clean(self):
        """Refuse to shadow something already defined elsewhere.

        The ingredient's own base unit is 1:1 by definition, and its PURCHASE
        unit already carries a factor via FEATURE-050 which the restock form
        converts with. A second copy of either is a value that can be edited
        apart from the original, after which restock and depletion would
        silently disagree about what a sack is.
        """
        from django.core.exceptions import ValidationError
        if self.ingredient_id and self.unit_id:
            if self.unit_id == self.ingredient.unit_id:
                raise ValidationError(
                    f"{self.ingredient.name} is already measured in "
                    f"'{self.unit.abbreviation}' — no conversion needed."
                )
            if (self.ingredient.purchase_unit_id == self.unit_id
                    and self.ingredient.purchase_to_base_factor):
                raise ValidationError(
                    f"'{self.unit.abbreviation}' is already this ingredient's "
                    f"purchase unit (1 = {self.ingredient.purchase_to_base_factor} "
                    f"{self.ingredient.unit.abbreviation}). Edit it there, so "
                    f"restock and recipes cannot disagree."
                )

    def save(self, *args, **kwargs):
        self.full_clean()
        return super().save(*args, **kwargs)

    def __str__(self):
        return (f"1 {self.unit.abbreviation} {self.ingredient.name} "
                f"= {self.to_base_factor} {self.ingredient.unit.abbreviation}")


class RecipeIngredient(models.Model):
    """Ingredients used in recipes for menu items"""
    item = models.ForeignKey('Item', null=True, blank=True, on_delete=models.CASCADE, related_name='recipe_ingredients')
    variant = models.ForeignKey('VariantOption', null=True, blank=True, on_delete=models.CASCADE, related_name='recipe_ingredients')
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE, related_name='recipes')
    # 🔴 quantity_used REMAINS THE SINGLE SOURCE OF TRUTH, always in the
    # ingredient's BASE unit. Every depletion and costing path reads this field
    # and none of them knows about entry units. FEATURE-046 adds the entry
    # fields BELOW as an input/display convenience that is converted into this
    # one on save — so a conversion bug can never silently change what a sale
    # depletes or what a recipe costs.
    quantity_used = models.DecimalField(max_digits=10, decimal_places=4)
    # FEATURE-046 / ISSUE-122: what the user actually typed, and in which unit.
    # Both null => the row was entered directly in base units (every existing
    # row, and still a valid way to enter one).
    entry_unit = models.ForeignKey(
        IngredientUnit, null=True, blank=True, on_delete=models.PROTECT,
        related_name='recipe_entries',
    )
    entry_quantity = models.DecimalField(
        max_digits=10, decimal_places=4, null=True, blank=True
    )

    def save(self, *args, **kwargs):
        """Derive quantity_used from the entry fields when they are supplied.

        Done here rather than in a serializer so EVERY write path — admin,
        management command, fixture, future endpoint — goes through the same
        conversion. A second implementation elsewhere is how the base-unit
        invariant would quietly break.
        """
        if self.entry_unit_id and self.entry_quantity is not None:
            from .services import convert_to_base_units  # circular at module load
            self.quantity_used = convert_to_base_units(
                self.ingredient, self.entry_quantity, self.entry_unit
            )
        super().save(*args, **kwargs)
    # BUG-003: how a variant line interacts with the item-level line for the same
    # ingredient. 'replace' (default) suppresses the base line — substitution,
    # e.g. Oat milk replaces Regular milk. 'add' depletes alongside the base —
    # additive add-ons/sizes, e.g. Extra Shot or Large adds to the base shot.
    # Default 'replace' preserves the historical behaviour for all existing rows.
    depletion_mode = models.CharField(
        max_length=7,
        choices=[('replace', 'Replace'), ('add', 'Add')],
        default='replace',
    )

    class Meta:
        constraints = [
            # BUG-013: every recipe line is owned by an Item. ``variant`` null is
            # the item's base recipe; ``variant`` set is a recipe specific to that
            # item + variant option. The owning item is mandatory because the
            # VariantOption is shared across products (CategoryVariantGroup /
            # ProductVariantGroup) — without the item dimension a variant line
            # bled onto every item that shared the option. (Supersedes BUG-001's
            # recipe_item_or_variant_not_both XOR, which forced item NULL on
            # variant lines and caused the cross-item bleed.)
            models.CheckConstraint(
                check=models.Q(item__isnull=False),
                name='recipe_item_required'
            )
        ]

    def __str__(self):
        return f"{self.ingredient.name} x{self.quantity_used} for {self.item or self.variant}"


class PreparationComponent(models.Model):
    """FEATURE-056: one component consumed to produce a batch of a preparation.

    The ``preparation`` is itself an Ingredient (is_preparation=True); each row
    says one batch needs ``quantity_used`` of ``component`` (in the component's
    base unit). services.produce_batch() sums component_cost = Σ(quantity_used *
    component.cost_per_unit), then rolls (component_cost / preparation.batch_yield)
    into the preparation's weighted-average cost_per_unit and adds the yield to
    its stock. Components are ordinary Ingredients, so a raw material shared
    between a direct drink recipe and a batch keeps ONE stock and ONE cost — no
    duplication, no double-count.
    """
    preparation = models.ForeignKey(
        Ingredient, on_delete=models.CASCADE, related_name='components'
    )
    component = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name='used_in_preparations'
    )
    quantity_used = models.DecimalField(max_digits=12, decimal_places=4)

    class Meta:
        constraints = [
            # A preparation can never be a component of itself.
            models.CheckConstraint(
                check=~models.Q(preparation=models.F('component')),
                name='prep_not_self_component',
            ),
            models.UniqueConstraint(
                fields=['preparation', 'component'],
                name='uniq_preparation_component',
            ),
        ]

    def __str__(self):
        return f"{self.quantity_used} {self.component.name} → {self.preparation.name}"


class IngredientLog(models.Model):
    """FEATURE-009: append-only ledger of every ingredient stock movement.

    One row per stock change (sale, void, manual adjustment, restock). Rows
    snapshot stock_before/stock_after at write time so the ledger stays an
    accurate audit trail even if the live Ingredient.current_stock is later
    touched by another path. There are no update/delete endpoints and the
    admin registration is read-only — the ledger is immutable history.
    """

    ACTION_CHOICES = [
        ('sale', 'Sale'),
        ('void', 'Void'),
        ('refund', 'Refund'),  # FEATURE-015: ingredient restore on customer refund
        ('adjustment', 'Adjustment'),
        ('restock', 'Restock'),
        ('production', 'Production'),  # FEATURE-056: prep-a-batch (both the
        # component depletion and the preparation's yield increment)
        ('correction', 'Correction'),  # FEATURE-058: restock void/edit/re-attribute
    ]

    ingredient = models.ForeignKey(
        Ingredient, on_delete=models.PROTECT, related_name='logs'
    )
    action = models.CharField(max_length=20, choices=ACTION_CHOICES)
    # Negative = depletion (sale), positive = restock/upward adjustment.
    quantity_change = models.DecimalField(max_digits=10, decimal_places=4)
    stock_before = models.DecimalField(max_digits=10, decimal_places=4)
    stock_after = models.DecimalField(max_digits=10, decimal_places=4)
    # Linked when action is sale/void; null for manual adjustment/restock.
    transaction = models.ForeignKey(
        'PosTransaction', null=True, blank=True,
        on_delete=models.SET_NULL, related_name='ingredient_logs'
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True,
        on_delete=models.SET_NULL, related_name='ingredient_logs'
    )
    timestamp = models.DateTimeField(default=dj_tz.now)
    notes = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ['-timestamp']

    def __str__(self):
        return (
            f"{self.ingredient.name} {self.action} "
            f"{self.quantity_change} ({self.stock_after})"
        )
