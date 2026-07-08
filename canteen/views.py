from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.http import Http404
from .services import (
    create_pos_transaction, _restore_ingredients, refund_transaction,
    close_shift_and_finalize_z, stock_movements_for_shift, produce_batch,
)
from .models import (
    ItemCategory, Item, ItemLog, PosTransaction, PosTransactionItem, Shift,
    VariantGroup, VariantOption, CategoryVariantGroup, ProductVariantGroup,
    TransactionItemVariant, BusinessProfile, RecipeIngredient, Ingredient,
    IngredientUnit, Supplier, IngredientRestockLog, ZReport, IngredientLog,
    PreparationComponent,
)
from .serializers import (
    ItemCategorySerializer,
    ItemSerializer,
    ItemCreateSerializer,
    ItemUpdateSerializer,
    ShiftSerializer,
    VariantGroupSerializer,
    VariantOptionSerializer,
    CategoryVariantGroupSerializer,
    ProductVariantGroupSerializer,
    IngredientUnitSerializer,
    SupplierSerializer,
    IngredientSerializer,
    IngredientRestockLogSerializer,
    RecipeIngredientSerializer,
    ItemLogSerializer,
    ZReportSerializer,
)
from django.db.models import Sum, Count, F, FloatField, Q
from django.db.models.functions import TruncDate, Coalesce
from datetime import datetime, timedelta, date, timezone as dt_tz
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from .permissions import IsManagerOrAbove, IsCashierOrAbove, IsAdmin, IsAdminOrStaff, HasPageAccess
from . import network_service
import csv, io, logging, subprocess

logger = logging.getLogger(__name__)
from rest_framework.parsers import MultiPartParser


class HealthCheckView(APIView):
    permission_classes = [AllowAny]

    def get(self, request):
        checks = {}
        # DB check
        try:
            from django.db import connection
            connection.ensure_connection()
            checks['database'] = 'ok'
        except Exception:
            checks['database'] = 'error'

        all_ok = all(v == 'ok' for v in checks.values())
        return Response(
            {'status': 'ok' if all_ok else 'degraded', 'checks': checks},
            status=200 if all_ok else 503
        )


class ItemCategoryViewSet(viewsets.ModelViewSet):
    queryset = ItemCategory.objects.all()
    serializer_class = ItemCategorySerializer
    # BUG-015: same global-PAGE_SIZE=50 truncation as Item/Ingredient. The
    # inventory frontend reads `results` (page 1) only, and categories drive the
    # POS grid grouping + item-assignment dropdowns — serve the (small) catalog
    # whole so a 51st category can't silently vanish.
    pagination_class = None

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsCashierOrAbove()]
        # FEATURE-044: category writes gated by 'inventory' page.
        return [HasPageAccess('inventory')()]

class PosTransactionViewSet(viewsets.ViewSet):
    permission_classes = [IsCashierOrAbove]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['transaction_no']
    ordering_fields = ['created_at']
    ordering = ['-created_at']

    def list(self, request):
        """List all transactions, with optional date filtering"""
        transactions = PosTransaction.objects.select_related(
            'cashier', 'shift'
        ).annotate(items_count=Count('items'))

        date_from_str = request.query_params.get('date_from')
        date_to_str = request.query_params.get('date_to')

        if date_from_str:
            try:
                date_from = datetime.strptime(date_from_str, '%Y-%m-%d').date()
                transactions = transactions.filter(created_at__date__gte=date_from)
            except ValueError:
                return Response({'error': 'Invalid date_from format. Use YYYY-MM-DD.'}, status=400)

        if date_to_str:
            try:
                date_to = datetime.strptime(date_to_str, '%Y-%m-%d').date()
                transactions = transactions.filter(created_at__date__lte=date_to)
            except ValueError:
                return Response({'error': 'Invalid date_to format. Use YYYY-MM-DD.'}, status=400)

        # Apply filtering and ordering backends
        for backend in list(self.filter_backends):
            transactions = backend().filter_queryset(request, transactions, self)

        def _serialize_tx(t):
            amt = t.total_amount
            total = float(amt.amount) if hasattr(amt, 'amount') else float(amt)
            return {
                'id': t.id,
                'transaction_no': t.transaction_no,
                'total': total,
                'total_amount': total,
                'status': t.status,
                'void': t.void,
                'payment_method': t.payment_method,
                'created_at': t.created_at.isoformat(),
                'gcash_reference': t.gcash_reference,
                'items_count': t.items_count,
                'discount_amount': float(t.discount_amount) if t.discount_amount else 0.0,
                'discount_type': t.discount_type or 'none',
                'discount_id_number': t.discount_id_number or '',
            }

        # Apply pagination
        paginator = PageNumberPagination()
        page = paginator.paginate_queryset(transactions, request, view=self)
        if page is not None:
            return paginator.get_paginated_response([_serialize_tx(t) for t in page])

        return Response([_serialize_tx(t) for t in transactions])

    def retrieve(self, request, pk=None):
        """Get transaction details including items"""
        try:
            transaction = PosTransaction.objects.select_related(
                'cashier', 'voided_by', 'shift'
            ).prefetch_related('items__item', 'items__variant_selections').get(pk=pk)
            items_data = []

            # Get transaction items
            transaction_items = transaction.items.all()
            for item in transaction_items:
                items_data.append({
                    'name': item.item.name,
                    'quantity': item.quantity,
                    'price': float(item.unit_price),
                    'base_price': float(item.base_price) if item.base_price is not None else None,
                    'final_price': float(item.final_price) if item.final_price is not None else None,
                    'variant_selections': [
                        {
                            'group_name': v.group_name,
                            'option_name': v.option_name,
                            'price_modifier': float(v.price_modifier)
                        }
                        for v in item.variant_selections.all()
                    ]
                })
            
            data = {
                'id': transaction.id,
                'transaction_no': transaction.transaction_no,
                'total': float(transaction.total_amount.amount) if hasattr(transaction.total_amount, 'amount') else float(transaction.total_amount),
                'payment_method': transaction.payment_method,
                'created_at': transaction.created_at.isoformat(),
                'gcash_reference': transaction.gcash_reference if hasattr(transaction, 'gcash_reference') else None,
                'cashier': transaction.cashier.get_full_name() or transaction.cashier.username if transaction.cashier else 'N/A',
                'items': items_data,
                'total_amount': float(transaction.total_amount.amount) if hasattr(transaction.total_amount, 'amount') else float(transaction.total_amount),
                'maya_reference': transaction.maya_reference or '',
                'customer_phone': transaction.customer_phone or '',
                'cash_received': float(transaction.cash_received) if transaction.cash_received else None,
                'change_given': float(transaction.change_given) if transaction.change_given else None,
                'void': transaction.void,
                'status': transaction.status,
                'transaction_type': transaction.transaction_type,
                'voided_by': transaction.voided_by.get_full_name() or transaction.voided_by.username if transaction.voided_by else None,
                'voided_at': transaction.voided_at.isoformat() if transaction.voided_at else None,
                'void_reason': transaction.purpose_of_void or '',
                'discount_amount':    float(transaction.discount_amount) if transaction.discount_amount else 0.0,
                'discount_type':      transaction.discount_type or 'none',
                'discount_id_number': transaction.discount_id_number or '',
            }
            return Response(data)
        except PosTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=404)

    def create(self, request):
        """Create a new POS transaction with payment details"""
        try:
            items_data = request.data.get('items', [])
            payment_method = request.data.get('payment_method', 'cash')
            
            transaction = create_pos_transaction(
                items_data=items_data,
                payment_method=payment_method,
                cashier=request.user,
                payment_lines=request.data.get('payment_lines'),  # FEATURE-016: split payment
                cash_received=request.data.get('cash_received'),
                gcash_reference=request.data.get('gcash_reference', ''),
                maya_reference=request.data.get('maya_reference', ''),
                card_reference=request.data.get('card_reference', ''),
                customer_phone=request.data.get('customer_phone', ''),
                discount_amount=request.data.get('discount_amount', 0),
                discount_type=request.data.get('discount_type', 'none'),
                discount_id_number=request.data.get('discount_id_number', ''),
            )
            
            return Response({
                'id': str(transaction.id),
                'success': True,
                'transaction_no': transaction.transaction_no,
                'total': float(transaction.total_amount.amount),
                'payment_method': transaction.payment_method,
                'cash_received': request.data.get('cash_received'),
                'change': float(transaction.change_given) if transaction.change_given else 0,
                'gcash_reference': transaction.gcash_reference or '',
                'maya_reference': transaction.maya_reference or '',
                'customer_phone': transaction.customer_phone or '',
            })
        except ValidationError as e:
            return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            logger.exception('Transaction creation failed')
            return Response({'success': False, 'error': 'An unexpected error occurred.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], permission_classes=[IsManagerOrAbove])
    def void(self, request, pk=None):
        """Void a transaction and reverse stock"""
        # Check permissions: only manager and admin can void
        if not IsManagerOrAbove().has_permission(request, self):
            return Response({'error': 'Permission denied. Only managers and admins can void transactions.'}, 
                            status=status.HTTP_403_FORBIDDEN)
                            
        try:
            with db_transaction.atomic():
                transaction = PosTransaction.objects.get(pk=pk)

                if transaction.void or transaction.status in ('void', 'refunded'):
                    return Response({'error': 'This transaction has already been voided or refunded.'},
                                    status=status.HTTP_400_BAD_REQUEST)

                # Reverse stock for each item in the transaction (only if inventory tracking is enabled)
                _bp = BusinessProfile.get_instance()
                if not _bp or _bp.track_inventory:
                    for item_entry in transaction.items.all():
                        Item.objects.filter(pk=item_entry.item.pk).update(
                            stock=F('stock') + item_entry.quantity
                        )
                        # Ingredient stock restore + ledger (ISSUE-069):
                        # mirrors the sale depletion in reverse (action='void'),
                        # attributed to the voiding user and linked to the txn.
                        _restore_ingredients(
                            item_entry.item, item_entry, item_entry.quantity,
                            transaction=transaction, performed_by=request.user,
                        )
                        refreshed = Item.objects.get(pk=item_entry.item.pk)
                        ItemLog.objects.create(
                            item=refreshed,
                            quantity=item_entry.quantity,
                            current_stock=refreshed.stock,
                            action='return',
                            remarks=f"Void reversal — OR#{transaction.transaction_no} (ID: {transaction.pk})",
                            created_by=request.user,
                        )

                transaction.void = True
                transaction.voided_at = timezone.now()
                transaction.status = 'void'
                transaction.voided_by = request.user
                transaction.purpose_of_void = request.data.get('reason', 'No reason provided')
                # FEATURE-012: void must NOT mutate the frozen totals. Restrict
                # the write to void bookkeeping fields only so the frozen
                # columns can never be re-persisted from a stale instance.
                transaction.save(update_fields=[
                    'void', 'voided_at', 'status', 'voided_by',
                    'purpose_of_void', 'updated_at',
                ])

                return Response({'success': True, 'message': 'Transaction voided successfully'})
        except PosTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception('Void failed')
            return Response({'error': 'An unexpected error occurred.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=True, methods=['post'], permission_classes=[IsManagerOrAbove])
    def refund(self, request, pk=None):
        """FEATURE-015: issue a refund for a prior sale (manager/admin only).

        A refund is distinct from a void: the original is left untouched and a
        new negative transaction is posted to the refunder's current open
        shift. Cashiers cannot refund.
        """
        try:
            refund = refund_transaction(pk, request.user)
        except PosTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'},
                            status=status.HTTP_404_NOT_FOUND)
        except ValidationError as e:
            return Response({'error': e.detail if hasattr(e, 'detail') else str(e)},
                            status=status.HTTP_400_BAD_REQUEST)
        except Exception:
            logger.exception('Refund failed')
            return Response({'error': 'An unexpected error occurred.'},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        return Response({
            'success': True,
            'refund_id': str(refund.id),
            'refund_transaction_no': refund.transaction_no,
            'transaction_type': refund.transaction_type,
            'amount': float(refund.gross_total or 0),
        }, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsCashierOrAbove])
    def print_receipt(self, request, pk=None):
        from .receipt_service import print_receipt as do_print
        try:
            transaction = PosTransaction.objects.get(pk=pk)
        except PosTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=404)
        result = do_print(transaction)
        return Response({'status': 'ok' if result.get('success') else 'error', 'print_status': result})

    @action(detail=False, methods=['post'], permission_classes=[IsCashierOrAbove],
            url_path='kick_drawer')
    def kick_drawer(self, request):
        from .receipt_service import kick_cash_drawer
        import threading
        threading.Thread(target=kick_cash_drawer, daemon=True).start()
        return Response({'status': 'kick queued'})

    @action(detail=False, methods=['post'], permission_classes=[IsManagerOrAbove],
            url_path='test_print')
    def test_print(self, request):
        from .receipt_service import print_receipt
        from .models import BusinessProfile
        from decimal import Decimal
        import threading
        profile = BusinessProfile.get_instance()
        if profile.printer_mode == 'disabled':
            return Response({'status': 'error', 'error': 'Printer not configured or disabled.'}, status=400)
        # Build a mock transaction-like object for the test
        class MockItem:
            def __init__(self):
                self.unit_price = 100
                class _item:
                    name = 'Test Item'
                self.item = _item()
                self.quantity = 2
                class _money:
                    amount = 200
                self.subtotal = _money()
                class _variants:
                    def all(self): return []
                self.variant_selections = _variants()

        class MockTransaction:
            transaction_no = 'TEST-001'
            payment_method = 'cash'
            gcash_reference = None
            maya_reference = None
            customer_phone = None
            created_at = timezone.now()
            cashier = None
            discount_amount = Decimal('0.00')
            discount_type = 'none'
            discount_id_number = ''
            class _total:
                amount = 200
            total_amount = _total()
            class _cash:
                amount = 250
            cash_received = _cash()
            class _items:
                def select_related(self, *a): return self
                def all(self): return [MockItem()]
            items = _items()
            def get_payment_method_display(self):
                return 'Cash'
        threading.Thread(
            target=print_receipt, args=(MockTransaction(),), daemon=True).start()
        return Response({'status': 'ok'})

    @action(detail=False, methods=['post'], permission_classes=[IsCashierOrAbove],
            url_path='print_xreport')
    def print_xreport(self, request):
        """Fire-and-forget ESC/POS print of X-report summary."""
        from .receipt_service import print_xreport_summary
        import threading
        threading.Thread(target=print_xreport_summary, args=(request.data,), daemon=True).start()
        return Response({'status': 'print queued'})

    @action(detail=False, methods=['get'], permission_classes=[IsCashierOrAbove])
    def xreport(self, request):
        """Current shift (X-report) — sales summary since shift open"""
        from django.db.models import Sum, Count
        import datetime as dt

        # Get current open shift for this user
        shift = Shift.objects.filter(
            cashier=request.user, is_open=True
        ).first()

        if not shift:
            return Response(
                {'error': 'No open shift found. Start a shift first.'},
                status=status.HTTP_404_NOT_FOUND
            )

        # FLAG-047: exclude seed rows from the X-report so demo data never
        # contaminates the live shift summary.
        shift_qs = PosTransaction.objects.filter(
            shift=shift, is_seed=False,
        )
        # FEATURE-015: refunds (status='refunded') are excluded from sales by
        # the status='completed' filter; they are surfaced separately below.
        completed = shift_qs.filter(void=False, status='completed')
        voided = shift_qs.filter(void=True)
        refunds = shift_qs.filter(transaction_type='refund')

        gross = float(completed.aggregate(
            total=Sum('total_amount'))['total'] or 0)
        transaction_count = completed.count()
        void_count = voided.count()
        void_total = float(voided.aggregate(
            total=Sum('total_amount'))['total'] or 0)
        # refund_total is a positive magnitude (refund total_amount is negative).
        refund_count = refunds.count()
        refund_total = abs(float(refunds.aggregate(
            total=Sum('total_amount'))['total'] or 0))
        net_sales = round(gross, 2)
        average_transaction = round(
            gross / transaction_count, 2) if transaction_count else 0

        # FEATURE-016: payment breakdown comes from the PaymentLine tender rows
        # of the sale transactions (grouped by method), supporting split
        # payments, instead of the single payment_method field.
        from .models import PaymentLine
        pl_rows = (
            PaymentLine.objects.filter(transaction__in=completed)
            .values('method')
            .annotate(total=Sum('amount'), cnt=Count('id'))
        )
        pl_by_method = {r['method']: r for r in pl_rows}
        by_method = []
        for method in ['cash', 'gcash', 'maya', 'card']:
            row = pl_by_method.get(method)
            by_method.append({
                'payment_method': method,
                'count': row['cnt'] if row else 0,
                'subtotal': float(row['total']) if row else 0.0,
            })

        return Response({
            'shift_id': str(shift.id),
            'cashier': request.user.username,
            'opened_at': shift.opened_at.isoformat(),
            'generated_at': timezone.now().isoformat(),
            'gross_sales': gross,
            'transaction_count': transaction_count,
            'average_transaction': average_transaction,
            'void_count': void_count,
            'void_total': void_total,
            'refund_count': refund_count,
            'refund_total': refund_total,
            'net_sales': net_sales,
            'by_payment_method': by_method,
            # FEATURE-008: per-ingredient sold/voided from the IngredientLog
            # ledger, scoped to this open shift. Empty list when nothing moved.
            'stock_movements': stock_movements_for_shift(shift),
        })

# ============================================================================
# VARIANT VIEWSETS
# ============================================================================

class VariantGroupViewSet(viewsets.ModelViewSet):
    queryset = VariantGroup.objects.prefetch_related('options').all()
    serializer_class = VariantGroupSerializer
    # FEATURE-044: variant management gated by 'inventory' page.
    permission_classes = [HasPageAccess('inventory')]
    # BUG-015: top-level catalog list read whole by inventory.html
    # (_allVariantGroups) — exempt from the global PAGE_SIZE=50 paginator so a
    # 51st variant group doesn't disappear from assignment UIs.
    pagination_class = None

    @action(detail=True, methods=['patch'], url_path='reorder-options')
    def reorder_options(self, request, pk=None):
        group = self.get_object()
        order = request.data.get('order', [])  # list of {id, sort_order}
        for entry in order:
            VariantOption.objects.filter(id=entry['id'], group=group).update(sort_order=entry['sort_order'])
        return Response({'status': 'reordered'})


class VariantOptionViewSet(viewsets.ModelViewSet):
    serializer_class = VariantOptionSerializer
    # FEATURE-044: variant management gated by 'inventory' page.
    permission_classes = [HasPageAccess('inventory')]

    def get_queryset(self):
        return VariantOption.objects.filter(group_id=self.kwargs['group_pk'])

    def perform_create(self, serializer):
        group = get_object_or_404(VariantGroup, pk=self.kwargs['group_pk'])
        serializer.save(group=group)


class CategoryVariantGroupViewSet(viewsets.ModelViewSet):
    serializer_class = CategoryVariantGroupSerializer
    # FEATURE-044: variant management gated by 'inventory' page.
    permission_classes = [HasPageAccess('inventory')]
    # Read whole by inventory.html to derive which groups an item inherits from
    # its category (loadProductVariantGroups) — exempt from the global
    # PAGE_SIZE=50 paginator, like VariantGroupViewSet (BUG-015). Otherwise a
    # 51st category assignment silently drops out of inheritance detection and
    # an unchecked inherited group fails to persist its disable.
    pagination_class = None

    def get_queryset(self):
        return (CategoryVariantGroup.objects
                .filter(category_id=self.kwargs['category_pk'])
                .select_related('group').order_by('id'))

    def perform_create(self, serializer):
        category = get_object_or_404(ItemCategory, pk=self.kwargs['category_pk'])
        group_id = self.request.data.get('group_id')
        group = get_object_or_404(VariantGroup, pk=group_id)
        serializer.save(category=category, group=group)


class ProductVariantGroupViewSet(viewsets.ModelViewSet):
    serializer_class = ProductVariantGroupSerializer
    # FEATURE-044: variant management gated by 'inventory' page.
    permission_classes = [HasPageAccess('inventory')]
    # Read whole by inventory.html: loadProductVariantGroups builds the override
    # map from this list and saveProductVariantGroups sweeps it to DELETE stale
    # overrides. Under the global PAGE_SIZE=50 paginator a >50-override item
    # would leave rows undeleted; exempt it (BUG-015 rationale).
    pagination_class = None

    def get_queryset(self):
        return ProductVariantGroup.objects.filter(product_id=self.kwargs['product_pk']).select_related('group').order_by('id')

    def perform_create(self, serializer):
        product = get_object_or_404(Item, pk=self.kwargs['product_pk'])
        group_id = self.request.data.get('group_id')
        group = get_object_or_404(VariantGroup, pk=group_id)
        serializer.save(product=product, group=group)


class DashboardViewSet(viewsets.ViewSet):
    """Dashboard statistics and analytics"""
    # FEATURE-044: gated by the 'dashboard' page (defaults to manager/admin).
    permission_classes = [HasPageAccess('dashboard')]

    def list(self, request):
        """Get dashboard data"""
        today = timezone.now().date()

        # FLAG-047: every dashboard money/count query excludes seed rows so
        # demo data never shows up in live totals or charts.
        base = PosTransaction.objects.filter(status='completed', is_seed=False)

        # Today's stats
        today_transactions = base.filter(
            created_at__date=today,
        )
        today_revenue = today_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        today_count = today_transactions.count()

        # This week's stats
        week_start = today - timedelta(days=today.weekday())
        week_transactions = base.filter(
            created_at__date__gte=week_start,
        )
        week_revenue = week_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        # This month's stats
        month_start = today.replace(day=1)
        month_transactions = base.filter(
            created_at__date__gte=month_start,
        )
        month_revenue = month_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0

        # All time stats
        all_transactions = base
        total_revenue = all_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        total_count = all_transactions.count()

        # Last 7 days daily revenue — single query
        seven_days_ago = today - timedelta(days=6)
        daily_totals = dict(
            base.filter(
                created_at__date__gte=seven_days_ago,
            ).annotate(
                day=TruncDate('created_at')
            ).values('day').annotate(
                total=Sum('total_amount')
            ).values_list('day', 'total')
        )
        last_7_days = []
        for i in range(6, -1, -1):
            d = today - timedelta(days=i)
            last_7_days.append({
                'date': d.strftime('%Y-%m-%d'),
                'day_name': d.strftime('%a'),
                'revenue': float(daily_totals.get(d, 0))
            })
        
        # Top selling items today (by quantity)
        top_items = PosTransactionItem.objects.filter(
            pos_transaction__created_at__date=today,
            pos_transaction__void=False,
            pos_transaction__is_seed=False,  # FLAG-047
        ).values(
            'item__name'
        ).annotate(
            total_quantity=Sum('quantity'),
            total_revenue=Sum('subtotal')
        ).order_by('-total_quantity')[:5]
        
        top_items_list = [
            {
                'name': item['item__name'],
                'quantity': item['total_quantity'],
                'revenue': float(item['total_revenue'])
            }
            for item in top_items
        ]
        
        # Backup health check (ISSUE-112: use real backup path + tightened glob)
        import os, glob
        from django.conf import settings
        backup_dir = os.environ.get('BACKUP_PATH', str(settings.BASE_DIR / 'backups'))
        backup_warning = False
        last_backup = None
        try:
            backups = sorted(glob.glob(f'{backup_dir}/db_[0-9]*.sqlite3'))
            if backups:
                last_backup_time = os.path.getmtime(backups[-1])
                PHT = dt_tz(timedelta(hours=8))
                last_backup = datetime.fromtimestamp(last_backup_time, tz=PHT).strftime('%Y-%m-%d %H:%M')
                hours_since = (timezone.now().timestamp() - last_backup_time) / 3600
                backup_warning = hours_since > 48
            else:
                backup_warning = True
        except Exception:
            backup_warning = True

        return Response({
            'today': {
                'revenue': float(today_revenue),
                'transactions': today_count
            },
            'week': {
                'revenue': float(week_revenue),
                'transactions': week_transactions.count()
            },
            'month': {
                'revenue': float(month_revenue),
                'transactions': month_transactions.count()
            },
            'all_time': {
                'revenue': float(total_revenue),
                'transactions': total_count
            },
            'last_7_days': last_7_days,
            'top_items': top_items_list,
            'backup_warning': backup_warning,
            'last_backup': last_backup,
        })

# ============================================
# PAYMENT GATEWAY ENDPOINTS
# ============================================

from rest_framework.decorators import api_view
from .payment_adapters import PaymentGatewayFactory
import json

@api_view(['POST'])
@permission_classes([IsCashierOrAbove])
def process_gcash_payment(request):
    """Process GCash payment (mock or real based on config)"""
    try:
        amount = request.data.get('amount')
        items = request.data.get('items', [])
        reference = request.data.get('reference')
        
        if not amount or not items:
            return Response({
                'success': False,
                'message': 'Amount and items are required'
            }, status=400)
        
        # Get GCash adapter (mock or real)
        adapter = PaymentGatewayFactory.get_adapter('gcash')
        
        # Process payment
        result = adapter.process_payment(
            amount=amount,
            reference=reference,
            metadata={'items': items}
        )
        
        if result['success']:
            try:
                # Create transaction record using service
                transaction = create_pos_transaction(
                    items_data=items,
                    payment_method='gcash',
                    cashier=request.user,
                    gcash_reference=result['transaction_id'],
                )

                return Response({
                    'success': True,
                    'transaction_id': transaction.id,
                    'transaction_no': transaction.transaction_no,
                    'gcash_reference': result['transaction_id'],
                    'amount': float(amount),
                    'message': result['message']
                })
            except ValidationError as e:
                return Response({'success': False, 'message': str(e)}, status=400)
            except Exception as e:
                logger.exception('GCash post-payment processing failed')
                return Response({'success': False, 'message': 'An unexpected error occurred.'}, status=500)
        else:
            return Response({
                'success': False,
                'message': result['message'],
                'error_code': result.get('error_code')
            }, status=400)

    except Exception as e:
        logger.exception('GCash payment processing error')
        return Response({
            'success': False,
            'message': 'Payment processing error. Please try again.'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsCashierOrAbove])
def process_maya_payment(request):
    """Process Maya QR payment (mock or real based on config)"""
    try:
        amount = request.data.get('amount')
        items = request.data.get('items', [])
        reference = request.data.get('reference')
        
        if not amount or not items:
            return Response({
                'success': False,
                'message': 'Amount and items are required'
            }, status=400)
        
        # Get Maya adapter (mock or real)
        adapter = PaymentGatewayFactory.get_adapter('maya')
        
        # Process payment
        result = adapter.process_payment(
            amount=amount,
            reference=reference,
            metadata={'items': items}
        )
        
        if result['success']:
            # Use service function for atomic transaction creation and stock reversal
            transaction = create_pos_transaction(
                items_data=items,
                payment_method='maya',
                cashier=request.user,
                maya_reference=result['transaction_id']
            )
            
            return Response({
                'success': True,
                'transaction_id': transaction.id,
                'transaction_no': transaction.transaction_no,
                'maya_reference': result['transaction_id'],
                'qr_code': result.get('qr_code'),  # For QR display
                'amount': float(amount),
                'message': result['message']
            })
        else:
            return Response({
                'success': False,
                'message': result['message'],
                'error_code': result.get('error_code')
            }, status=400)
            
    except Exception as e:
        logger.exception('Maya payment processing error')
        return Response({
            'success': False,
            'message': 'Payment processing error. Please try again.'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsCashierOrAbove])
def process_card_payment(request):
    """Process card payment via Maya Terminal (mock or real)"""
    try:
        amount = request.data.get('amount')
        items = request.data.get('items', [])
        reference = request.data.get('reference')
        
        if not amount or not items:
            return Response({
                'success': False,
                'message': 'Amount and items are required'
            }, status=400)
        
        # Get Maya Terminal adapter (mock or real)
        adapter = PaymentGatewayFactory.get_adapter('maya', terminal=True)
        
        # Process payment
        result = adapter.process_payment(
            amount=amount,
            reference=reference,
            metadata={'items': items}
        )
        
        if result['success']:
            # Use service function for atomic transaction creation and stock reversal
            transaction = create_pos_transaction(
                items_data=items,
                payment_method='card',
                cashier=request.user,
                card_reference=result['transaction_id']
            )
            
            return Response({
                'success': True,
                'transaction_id': transaction.id,
                'transaction_no': transaction.transaction_no,
                'card_reference': result['transaction_id'],
                'card_type': result.get('card_type'),
                'last_4': result.get('last_4_digits'),
                'approval_code': result.get('approval_code'),
                'amount': float(amount),
                'message': result['message']
            })
        else:
            return Response({
                'success': False,
                'message': result['message'],
                'error_code': result.get('error_code')
            }, status=400)
            
    except Exception as e:
        logger.exception('Card payment processing error')
        return Response({
            'success': False,
            'message': 'Payment processing error. Please try again.'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsCashierOrAbove])
def get_payment_config(request):
    """Get payment gateway configuration (for frontend)"""
    from .models import PaymentGatewayConfig
    
    try:
        configs = {}
        
        for gateway in ['gcash', 'maya']:
            try:
                config = PaymentGatewayConfig.objects.get(gateway=gateway)
                configs[gateway] = {
                    'is_active': config.is_active,
                    'use_mock_mode': config.use_mock_mode,
                    'enable_terminal': config.enable_terminal if gateway == 'maya' else False,
                }
            except PaymentGatewayConfig.DoesNotExist:
                configs[gateway] = {
                    'is_active': True,
                    'use_mock_mode': True,
                    'enable_terminal': False,
                }
        
        return Response({
            'success': True,
            'configs': configs
        })
        
    except Exception as e:
        logger.exception('Failed to load payment config')
        return Response({
            'success': False,
            'message': 'Failed to load payment configuration.'
        }, status=500)


@api_view(['POST'])
@permission_classes([IsManagerOrAbove])
def update_payment_config(request):
    """Update payment gateway configuration"""
    from .models import PaymentGatewayConfig
    
    try:
        gateway = request.data.get('gateway')
        config_data = request.data.get('config', {})
        
        if gateway not in ['gcash', 'maya', 'card']:
            return Response({
                'success': False,
                'message': 'Invalid gateway'
            }, status=400)
        
        config, created = PaymentGatewayConfig.objects.get_or_create(gateway=gateway)
        
        # Update configuration
        if 'is_active' in config_data:
            config.is_active = config_data['is_active']
        if 'use_mock_mode' in config_data:
            config.use_mock_mode = config_data['use_mock_mode']
        if 'merchant_id' in config_data:
            config.merchant_id = config_data['merchant_id']
        if 'api_key' in config_data:
            config.api_key = config_data['api_key']
        if 'api_secret' in config_data:
            config.api_secret = config_data['api_secret']
        if 'webhook_url' in config_data:
            config.webhook_url = config_data['webhook_url']
        if 'enable_terminal' in config_data:
            config.enable_terminal = config_data['enable_terminal']
        if 'terminal_id' in config_data:
            config.terminal_id = config_data['terminal_id']
        
        config.save()
        
        return Response({
            'success': True,
            'message': f'{gateway.upper()} configuration updated',
            'config': {
                'gateway': config.gateway,
                'is_active': config.is_active,
                'use_mock_mode': config.use_mock_mode,
            }
        })
        
    except Exception as e:
        logger.exception('Failed to update payment config')
        return Response({
            'success': False,
            'message': 'Failed to update payment configuration.'
        }, status=500)


@api_view(['GET'])
@permission_classes([IsManagerOrAbove])
def get_terminal_credential_status(request, gateway):
    """Return whether each terminal credential is set, never the value itself."""
    from .models import PaymentGatewayConfig
    if gateway not in ('gcash', 'maya'):
        return Response({'error': 'Invalid gateway'}, status=400)
    cfg = PaymentGatewayConfig.objects.filter(gateway=gateway).first()
    if not cfg:
        return Response({
            'gateway': gateway,
            'merchant_id_is_set': False,
            'api_key_is_set': False,
            'api_secret_is_set': False,
            'configured': False,
        })
    flags = {
        'merchant_id_is_set': bool(cfg.merchant_id),
        'api_key_is_set': bool(cfg.api_key),
        'api_secret_is_set': bool(cfg.api_secret),
    }
    return Response({
        'gateway': gateway,
        **flags,
        'configured': all(flags.values()),
    })


def _accreditation_payload(counter):
    """Shared status shape for the accreditation endpoints."""
    accredited = bool(counter and counter.accredited_at)
    reset_by = counter.reset_by if counter else None
    return {
        'accredited': accredited,
        'accredited_at': counter.accredited_at.isoformat()
        if accredited else None,
        'reset_by': reset_by.username if reset_by else None,
    }


@api_view(['GET'])
@permission_classes([IsAdminOrStaff])
def accreditation_status(request):
    """FEATURE-035: current BIR accreditation status (admin/staff only)."""
    from .models import ZCounter
    counter = ZCounter.objects.filter(pk=1).first()
    return Response(_accreditation_payload(counter))


@api_view(['POST'])
@permission_classes([IsAdminOrStaff])
def accreditation_reset(request):
    """FEATURE-035: one-time post-accreditation Z-series reset (admin/staff).

    Resets z_counter -> 0 and grand_total -> 0, stamps the accreditation
    event on the ZCounter, and flags every existing ZReport as
    pre-accreditation (is_official=False). Callable once; a second call
    returns 400.
    """
    from .services import apply_accreditation_reset, AccreditationAlreadyApplied
    try:
        counter = apply_accreditation_reset(request.user)
    except AccreditationAlreadyApplied as e:
        return Response(
            {'error': str(e)}, status=status.HTTP_400_BAD_REQUEST
        )
    payload = _accreditation_payload(counter)
    payload['message'] = (
        'BIR accreditation applied. The official Z-series will start at #1.'
    )
    return Response(payload, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([IsManagerOrAbove])
def upload_payment_qr(request, gateway):
    """Upload QR image for a payment gateway"""
    from .models import PaymentGatewayConfig
    try:
        uploaded, err = _validate_image_upload(request, 'qr_image')
        if err:
            return err
        config, _ = PaymentGatewayConfig.objects.get_or_create(gateway=gateway)
        config.qr_image = uploaded
        config.save()
        return Response({'success': True, 'qr_url': request.build_absolute_uri(config.qr_image.url)})
    except Exception as e:
        logger.exception('QR image upload failed for gateway %s', gateway)
        return Response({'error': 'Failed to upload QR image.'}, status=500)


@api_view(['GET'])
@permission_classes([IsCashierOrAbove])
def get_gateway_qr_config(request, gateway):
    """Get payment gateway config including QR image URL"""
    from .models import PaymentGatewayConfig
    try:
        config = PaymentGatewayConfig.objects.filter(gateway=gateway).first()
        if not config:
            return Response({'qr_url': None, 'is_active': False})
        return Response({
            'qr_url': request.build_absolute_uri(config.qr_image.url) if config.qr_image else None,
            'is_active': config.is_active
        })
    except Exception as e:
        logger.exception('Failed to load QR config for gateway %s', gateway)
        return Response({'error': 'Failed to load payment QR configuration.'}, status=500)


class ItemViewSet(viewsets.ModelViewSet):
    """Complete CRUD for Items"""
    queryset = Item.objects.all().select_related('category')
    serializer_class = ItemSerializer
    # B-INVESTIGATE-INV (Bug A): the global PAGE_SIZE=50 silently truncated
    # this list once the catalog crossed 50 items — every consumer (POS grid,
    # inventory table, dashboard low-stock) renders only page 1, so items
    # past the alphabetical cutoff "disappeared". The catalog is small by
    # nature; serve it whole.
    pagination_class = None

    def get_queryset(self):
        qs = Item.objects.all().select_related('category').prefetch_related(
            'variant_group_overrides__group__options',
            'category__variant_groups__group__options',
            # FEATURE-046: direct recipe lines + their ingredient stock, so the
            # serializer's makeable/makeable_status compute with no per-item query.
            'recipe_ingredients__ingredient',
        )
        sku = self.request.query_params.get('sku')
        search = self.request.query_params.get('search')
        if sku:
            return qs.filter(sku__iexact=sku.strip())
        if search:
            return qs.filter(name__icontains=search.strip())
        # BUG-008: opt-in active/archived filtering. DEFAULT (no param) is
        # UNCHANGED — every existing consumer (reports, recipe editor, POS
        # client-side filter, dashboard) still receives the full set.
        #   ?active_only=true  → only live items (inventory main list)
        #   ?is_active=false   → only archived items (inventory Archived view)
        #   ?is_active=true    → only live items (alias)
        active_only = self.request.query_params.get('active_only')
        is_active_param = self.request.query_params.get('is_active')
        truthy = ('true', '1', 'yes')
        if active_only is not None and active_only.lower() in truthy:
            return qs.filter(is_active=True)
        if is_active_param is not None:
            return qs.filter(is_active=is_active_param.lower() in truthy)
        return qs

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            # FEATURE-044: writes gated by 'inventory' page; reads stay cashier+ (POS).
            permission_classes = [HasPageAccess('inventory')]
        else:
            permission_classes = [IsCashierOrAbove]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return ItemCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ItemUpdateSerializer
        return ItemSerializer  # list, retrieve → read serializer with photo fallback

    def destroy(self, request, *args, **kwargs):
        """BUG-006: soft-delete (archive) items that have sales history.

        PosTransactionItem references Item with on_delete=PROTECT — past
        transactions and Z-reports must never be corrupted by hard-deleting a
        sold item. Previously the resulting ProtectedError was uncaught and
        surfaced as a raw HTTP 500. Now: try the hard delete (items that were
        never sold still delete cleanly → 204); if the DB protects it, flip
        is_active=False instead (drops it from the POS menu, keeps the record)
        and return 200 so the frontend can tell the user it was archived.
        """
        from django.db.models.deletion import ProtectedError
        item = self.get_object()
        try:
            item.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except ProtectedError:
            item.is_active = False
            item.save(update_fields=['is_active'])
            return Response(
                {'detail': 'Item has sales history — archived (removed from '
                           'menu, kept in your records) instead of deleted.'},
                status=status.HTTP_200_OK,
            )

    @action(detail=False, methods=['get'], permission_classes=[IsManagerOrAbove])
    def analytics(self, request):
        """Get inventory analytics"""
        from .models import BusinessProfile
        _threshold = BusinessProfile.get_instance().low_stock_threshold

        agg = Item.objects.aggregate(
            total_items=Count('id'),
            total_value=Sum(F('price') * F('stock'), output_field=FloatField()),
            total_cost=Sum(F('purchase_price') * F('stock'), output_field=FloatField()),
        )
        low_stock = Item.objects.filter(stock__lt=_threshold).count()
        total_items = agg['total_items'] or 0
        total_value = float(agg['total_value'] or 0)
        total_cost = float(agg['total_cost'] or 0)

        # Average profit margin still needs per-item calculation
        items_with_cost = Item.objects.filter(
            purchase_price__gt=0
        ).only('price', 'purchase_price')
        margins = [m for item in items_with_cost if (m := item.profit_margin) is not None]
        avg_margin = sum(margins) / len(margins) if margins else 0

        return Response({
            'total_items': total_items,
            'low_stock_count': low_stock,
            'total_inventory_value': total_value,
            'total_cost_value': total_cost,
            'potential_profit': total_value - total_cost,
            'average_profit_margin': round(avg_margin, 2),
        })
    
    @action(detail=True, methods=['get'], permission_classes=[HasPageAccess('ingredients')])
    def variants(self, request, pk=None):
        """ISSUE-070: variant options effective for this item, for the recipe
        builder's variant selector (so variant-specific recipes can be authored).

        Flattens the item's effective variant groups (single canonical resolver,
        FLAG-049) into their active options. ``id`` is the VariantOption id the
        recipe builder posts as ``variant``; ``name`` is prefixed with the group
        so same-named options across groups (e.g. two "Large"s) stay distinct.
        """
        from .services import resolve_effective_variant_groups
        item = self.get_object()
        options = []
        for resolved in resolve_effective_variant_groups(item):
            group = resolved['group']
            for option in group.options.all():
                if option.is_active:
                    options.append({
                        'id': str(option.id),
                        'name': f'{group.name} — {option.name}',
                    })
        return Response(options)

    @action(detail=True, methods=['get'], permission_classes=[IsManagerOrAbove])
    def logs(self, request, pk=None):
        """Return last 50 stock audit log entries for this item."""
        item = self.get_object()
        qs = ItemLog.objects.filter(item=item).order_by('-created_at')[:50]
        serializer = ItemLogSerializer(qs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'], permission_classes=[IsManagerOrAbove])
    def adjust_stock(self, request, pk=None):
        """Adjust stock levels — Audit Verified"""
        MAX_ADJUSTMENT = 10000
        MAX_STOCK = 999999

        adjustment = int(request.data.get('adjustment', 0))
        reason = request.data.get('reason', '')

        if abs(adjustment) > MAX_ADJUSTMENT:
            return Response(
                {'error': f'Adjustment value exceeds maximum allowed ({MAX_ADJUSTMENT}).'},
                status=status.HTTP_400_BAD_REQUEST
            )

        with db_transaction.atomic():
            item = Item.objects.select_for_update().get(pk=pk)
            old_stock = item.stock
            new_stock = item.stock + adjustment
            if new_stock < 0:
                return Response(
                    {'error': f'Adjustment would result in negative stock ({new_stock}). Current stock: {item.stock}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if new_stock > MAX_STOCK:
                return Response(
                    {'error': f'Resulting stock ({new_stock}) exceeds maximum allowed ({MAX_STOCK}).'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            item.stock = new_stock
            item.save()

            # Create log
            ItemLog.objects.create(
                item=item,
                quantity=adjustment,
                current_stock=item.stock,
                action='adjustment',
                remarks=f"Changed from {old_stock} to {item.stock}",
                created_by=request.user,
            )

        return Response({
            'success': True,
            'old_stock': old_stock,
            'new_stock': item.stock,
            'adjustment': adjustment
        })

    @action(detail=False, methods=['post'], parser_classes=[MultiPartParser],
            permission_classes=[IsManagerOrAbove], url_path='import_csv')
    def import_csv(self, request):
        file_obj = request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No file provided.'}, status=400)
        if not file_obj.name.endswith('.csv'):
            return Response({'error': 'File must be a .csv'}, status=400)

        decoded = file_obj.read().decode('utf-8-sig')
        reader = csv.DictReader(io.StringIO(decoded))

        results = []
        required = {'name', 'price', 'stock'}

        for i, row in enumerate(reader, start=2):  # row 1 is header
            row = {k.strip().lower(): v.strip() for k, v in row.items()}
            missing = required - set(row.keys())
            if missing:
                results.append({'row': i, 'status': 'error',
                                 'message': f'Missing columns: {missing}', 'name': row.get('name','')})
                continue
            try:
                # Resolve category
                category = None
                cat_name = row.get('category', '').strip()
                if cat_name:
                    from .models import ItemCategory
                    category = ItemCategory.objects.filter(
                        name__iexact=cat_name
                    ).first()
                    if not category:
                        category = ItemCategory.objects.create(
                            name=cat_name
                        )

                data = {
                    'name': row['name'],
                    'price': row['price'],
                    'stock': row['stock'],
                    'purchase_price': row.get('purchase_price') or None,
                    'sku': row.get('sku') or None,
                    'description': row.get('description') or '',
                    'is_active': True,
                }
                if category:
                    data['category'] = category.id

                # Update existing by name (case-insensitive), else create
                existing = Item.objects.filter(name__iexact=row['name']).first()
                if existing:
                    serializer = ItemUpdateSerializer(existing, data=data, partial=True)
                else:
                    serializer = ItemCreateSerializer(data=data)

                if serializer.is_valid():
                    serializer.save()
                    results.append({'row': i, 'status': 'ok',
                                     'name': row['name'],
                                     'action': 'updated' if existing else 'created'})
                else:
                    results.append({'row': i, 'status': 'error',
                                     'name': row['name'],
                                     'message': str(serializer.errors)})
            except Exception as e:
                logger.exception('CSV import failed at row %d', i)
                results.append({'row': i, 'status': 'error',
                                 'name': row.get('name', ''), 'message': 'Failed to import this row.'})

        ok = [r for r in results if r['status'] == 'ok']
        errors = [r for r in results if r['status'] == 'error']
        return Response({
            'total': len(results),
            'created': len([r for r in ok if r.get('action') == 'created']),
            'updated': len([r for r in ok if r.get('action') == 'updated']),
            'errors': len(errors),
            'rows': results
        })

# ============================================================================
# SHIFT VIEWS
# ============================================================================

class ShiftViewSet(viewsets.ModelViewSet):
    permission_classes = [IsManagerOrAbove]
    queryset = Shift.objects.all().order_by('-opened_at')
    serializer_class = ShiftSerializer

    def update(self, request, *args, **kwargs):
        """Block reopening a closed shift via PUT/PATCH."""
        shift = self.get_object()
        if shift.closed_at and request.data.get('is_open') in (True, 'true', 'True'):
            return Response(
                {'error': 'This shift has been closed and cannot be reopened.'},
                status=status.HTTP_403_FORBIDDEN
            )
        return super().update(request, *args, **kwargs)

    def destroy(self, request, *args, **kwargs):
        """Shifts are financial audit records and must never be deleted."""
        return Response(
            {'error': 'Shifts cannot be deleted. They are permanent audit records.'},
            status=status.HTTP_405_METHOD_NOT_ALLOWED
        )

    @action(detail=False, methods=['post'], url_path='open', permission_classes=[IsCashierOrAbove])
    def open_shift(self, request):
        """ISSUE-107: open a new shift for the requesting user (own shift).

        400 on negative/invalid opening_cash, or if the cashier already has
        an open shift (the partial unique constraint surfaces as
        IntegrityError inside the service and is returned friendly).
        """
        from decimal import Decimal, InvalidOperation
        from django.core.exceptions import ValidationError as DjangoValidationError
        from .services import open_shift as open_shift_service

        raw = request.data.get('opening_cash', 0)
        try:
            opening_cash = Decimal(str(raw))
        except (InvalidOperation, ValueError, TypeError):
            return Response({'error': 'opening_cash must be a valid decimal.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            shift = open_shift_service(request.user, opening_cash)
        except DjangoValidationError as e:
            return Response({'error': '; '.join(e.messages)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(ShiftSerializer(shift).data,
                        status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['get'], url_path='current', permission_classes=[IsCashierOrAbove])
    def current(self, request):
        """Return the requesting user's open shift, or 204 if none."""
        shift = Shift.objects.filter(
            cashier=request.user, is_open=True
        ).order_by('-opened_at').first()
        if shift:
            return Response(ShiftSerializer(shift).data)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=False, methods=['get'], url_path='active', permission_classes=[IsCashierOrAbove])
    def active(self, request):
        """FEATURE-037: cross-account floor view of all currently-open shifts.

        Non-sensitive: shift number, cashier username/role, opened time only.
        No financial fields (float, sales). Visible to all roles.
        """
        shifts = (
            Shift.objects.filter(is_open=True)
            .select_related('cashier')
            .order_by('opened_at')
        )
        data = [
            {
                'shift_number': s.id,
                'cashier_username': s.cashier.username,
                'cashier_role': getattr(s.cashier, 'role', ''),
                'opened_at': s.opened_at,
            }
            for s in shifts
        ]
        return Response(data)

    @action(detail=True, methods=['post'], url_path='close',
            permission_classes=[IsCashierOrAbove])
    def close(self, request, pk=None):
        """FEATURE-011-D: finalize this shift into an immutable ZReport.

        Body: {"cash_counted": <decimal>} (required, >= 0). Cashiers may
        only close their own shift; managers/admins may close any.
        """
        from decimal import Decimal, InvalidOperation
        from django.core.exceptions import ValidationError as DjangoValidationError

        try:
            shift = Shift.objects.get(pk=pk)
        except Shift.DoesNotExist:
            return Response({'error': 'Shift not found.'},
                            status=status.HTTP_404_NOT_FOUND)

        role = getattr(request.user, 'role', '')
        if role not in ('manager', 'admin') and shift.cashier_id != request.user.id:
            return Response({'error': 'You can only close your own shift.'},
                            status=status.HTTP_403_FORBIDDEN)

        raw = request.data.get('cash_counted')
        if raw is None or raw == '':
            return Response({'error': 'cash_counted is required.'},
                            status=status.HTTP_400_BAD_REQUEST)
        try:
            cash_counted = Decimal(str(raw))
        except (InvalidOperation, ValueError):
            return Response({'error': 'cash_counted must be a decimal.'},
                            status=status.HTTP_400_BAD_REQUEST)
        if cash_counted < 0:
            return Response({'error': 'cash_counted must be >= 0.'},
                            status=status.HTTP_400_BAD_REQUEST)

        try:
            z_report = close_shift_and_finalize_z(
                shift.id, cash_counted, request.user
            )
        except DjangoValidationError as e:
            return Response({'error': '; '.join(e.messages)},
                            status=status.HTTP_400_BAD_REQUEST)

        return Response(ZReportSerializer(z_report).data,
                        status=status.HTTP_201_CREATED)


class ZReportPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 200


class ZReportViewSet(viewsets.ReadOnlyModelViewSet):
    """FEATURE-011-C: read-only access to immutable Z reports.

    No create/update/delete is exposed (ReadOnlyModelViewSet) and the
    model itself rejects re-saves; manager/admin only. Lookup by the
    gapless z_counter, not the surrogate pk.
    """
    # FEATURE-044: gated by the 'zreport' page (defaults to manager/admin).
    permission_classes = [HasPageAccess('zreport')]
    serializer_class = ZReportSerializer
    pagination_class = ZReportPagination
    lookup_field = 'z_counter'

    def get_queryset(self):
        qs = ZReport.objects.all().order_by('-reset_counter', '-z_counter')
        business_date = self.request.query_params.get('business_date')
        if business_date:
            qs = qs.filter(business_date=business_date)
        return qs

    def get_object(self):
        """FEATURE-035: z_counter is unique per reset series, so a value may
        recur across an accreditation reset. Resolve detail lookups to the most
        recent series (highest reset_counter) for the requested z_counter."""
        from django.http import Http404
        z_counter = self.kwargs[self.lookup_field]
        obj = (
            self.filter_queryset(self.get_queryset())
            .filter(z_counter=z_counter)
            .order_by('-reset_counter')
            .first()
        )
        if obj is None:
            raise Http404('No ZReport matches the given z_counter.')
        self.check_object_permissions(self.request, obj)
        return obj

    @action(detail=True, methods=['post'])
    def print(self, request, z_counter=None):
        """FEATURE-011-D: thermal-print this immutable Z.

        Idempotent — the ZReport is already frozen; this only triggers
        the printer. A read-side mutation-free POST is acceptable here.
        Fire-and-forget so a slow/offline printer never blocks the API.
        """
        z_report = self.get_object()
        from .receipt_service import print_z_report
        import threading
        threading.Thread(
            target=print_z_report, args=(z_report,), daemon=True
        ).start()
        return Response({'status': 'print queued',
                         'z_counter': z_report.z_counter})


@api_view(['GET'])
@permission_classes([IsAuthenticated])
def get_business_profile(request):
    from .models import BusinessProfile
    profile = BusinessProfile.get_instance()
    data = {
        'business_name': profile.business_name,
        'tagline': profile.tagline,
        'contact_number': profile.contact_number,
        'email': profile.email,
        'address': profile.address,
        'tin': profile.tin,
        'receipt_header': profile.receipt_header,
        'receipt_footer': profile.receipt_footer,
        'low_stock_threshold': profile.low_stock_threshold,
        'printer_mode': profile.printer_mode,
        'paper_width': profile.paper_width,
        'printer_font': profile.printer_font,
        'color_scheme': profile.color_scheme,
        'logo': request.build_absolute_uri(profile.logo.url) if profile.logo else None,
        'vat_enabled': profile.vat_enabled,
        'vat_rate': float(profile.vat_rate),
        'vat_inclusive': profile.vat_inclusive,
        'currency': profile.currency,
        'sc_discount_enabled': profile.sc_discount_enabled,
        'sc_discount_rate': float(profile.sc_discount_rate),
        'pwd_discount_enabled': profile.pwd_discount_enabled,
        'pwd_discount_rate': float(profile.pwd_discount_rate),
        'promo_discount_enabled': profile.promo_discount_enabled,
        'track_inventory': profile.track_inventory,
        'ingredient_management_enabled': profile.ingredient_management_enabled,
        # FEATURE-011-B: BIR identity (display-only; Session C/D consume these)
        'machine_identification_number': profile.machine_identification_number,
        'machine_serial_number': profile.machine_serial_number,
        'pos_accreditation_number': profile.pos_accreditation_number,
        'pos_permit_number': profile.pos_permit_number,
        'pos_accreditation_valid_until': (
            profile.pos_accreditation_valid_until.isoformat()
            if profile.pos_accreditation_valid_until else None
        ),
    }
    # Only expose printer network details to managers/admins
    if request.user.is_staff or getattr(request.user, 'role', '') in ('manager', 'admin'):
        data['printer_ip'] = profile.printer_ip
        data['printer_port'] = profile.printer_port
    return Response(data)


@api_view(['PATCH'])
@permission_classes([IsManagerOrAbove])
def update_business_profile(request):
    from .models import BusinessProfile
    profile = BusinessProfile.get_instance()
    fields = ['business_name', 'tagline', 'contact_number', 'email', 'address', 'tin', 'receipt_header', 'receipt_footer', 'low_stock_threshold', 'printer_ip', 'printer_port', 'printer_mode', 'paper_width', 'printer_font', 'color_scheme', 'logo', 'vat_enabled', 'vat_rate', 'vat_inclusive', 'currency', 'sc_discount_enabled', 'sc_discount_rate', 'pwd_discount_enabled', 'pwd_discount_rate', 'promo_discount_enabled', 'track_inventory', 'ingredient_management_enabled',
              # FEATURE-011-B: BIR identity fields
              'machine_identification_number', 'machine_serial_number',
              'pos_accreditation_number', 'pos_permit_number',
              'pos_accreditation_valid_until']
    from django.utils.dateparse import parse_date
    # ISSUE-099: reject out-of-choice printer config at the write boundary.
    choice_fields = {
        'printer_mode': {'usb', 'network', 'disabled'},
        'paper_width': {'58mm', '80mm'},
        'printer_font': {'A', 'B'},
    }
    for field in fields:
        if field in request.data:
            value = request.data[field]
            if field in choice_fields and value not in choice_fields[field]:
                return Response(
                    {'error': f'{field} must be one of {sorted(choice_fields[field])}.'},
                    status=400,
                )
            if field == 'pos_accreditation_valid_until':
                # Empty means "not set" -> NULL (never '' on the DateField).
                if not value:
                    value = None
                else:
                    parsed = parse_date(str(value))
                    if parsed is None:
                        return Response(
                            {'error': 'pos_accreditation_valid_until must be '
                                      'an ISO date (YYYY-MM-DD).'},
                            status=400,
                        )
                    value = parsed
            setattr(profile, field, value)
    profile.save()
    return Response({'success': True, 'business_name': profile.business_name, 'tagline': profile.tagline})


ALLOWED_IMAGE_TYPES = {'image/jpeg', 'image/png', 'image/webp'}
MAX_UPLOAD_SIZE = 5 * 1024 * 1024  # 5MB


def _validate_image_upload(request, field_name='logo'):
    """Validate uploaded image file. Returns (file, error_response) tuple."""
    from django.utils.text import get_valid_filename
    uploaded = request.FILES.get(field_name)
    if not uploaded:
        return None, Response({'error': 'No file provided.'}, status=400)
    if uploaded.content_type not in ALLOWED_IMAGE_TYPES:
        return None, Response({'error': 'Only JPEG, PNG, and WebP images are allowed.'}, status=400)
    if uploaded.size > MAX_UPLOAD_SIZE:
        return None, Response({'error': 'File size must be under 5MB.'}, status=400)
    uploaded.name = get_valid_filename(uploaded.name)
    return uploaded, None


@api_view(['POST'])
@permission_classes([IsManagerOrAbove])
def upload_business_logo(request):
    from .models import BusinessProfile
    profile = BusinessProfile.get_instance()
    uploaded, err = _validate_image_upload(request, 'logo')
    if err:
        return err
    profile.logo = uploaded
    profile.save()
    return Response({'success': True, 'logo': request.build_absolute_uri(profile.logo.url)})


# ============================================================================
# INGRENT VIEWSETS
# ============================================================================

class IngredientUnitViewSet(viewsets.ModelViewSet):
    queryset = IngredientUnit.objects.all().order_by('name')
    serializer_class = IngredientUnitSerializer
    permission_classes = [IsAuthenticated]
    # BUG-015: same truncation as Bug A on ItemViewSet — the global PAGE_SIZE=50
    # silently dropped page-2 rows, and the ingredients frontend reads only
    # `results` (page 1) with no next-page handling. Reference data is small;
    # serve it whole so new entries past the alphabetical cutoff stay visible.
    pagination_class = None


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.filter(is_active=True).order_by('name')
    serializer_class = SupplierSerializer
    # FEATURE-044: gated by the 'ingredients' page (defaults to manager/admin).
    permission_classes = [HasPageAccess('ingredients')]
    # BUG-015: see IngredientUnitViewSet — disable pagination so the supplier
    # list isn't truncated to the first 50 in the frontend.
    pagination_class = None


class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.filter(is_active=True).select_related('unit','supplier').order_by('name')
    serializer_class = IngredientSerializer
    # FEATURE-044: gated by the 'ingredients' page (defaults to manager/admin).
    permission_classes = [HasPageAccess('ingredients')]
    # BUG-015: root cause — this inherited the global PAGE_SIZE=50 paginator
    # (settings.py), but ingredients.html reads only `results` (page 1) with no
    # pagination UI. Once the canteen crossed 50 ingredients, new entries that
    # sort past the page-1 cutoff (e.g. "wintermelon") saved fine (201) but
    # never appeared in the list or recipe dropdown. Same fix as ItemViewSet.
    pagination_class = None

    @action(detail=True, methods=['post'])
    def restock(self, request, pk=None):
        ingredient = self.get_object()
        # FEATURE-050: pass the ingredient in context so package-based entry can
        # read its purchase_to_base_factor / last_purchase_price for conversion.
        serializer = IngredientRestockLogSerializer(
            data=request.data, context={'ingredient': ingredient}
        )
        if serializer.is_valid():
            serializer.save(ingredient=ingredient, recorded_by=request.user)
            return Response(serializer.data, status=201)
        return Response(serializer.errors, status=400)

    @action(detail=True, methods=['get'])
    def restock_logs(self, request, pk=None):
        ingredient = self.get_object()
        logs = ingredient.restock_logs.all().order_by('-date')[:50]
        serializer = IngredientRestockLogSerializer(logs, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def set_components(self, request, pk=None):
        """FEATURE-056: replace a preparation's component (BOM) list.

        Body: { components: [ { component: <ingredient_id>, quantity_used: <dec> }, ... ] }
        Marks the ingredient as a preparation and swaps its component set
        atomically. Rejects self-reference and non-positive quantities. Editing
        the BOM does NOT move any stock — only produce_batch does that.
        """
        from decimal import Decimal, InvalidOperation
        prep = self.get_object()
        rows = request.data.get('components', [])
        if not isinstance(rows, list) or not rows:
            return Response({'error': 'Provide at least one component.'}, status=400)
        cleaned = []
        for r in rows:
            cid = r.get('component')
            if str(cid) == str(prep.pk):
                return Response(
                    {'error': 'A preparation cannot contain itself.'}, status=400)
            try:
                qty = Decimal(str(r.get('quantity_used')))
            except (InvalidOperation, TypeError, ValueError):
                return Response({'error': 'quantity_used must be a number.'}, status=400)
            if qty <= 0:
                return Response(
                    {'error': 'Each component quantity must be greater than zero.'},
                    status=400)
            if not Ingredient.objects.filter(pk=cid, is_active=True).exists():
                return Response({'error': f'Unknown component {cid}.'}, status=400)
            cleaned.append((cid, qty))
        with db_transaction.atomic():
            if not prep.is_preparation:
                prep.is_preparation = True
                prep.save(update_fields=['is_preparation'])
            PreparationComponent.objects.filter(preparation=prep).delete()
            PreparationComponent.objects.bulk_create([
                PreparationComponent(preparation=prep, component_id=cid, quantity_used=qty)
                for cid, qty in cleaned
            ])
        return Response(self.get_serializer(prep).data, status=200)

    @action(detail=True, methods=['post'])
    def produce_batch(self, request, pk=None):
        """FEATURE-056: 'prep a batch' — deduct components, add yield, roll cost.

        Body: { num_batches: <dec>, notes: <optional str> }
        """
        prep = self.get_object()
        try:
            summary = produce_batch(
                prep, request.data.get('num_batches', 0),
                performed_by=request.user, notes=request.data.get('notes', ''),
            )
        except ValidationError as e:
            detail = e.detail
            msg = detail[0] if isinstance(detail, list) else detail
            return Response({'error': str(msg)}, status=400)
        prep.refresh_from_db()
        return Response(
            {**summary, 'ingredient': self.get_serializer(prep).data}, status=200)

    @action(detail=True, methods=['post'])
    def adjust(self, request, pk=None):
        """ISSUE-071: the only sanctioned path to move current_stock directly.

        Direct current_stock writes via PATCH are blocked (serializer makes the
        field read-only on update). A manual correction goes through here, which
        updates stock atomically under select_for_update() and writes an
        IngredientLog(action='adjustment') attributed to request.user.

        Two body forms (provide exactly one):
          { new_stock: decimal }       — BUG-017: set stock to this ABSOLUTE
              value. The delta is computed server-side against the locked row,
              so a stale client baseline can't corrupt the result (the old
              client-computed quantity_change set 'Apple green tea syrup' to
              10.017 instead of 960 because the browser's cached stock was out
              of sync). This is what the edit modal's Current Stock field sends.
          { quantity_change: decimal } — signed delta (e.g. ad-hoc correction).
        Both accept an optional { notes: string }.
        """
        from decimal import Decimal, InvalidOperation
        notes = request.data.get('notes', '') or ''
        raw_new = request.data.get('new_stock')
        raw_delta = request.data.get('quantity_change')

        if (raw_new is None) == (raw_delta is None):
            return Response(
                {'error': 'Provide exactly one of new_stock or quantity_change.'},
                status=status.HTTP_400_BAD_REQUEST)

        with db_transaction.atomic():
            ingredient = Ingredient.objects.select_for_update().get(pk=pk)
            before = ingredient.current_stock
            if raw_new is not None:
                try:
                    after = Decimal(str(raw_new))
                except (InvalidOperation, TypeError, ValueError):
                    return Response({'error': 'new_stock must be a number.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                if after < 0:
                    return Response({'error': 'new_stock cannot be negative.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                qty = after - before
                if qty == 0:
                    # Already at the requested value — harmless no-op, no ledger row.
                    return Response(self.get_serializer(ingredient).data)
            else:
                try:
                    qty = Decimal(str(raw_delta))
                except (InvalidOperation, TypeError, ValueError):
                    return Response({'error': 'quantity_change must be a number.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                if qty == 0:
                    return Response({'error': 'quantity_change cannot be zero.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                after = before + qty

            ingredient.current_stock = after
            ingredient.save(update_fields=['current_stock', 'updated_at'])
            IngredientLog.objects.create(
                ingredient=ingredient,
                action='adjustment',
                quantity_change=qty,
                stock_before=before,
                stock_after=after,
                performed_by=request.user,
                notes=notes,
            )
        return Response(self.get_serializer(ingredient).data)

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        low = [i for i in self.get_queryset() if i.is_low_stock]
        serializer = self.get_serializer(low, many=True)
        return Response(serializer.data)

    @action(detail=False, methods=['post'])
    def reconcile(self, request):
        """FEATURE-055: weekly par-based physical count / reconcile.

        A boundary ritual (open/close), not a during-service task: the manager
        enters the ABSOLUTE counted amount for each flagged ingredient. For each,
        the server locks the row, computes variance = counted − expected (the
        locked current_stock), sets stock to the counted value, and writes an
        IngredientLog(action='adjustment') tagged as a weekly count. Negative
        variance = shrinkage/waste. Absolute-value entry (like `adjust`) so a
        stale client baseline can't corrupt the result. Atomic across the batch.

        Body: { counts: [ { id, counted_stock }, ... ], note?: str }
        Returns per-line variance so the UI can show the waste report.
        """
        from decimal import Decimal, InvalidOperation
        counts = request.data.get('counts')
        if not isinstance(counts, list) or not counts:
            return Response({'error': 'counts must be a non-empty list.'},
                            status=status.HTTP_400_BAD_REQUEST)
        base_note = (request.data.get('note') or 'Weekly count').strip()
        results = []
        with db_transaction.atomic():
            for entry in counts:
                iid = entry.get('id')
                try:
                    counted = Decimal(str(entry.get('counted_stock')))
                except (InvalidOperation, TypeError, ValueError):
                    return Response(
                        {'error': f'counted_stock must be a number (ingredient {iid}).'},
                        status=status.HTTP_400_BAD_REQUEST)
                if counted < 0:
                    return Response(
                        {'error': f'counted_stock cannot be negative (ingredient {iid}).'},
                        status=status.HTTP_400_BAD_REQUEST)
                try:
                    ingredient = Ingredient.objects.select_for_update().get(pk=iid)
                except Ingredient.DoesNotExist:
                    return Response({'error': f'ingredient {iid} not found.'},
                                    status=status.HTTP_400_BAD_REQUEST)
                before = ingredient.current_stock
                variance = counted - before
                if variance != 0:
                    ingredient.current_stock = counted
                    ingredient.save(update_fields=['current_stock', 'updated_at'])
                    IngredientLog.objects.create(
                        ingredient=ingredient, action='adjustment',
                        quantity_change=variance, stock_before=before,
                        stock_after=counted, performed_by=request.user,
                        notes=f'{base_note}: counted {counted}, expected {before}, '
                              f'variance {variance}',
                    )
                results.append({
                    'id': ingredient.id, 'name': ingredient.name,
                    'expected': float(before), 'counted': float(counted),
                    'variance': float(variance),
                    'unit': ingredient.unit.abbreviation if ingredient.unit else '',
                })
        return Response({'results': results})


class RecipeIngredientViewSet(viewsets.ModelViewSet):
    # BUG-014 / PAGINATION-WARN-2: explicit ordering — the global DRF paginator
    # (PAGE_SIZE=50) otherwise warns (UnorderedObjectListWarning) and can yield
    # inconsistent pages on an unordered queryset.
    queryset = RecipeIngredient.objects.select_related('ingredient','item','variant').order_by('id')
    serializer_class = RecipeIngredientSerializer
    # FEATURE-044: gated by the 'ingredients' page (defaults to manager/admin).
    permission_classes = [HasPageAccess('ingredients')]

    def get_queryset(self):
        qs = super().get_queryset()
        item_id = self.request.query_params.get('item')
        variant_id = self.request.query_params.get('variant')
        if item_id:
            qs = qs.filter(item_id=item_id)
        if variant_id:
            qs = qs.filter(variant_id=variant_id)
        elif item_id:
            # BUG-013: variant lines now also carry item, so a bare ?item=<id>
            # (the base-recipe view, no variant selected) must return only the
            # item's base lines — not its variant lines mixed in.
            qs = qs.filter(variant__isnull=True)
        return qs


# FEATURE-020: local-status MVP
def _latest_backup_info():
    import glob, os
    from django.conf import settings
    backup_dir = str(settings.BASE_DIR / 'backups')
    matches = sorted(glob.glob(f'{backup_dir}/db_[0-9]*.sqlite3'))
    if not matches:
        return None
    latest = matches[-1]
    st = os.stat(latest)
    return {
        'filename': os.path.basename(latest),
        'size_bytes': st.st_size,
        'mtime': datetime.fromtimestamp(st.st_mtime, tz=dt_tz.utc).isoformat(),
    }


def _disk_info():
    import shutil
    from django.conf import settings
    total, used, free = shutil.disk_usage(settings.BASE_DIR)
    return {
        'total_bytes': total,
        'used_bytes': used,
        'available_bytes': free,
        'used_percent': round(used / total * 100, 1) if total else 0.0,
    }


@api_view(['GET'])
@permission_classes([IsManagerOrAbove])
def local_status(request):
    return Response({
        'backup': _latest_backup_info(),
        'disk': _disk_info(),
        'server_time': timezone.now().isoformat(),
    })


@api_view(['POST'])
@permission_classes([IsManagerOrAbove])
def local_status_snapshot(request):
    from django.conf import settings
    script = str(settings.BASE_DIR / 'backup_db.sh')
    try:
        proc = subprocess.run(
            ['/bin/bash', script],
            capture_output=True, text=True, timeout=30,
        )
    except subprocess.TimeoutExpired:
        return Response({'detail': 'snapshot timed out'}, status=500)
    if proc.returncode != 0:
        return Response(
            {'detail': 'snapshot failed', 'stderr': proc.stderr},
            status=500,
        )
    info = _latest_backup_info()
    if not info:
        return Response({'detail': 'snapshot produced no file'}, status=500)
    return Response({'filename': info['filename'], 'size_bytes': info['size_bytes']})


# FEATURE-040: on-screen receipt preview — renders the SAME layout the printer
# uses (canteen/receipt_layout), so screen and paper stay in parity (FLAG-064).
@api_view(['GET'])
@permission_classes([IsManagerOrAbove])
def receipt_preview(request):
    from .receipt_layout import render_sample_text, receipt_cols
    from .models import BusinessProfile
    profile = BusinessProfile.objects.first()
    # Optional live overrides so the preview reflects unsaved width/font choices.
    # Set on the in-memory instance only — never saved.
    pw = request.query_params.get('paper_width')
    pf = request.query_params.get('printer_font')
    if profile and pw in ('58mm', '80mm'):
        profile.paper_width = pw
    if profile and pf in ('A', 'B'):
        profile.printer_font = pf
    return Response({
        'text': render_sample_text(profile),
        'cols': receipt_cols(profile),
        'paper_width': profile.paper_width if profile else '58mm',
        'font': profile.printer_font if profile else 'A',
        'logo_url': (profile.logo.url if (profile and profile.logo) else None),
    })


# FEATURE-039: WiFi network management (admin-only — the box is reached only over
# Tailscale-over-WiFi, so a bad change can sever access; see scripts/network).
@api_view(['GET'])
@permission_classes([IsAdmin])
def network_status(request):
    """Current WiFi + any pending/confirmed change awaiting the admin's signal."""
    return Response({
        'current': network_service.current_wifi(),
        'pending': network_service.get_state(),
    })


@api_view(['POST'])
@permission_classes([IsAdmin])
def network_apply(request):
    """Apply a new WiFi profile. Creates a NEW connection (never overwrites the
    working one) and starts a 60s confirmation window with auto-revert."""
    ssid = str(request.data.get('ssid', '')).strip()
    password = str(request.data.get('password', ''))
    if not ssid or len(ssid) > 32:
        return Response({'error': 'SSID must be 1–32 characters.'}, status=400)
    # WPA-PSK keys are 8–63 chars; empty password = open network.
    if password and not (8 <= len(password) <= 63):
        return Response({'error': 'WiFi password must be 8–63 characters.'}, status=400)
    ok, message = network_service.apply(ssid, password)
    if not ok:
        return Response({'error': message}, status=400)
    return Response({'detail': message, 'pending': network_service.get_state()})


@api_view(['POST'])
@permission_classes([IsAdmin])
def network_confirm(request):
    """Explicit success signal — admin reached this page over Tailscale on the new
    network and confirms. Cancels the pending auto-revert."""
    ok, message = network_service.confirm()
    if not ok:
        return Response({'error': message}, status=400)
    return Response({'detail': message, 'pending': network_service.get_state()})


# ============================================================================
# B13 — Reporting + remote ops
# ============================================================================

from decimal import Decimal as _Dec, ROUND_HALF_UP as _RHU

_MONEY_Q = _Dec('0.01')


def _money(value):
    """Decimal money as a 2dp string (matches ZReportSerializer convention —
    Decimals serialize as strings so no float precision is lost). Accepts a
    MoneyField Sum result (a Money instance), a Decimal, or None."""
    if value is None:
        value = 0
    if hasattr(value, 'amount'):   # djmoney Money
        value = value.amount
    return str(_Dec(str(value)).quantize(_MONEY_Q, rounding=_RHU))


def _aggregate_zreports(d_from, d_to):
    """FEATURE-013 Z aggregation, shared by the legacy from/to mode and the
    ISSUE-118 weekly mode.

    Filter parity with the original period report: every finalized ZReport
    whose business_date falls in [d_from, d_to] counts — there is NO
    is_official filter (pre-accreditation/UNOFFICIAL Z's are included, as
    they always were). Seed exclusion happens at Z finalize time (FLAG-047),
    so totals never include is_seed transactions.
    """
    reports = (
        ZReport.objects
        .filter(business_date__gte=d_from, business_date__lte=d_to)
        .select_related('shift', 'shift__cashier')
        .order_by('business_date', 'z_counter')
    )

    totals = {
        'gross': _Dec('0'), 'discount': _Dec('0'), 'vat': _Dec('0'),
        'net': _Dec('0'), 'cash': _Dec('0'), 'card': _Dec('0'),
        'gcash': _Dec('0'), 'maya': _Dec('0'),
    }
    txn_count = void_count = 0
    daily = []
    for z in reports:
        pb = z.payment_breakdown or {}
        totals['gross'] += z.gross_sales
        totals['discount'] += z.discount_total
        totals['vat'] += z.output_vat
        totals['net'] += z.net_sales
        for method in ('cash', 'card', 'gcash', 'maya'):
            totals[method] += _Dec(str(pb.get(method) or 0))
        txn_count += z.transaction_count
        void_count += z.voided_count
        daily.append({
            'date': z.business_date.strftime('%Y-%m-%d'),
            'z_counter': z.z_counter,
            'shift_id': z.shift_id,
            'cashier': z.shift.cashier.username if (z.shift and z.shift.cashier) else '—',
            'gross': _money(z.gross_sales),
            'net': _money(z.net_sales),
            'transaction_count': z.transaction_count,
            'void_count': z.voided_count,
        })
    return totals, txn_count, void_count, daily


def _live_today_snapshot(today):
    """ISSUE-118: live (X-style) numbers for the current PHT day — seed-free
    transactions belonging to still-open shifts, i.e. sales not yet frozen
    into a ZReport. Mirrors the xreport filters (status='completed' for
    sales, voids counted separately, refunds excluded by status)."""
    live_qs = PosTransaction.objects.filter(
        shift__is_open=True, is_seed=False, created_at__date=today,
    )
    completed = live_qs.filter(void=False, status='completed')
    agg = completed.aggregate(
        gross=Sum('total_amount'),
        discount=Sum('discount_amount'),
        vat=Sum('vat_amount'),
        cnt=Count('id'),
    )
    gross = _Dec(str(agg['gross'] or 0))
    snapshot = {
        'gross': gross,
        'discount': _Dec(str(agg['discount'] or 0)),
        'vat': _Dec(str(agg['vat'] or 0)),
        # X-report parity: net_sales == gross for the live portion.
        'net': gross,
        'transaction_count': agg['cnt'] or 0,
        'void_count': live_qs.filter(void=True).count(),
        'cash': _Dec('0'), 'card': _Dec('0'),
        'gcash': _Dec('0'), 'maya': _Dec('0'),
    }
    from .models import PaymentLine
    pl_rows = (
        PaymentLine.objects.filter(transaction__in=completed)
        .values('method')
        .annotate(total=Sum('amount'))
    )
    for r in pl_rows:
        if r['method'] in ('cash', 'card', 'gcash', 'maya'):
            snapshot[r['method']] = _Dec(str(r['total'] or 0))
    return snapshot


def _weekly_top_items(d_from, d_to):
    """ISSUE-118: top items for the week, by quantity. Same filters as the
    dashboard top_items query (void=False, is_seed=False) over the
    PHT-date range; covers finalized and live transactions alike."""
    rows = (
        PosTransactionItem.objects.filter(
            pos_transaction__created_at__date__gte=d_from,
            pos_transaction__created_at__date__lte=d_to,
            pos_transaction__void=False,
            pos_transaction__is_seed=False,  # FLAG-047
        )
        .values('item__name')
        .annotate(total_quantity=Sum('quantity'), total_revenue=Sum('subtotal'))
        .order_by('-total_quantity')[:5]
    )
    return [
        {
            'name': r['item__name'],
            'quantity': r['total_quantity'],
            'revenue': _money(r['total_revenue']),
        }
        for r in rows
    ]


def _weekly_worst_sellers(d_from, d_to):
    """ISSUE-121: bottom 5 active items by quantity sold in the window —
    zero and near-zero sellers, the owner's menu-pruning signal. Items with
    no sales at all rank first (quantity 0). Same sale filters as
    _weekly_top_items (void=False, is_seed=False)."""
    sale_filter = Q(
        postransactionitem__pos_transaction__created_at__date__gte=d_from,
        postransactionitem__pos_transaction__created_at__date__lte=d_to,
        postransactionitem__pos_transaction__void=False,
        postransactionitem__pos_transaction__is_seed=False,  # FLAG-047
    )
    rows = (
        Item.objects.filter(is_active=True)
        .annotate(
            total_quantity=Coalesce(
                Sum('postransactionitem__quantity', filter=sale_filter), 0
            ),
            total_revenue=Sum('postransactionitem__subtotal', filter=sale_filter),
        )
        .order_by('total_quantity', 'name')[:5]
    )
    return [
        {
            'name': r.name,
            'quantity': r.total_quantity,
            'revenue': _money(r.total_revenue),
        }
        for r in rows
    ]


def _weekly_busiest_hour(d_from, d_to):
    """ISSUE-121: busiest PHT hour of the week — completed, non-seed
    transaction count bucketed by local hour (same bucketing as the
    FEATURE-014 insights peak_hours, scoped to the week). None when the
    week has no transactions."""
    created_times = (
        PosTransaction.objects
        .filter(
            created_at__date__gte=d_from, created_at__date__lte=d_to,
            status='completed', void=False, is_seed=False,
        )
        .values_list('created_at', flat=True)
    )
    hour_counts = [0] * 24
    for created in created_times:
        hour_counts[timezone.localtime(created).hour] += 1
    busiest = max(range(24), key=lambda h: hour_counts[h])
    if not hour_counts[busiest]:
        return None
    return {'hour': busiest, 'count': hour_counts[busiest]}


def _cashier_summary(d_from, d_to):
    """Per-cashier txn count, gross, and void count over a PHT date range,
    seed-free. Shared by FEATURE-014 insights (current month) and the
    ISSUE-121 weekly report (Sat–Fri week)."""
    rows = (
        PosTransaction.objects
        .filter(
            created_at__date__gte=d_from, created_at__date__lte=d_to,
            is_seed=False,
        )
        .values('cashier__username')
        .annotate(
            txns=Count('id', filter=Q(void=False, status='completed')),
            gross=Sum('total_amount', filter=Q(void=False, status='completed')),
            voids=Count('id', filter=Q(void=True)),
        )
        .order_by('-gross')
    )
    return [
        {
            'name': r['cashier__username'] or '—',
            'txns': r['txns'],
            'gross': _money(r['gross']),
            'voids': r['voids'],
        }
        for r in rows
    ]


def _inventory_notices(today):
    """ISSUE-121: current-state inventory notices for the weekly report.
    Active items only. Out-of-stock items are listed once (not repeated in
    low-stock).

    ISSUE-121-FU-C: the expiry notice was removed from the weekly report. The
    expiry logic itself lives elsewhere in the app and is untouched — it just no
    longer feeds this report, so no expiry data is computed or returned here.
    """
    active = Item.objects.filter(is_active=True)

    low_stock = [
        {'id': i.id, 'name': i.name, 'stock': i.stock,
         'threshold': i.low_stock_threshold}
        for i in active.filter(
            stock__gt=0, stock__lte=F('low_stock_threshold')
        ).order_by('stock', 'name')
    ]
    out_of_stock = [
        {'id': i.id, 'name': i.name}
        for i in active.filter(stock=0).order_by('name')
    ]
    return {
        'low_stock': low_stock,
        'out_of_stock': out_of_stock,
    }


def _weekly_sold_lines(d_from, d_to):
    """FEATURE-054: sold (non-void, non-seed) line items in the PHT window —
    the shared basis for COGS and profit-by-item. Same filters as
    _weekly_top_items."""
    return PosTransactionItem.objects.filter(
        pos_transaction__created_at__date__gte=d_from,
        pos_transaction__created_at__date__lte=d_to,
        pos_transaction__void=False,
        pos_transaction__is_seed=False,  # FLAG-047
    )


def _weekly_cogs(d_from, d_to):
    """FEATURE-054: recipe-valued COGS for the window from the cost-at-sale
    snapshot (PosTransactionItem.unit_cost), so it stays historically accurate
    as ingredient costs drift. Returns (cogs, costed_lines, total_lines) —
    the ratio lets the UI note partial coverage for pre-snapshot history."""
    cogs = _Dec('0')
    costed = 0
    total = 0
    for l in _weekly_sold_lines(d_from, d_to).values('quantity', 'unit_cost'):
        total += 1
        if l['unit_cost'] is not None:
            cogs += _Dec(str(l['unit_cost'])) * _Dec(str(l['quantity']))
            costed += 1
    return cogs, costed, total


def _weekly_profit_by_item(d_from, d_to):
    """FEATURE-054: per-item revenue / COGS / gross profit / margin, ordered by
    gross profit — shows what actually MAKES money, not just what sells most.
    Cost uses the per-line cost-at-sale snapshot; lines without one contribute 0
    cost (overstating their profit until snapshots accumulate)."""
    agg = {}
    for l in _weekly_sold_lines(d_from, d_to).values(
        'item__name', 'quantity', 'subtotal', 'unit_cost'
    ):
        a = agg.setdefault(
            l['item__name'], {'qty': 0, 'revenue': _Dec('0'), 'cost': _Dec('0')}
        )
        a['qty'] += l['quantity']
        a['revenue'] += _Dec(str(l['subtotal']))
        if l['unit_cost'] is not None:
            a['cost'] += _Dec(str(l['unit_cost'])) * _Dec(str(l['quantity']))
    out = []
    for name, a in agg.items():
        profit = a['revenue'] - a['cost']
        margin = (
            float((profit / a['revenue'] * 100).quantize(_MONEY_Q, rounding=_RHU))
            if a['revenue'] > 0 else None
        )
        out.append({
            'name': name,
            'quantity': a['qty'],
            'revenue': _money(a['revenue']),
            'cost': _money(a['cost']),
            'profit': _money(profit),
            'margin_pct': margin,
        })
    out.sort(key=lambda r: _Dec(r['profit']), reverse=True)
    return out


def _ingredient_reorder_list():
    """FEATURE-054: ingredients at or below par — the owner's actionable
    'buy this' list. Only ingredients with a par set (par_level > 0) qualify,
    scarcest first. This is the ingredient-level counterpart to the item
    low-stock notices, and the right signal now that recipe items have no
    meaningful per-drink on-hand."""
    rows = (
        Ingredient.objects.filter(
            is_active=True, par_level__gt=0, current_stock__lte=F('par_level')
        ).select_related('unit').order_by('current_stock', 'name')
    )
    return [
        {
            'id': i.id, 'name': i.name,
            'current_stock': float(i.current_stock),
            'par_level': float(i.par_level),
            'unit': i.unit.abbreviation if i.unit else '',
        }
        for i in rows
    ]


def _weekly_restock_costs(d_from, d_to):
    """ISSUE-121-FU-B: cost (COGS/expense side) of everything restocked within
    the Sat–Fri window.

    SOURCE NOTE: the IngredientLog ledger defines a 'restock' action choice but
    nothing ever writes it — restocks are recorded only in IngredientRestockLog,
    which additionally snapshots cost_per_unit at restock time. So cost is read
    from that table as quantity_added * cost_per_unit (historically accurate,
    better than deriving from the ingredient's current cost_per_unit). Restocks
    are not transactions, so is_seed/void exclusions do not apply. Windowed on
    the PHT date of each restock, mirroring the sales queries' __date lookup.
    """
    rows = (
        IngredientRestockLog.objects
        .filter(date__date__gte=d_from, date__date__lte=d_to)
        .select_related('ingredient')
        .order_by('ingredient__name', 'date')
    )
    total = _Dec('0')
    by_ingredient = {}
    for r in rows:
        line_cost = (_Dec(str(r.quantity_added)) * _Dec(str(r.cost_per_unit)))
        total += line_cost
        agg = by_ingredient.setdefault(
            r.ingredient.name,
            {'name': r.ingredient.name, 'quantity': _Dec('0'), 'cost': _Dec('0')},
        )
        agg['quantity'] += _Dec(str(r.quantity_added))
        agg['cost'] += line_cost
    breakdown = [
        {'name': v['name'],
         'quantity': float(v['quantity']),
         'cost': _money(v['cost'])}
        for v in sorted(by_ingredient.values(), key=lambda x: -x['cost'])
    ]
    return {'total': _money(total), 'by_ingredient': breakdown}


def _weekly_restock_detail(d_from, d_to):
    """ISSUE-121-FU-F: per-restock detail rows for the Sat–Fri window.

    One row per IngredientRestockLog entry (the _weekly_restock_costs summary
    aggregates these by ingredient; this is the line-item view behind it).
    Ordered by date so the table reads chronologically. ``cost`` is the
    historical snapshot quantity_added * cost_per_unit. ``recorded_by`` is null
    on rows created before that field existed → rendered '—', never an error.
    Same PHT window resolution as the rest of the report.
    """
    rows = (
        IngredientRestockLog.objects
        .filter(date__date__gte=d_from, date__date__lte=d_to)
        .select_related('ingredient', 'ingredient__unit', 'recorded_by')
        .order_by('date')
    )
    detail = []
    for r in rows:
        line_cost = _Dec(str(r.quantity_added)) * _Dec(str(r.cost_per_unit))
        unit_abbr = getattr(getattr(r.ingredient, 'unit', None), 'abbreviation', '') or ''
        who = getattr(r.recorded_by, 'username', None) or '—'
        detail.append({
            'date': timezone.localdate(r.date).strftime('%Y-%m-%d'),
            'ingredient': r.ingredient.name,
            'quantity': float(r.quantity_added),
            'unit': unit_abbr,
            'cost': _money(line_cost),
            'recorded_by': who,
        })
    return detail


def _avg_ticket(gross, txn_count):
    """Average ticket as a money string; None when there are no
    transactions (null-safe for the WoW delta on an empty week)."""
    if not txn_count:
        return None
    return _money(_Dec(str(gross if not hasattr(gross, 'amount') else gross.amount)) / txn_count)


def _resolve_week(anchor):
    """ISSUE-121-FU-A: snap any date to its SATURDAY→FRIDAY window (PHT).

    The client's handwritten cycle collects 7 daily reports each Saturday, so
    the business week runs Sat→Fri. date.weekday() has Mon=0..Sat=5; the number
    of days since the most recent Saturday is (weekday - 5) % 7. This is the
    single source of weekly boundary math — the daily chart, WoW comparison,
    and top/bottom queries all derive their range from the returned pair.
    """
    days_since_sat = (anchor.weekday() - 5) % 7
    week_start = anchor - timedelta(days=days_since_sat)   # Saturday
    week_end = week_start + timedelta(days=6)              # Friday
    return week_start, week_end


def _weekly_report(week_param):
    """ISSUE-118: Weekly Performance Report HTTP wrapper around _weekly_payload."""
    payload, error = _weekly_payload(week_param)
    if error is not None:
        return Response(error, status=status.HTTP_400_BAD_REQUEST)
    return Response(payload)


def _weekly_payload(week_param):
    """ISSUE-118: Weekly Performance Report payload (dict).

    The week is SATURDAY→FRIDAY in PHT (ISSUE-121-FU-A), derived from any date
    inside it. Finalized ZReports provide completed days; the current PHT day
    adds live open-shift (X-style) data when the week is in progress. Days are
    labelled final/live so the two sources are never silently mixed.

    Returns (payload_dict, None) on success or (None, error_dict) on a bad week
    param, so both the JSON view and the thermal-print path (ISSUE-121-FU-D)
    build from exactly the same data.
    """
    try:
        anchor = datetime.strptime(week_param, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return None, {'error': 'week must be YYYY-MM-DD.'}
    week_start, week_end = _resolve_week(anchor)
    today = timezone.localdate()
    live_day = today if week_start <= today <= week_end else None

    totals, txn_count, void_count, daily = _aggregate_zreports(week_start, week_end)

    # Bucket the per-shift Z rows into per-day rows.
    by_day = {}
    for row in daily:
        d = by_day.setdefault(row['date'], {
            'gross': _Dec('0'), 'net': _Dec('0'),
            'transaction_count': 0, 'void_count': 0, 'z_count': 0,
        })
        d['gross'] += _Dec(row['gross'])
        d['net'] += _Dec(row['net'])
        d['transaction_count'] += row['transaction_count']
        d['void_count'] += row['void_count']
        d['z_count'] += 1

    live = _live_today_snapshot(today) if live_day else None
    if live:
        for key in ('gross', 'discount', 'vat', 'net', 'cash', 'card', 'gcash', 'maya'):
            totals[key] += live[key]
        txn_count += live['transaction_count']
        void_count += live['void_count']

    days = []
    for i in range(7):
        d = week_start + timedelta(days=i)
        key = d.strftime('%Y-%m-%d')
        bucket = by_day.get(key)
        gross = bucket['gross'] if bucket else _Dec('0')
        net = bucket['net'] if bucket else _Dec('0')
        d_txns = bucket['transaction_count'] if bucket else 0
        d_voids = bucket['void_count'] if bucket else 0
        if d == live_day:
            status_label = 'live'
            gross += live['gross']
            net += live['net']
            d_txns += live['transaction_count']
            d_voids += live['void_count']
        elif d > today:
            status_label = 'upcoming'
        else:
            status_label = 'final'
        days.append({
            'date': key,
            'day_name': d.strftime('%a'),
            'status': status_label,
            # A live day can also hold already-finalized Z's (closed shifts).
            'finalized_shifts': bucket['z_count'] if bucket else 0,
            'gross': _money(gross),
            'net': _money(net),
            'transaction_count': d_txns,
            'void_count': d_voids,
        })

    # Week-over-week delta vs the previous Sat–Fri (fully in the past, so
    # Z-only by construction). Omitted (null) when the prior week is empty.
    prev_start = week_start - timedelta(days=7)
    prev_end = week_start - timedelta(days=1)
    prev_totals, prev_txns, _pv, prev_daily = _aggregate_zreports(prev_start, prev_end)
    previous_week = None
    if prev_daily:
        delta = totals['gross'] - prev_totals['gross']
        pct = None
        if prev_totals['gross']:
            pct = float(
                (delta / prev_totals['gross'] * 100).quantize(_MONEY_Q, rounding=_RHU)
            )
        previous_week = {
            'week_start': prev_start.strftime('%Y-%m-%d'),
            'week_end': prev_end.strftime('%Y-%m-%d'),
            'gross_total': _money(prev_totals['gross']),
            # ISSUE-121: per-metric WoW baselines (net / txns / avg ticket).
            'net_total': _money(prev_totals['net']),
            'transaction_count': prev_txns,
            'avg_ticket': _avg_ticket(prev_totals['gross'], prev_txns),
            'delta': _money(delta),
            'delta_pct': pct,
        }

    # ISSUE-121-FU-B/F/G: restock summary + line-item detail, and the cash-flow
    # headline. Cash flow is cash-basis (drawer in − drawer out), NOT profit or
    # COGS — restock spend is money out of the drawer this week. It can go
    # negative on a heavy-restock week and that is expected.
    restock_costs = _weekly_restock_costs(week_start, week_end)
    restock_detail = _weekly_restock_detail(week_start, week_end)
    net_cash_flow = _money(_Dec(_money(totals['net'])) - _Dec(restock_costs['total']))

    # FEATURE-054: recipe-valued COGS + gross profit/margin. This is a
    # PROFITABILITY view (what the cafe earns after the cost of goods actually
    # sold), distinct from Net Cash Flow above (a cash-basis drawer figure).
    cogs, costed_lines, total_lines = _weekly_cogs(week_start, week_end)
    net_rev = _Dec(_money(totals['net']))
    gross_profit = net_rev - cogs
    gross_margin_pct = (
        float((gross_profit / net_rev * 100).quantize(_MONEY_Q, rounding=_RHU))
        if net_rev > 0 else None
    )
    profitability = {
        'cogs': _money(cogs),
        'gross_profit': _money(gross_profit),
        'gross_margin_pct': gross_margin_pct,
        # Coverage of the cost-at-sale snapshot across the window's lines
        # (1.0 once all sales postdate FEATURE-054); lets the UI caveat history.
        'costed_line_ratio': (
            round(costed_lines / total_lines, 3) if total_lines else None
        ),
    }

    payload = {
        'week_start': week_start.strftime('%Y-%m-%d'),
        'week_end': week_end.strftime('%Y-%m-%d'),
        'live_date': live_day.strftime('%Y-%m-%d') if live_day else None,
        'days': days,
        'summary': {
            'gross_total': _money(totals['gross']),
            'discount_total': _money(totals['discount']),
            'vat_amount': _money(totals['vat']),
            'net_total': _money(totals['net']),
            'cash_total': _money(totals['cash']),
            'card_total': _money(totals['card']),
            'gcash_total': _money(totals['gcash']),
            'maya_total': _money(totals['maya']),
            'transaction_count': txn_count,
            'void_count': void_count,
            'avg_ticket': _avg_ticket(totals['gross'], txn_count),
        },
        'top_items': _weekly_top_items(week_start, week_end),
        # FEATURE-054: profitability view + owner-determining data.
        'profitability': profitability,
        'profit_by_item': _weekly_profit_by_item(week_start, week_end),
        'ingredient_reorder': _ingredient_reorder_list(),
        # ISSUE-121: owner-facing additions.
        'worst_sellers': _weekly_worst_sellers(week_start, week_end),
        'busiest_hour': _weekly_busiest_hour(week_start, week_end),
        'cashiers': _cashier_summary(week_start, week_end),
        'inventory_notices': _inventory_notices(today),
        # ISSUE-121-FU-B: restock cost (expense side) for the window.
        'restock_costs': restock_costs,
        # ISSUE-121-FU-F: per-restock line-item detail (date, ingredient, qty,
        # cost, recorded_by) ordered by date.
        'restock_detail': restock_detail,
        # ISSUE-121-FU-G: net cash flow headline (net sales − restock spend).
        'net_cash_flow': net_cash_flow,
        'previous_week': previous_week,
    }
    return payload, None


@api_view(['GET'])
@permission_classes([IsManagerOrAbove])
def period_report(request):
    """FEATURE-013 / ISSUE-118: period report, now weekly-first.

    ?week=YYYY-MM-DD (any date inside the week) → Weekly Performance Report:
    Sat–Fri PHT, finalized ZReports + live open-shift data for today, payment
    mix, top items, and week-over-week delta (see _weekly_report).

    ?from=&to= keeps the original FEATURE-013 contract: aggregates across the
    immutable ZReports whose business_date (PHT-localdate of the shift's
    opened_at) falls within [from, to]. ZReport columns are built seed-free at
    finalize time (FLAG-047), so period totals never include is_seed
    transactions. Returns a summary plus one daily row per ZReport/shift for
    drill-down. 400 when from > to; an empty (not 404) result when the range
    holds no ZReports.
    """
    week = request.query_params.get('week')
    if week is not None:
        return _weekly_report(week)

    frm = request.query_params.get('from')
    to = request.query_params.get('to')
    try:
        d_from = datetime.strptime(frm, '%Y-%m-%d').date()
        d_to = datetime.strptime(to, '%Y-%m-%d').date()
    except (TypeError, ValueError):
        return Response(
            {'error': 'from and to are required as YYYY-MM-DD.'},
            status=status.HTTP_400_BAD_REQUEST,
        )
    if d_from > d_to:
        return Response(
            {'error': 'from must not be after to.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    totals, txn_count, void_count, daily = _aggregate_zreports(d_from, d_to)

    return Response({
        'from': d_from.strftime('%Y-%m-%d'),
        'to': d_to.strftime('%Y-%m-%d'),
        'summary': {
            'gross_total': _money(totals['gross']),
            'discount_total': _money(totals['discount']),
            'vat_amount': _money(totals['vat']),
            'net_total': _money(totals['net']),
            'cash_total': _money(totals['cash']),
            'card_total': _money(totals['card']),
            'gcash_total': _money(totals['gcash']),
            'maya_total': _money(totals['maya']),
            'transaction_count': txn_count,
            'void_count': void_count,
        },
        'daily': daily,
    })


@api_view(['POST'])
@permission_classes([IsManagerOrAbove])
def period_print(request):
    """ISSUE-121-FU-D: print the weekly report on the ESC/POS thermal printer.

    Same fire-and-forget mechanism as the X/Z report prints (threaded, never
    blocks the response). Recomputes the payload server-side via _weekly_payload
    so the paper and the on-screen report are the same data, then hands it to
    receipt_service.print_weekly_report (single-column thermal layout).
    Intended physical workflow: 7 daily Z reports + this weekly report on top.
    """
    week = request.query_params.get('week') or request.data.get('week')
    payload, error = _weekly_payload(week)
    if error is not None:
        return Response(error, status=status.HTTP_400_BAD_REQUEST)

    from .receipt_service import print_weekly_report
    import threading
    threading.Thread(
        target=print_weekly_report, args=(payload,), daemon=True
    ).start()
    return Response({'status': 'print queued'})


@api_view(['GET'])
@permission_classes([IsManagerOrAbove])
def insights_report(request):
    """FEATURE-014: owner insight surface.

    peak_hours — completed, non-seed transaction count bucketed by PHT
    hour-of-day over the last 30 days (all 24 hours returned so the chart has a
    full axis). cashiers — per-cashier txn count, gross, and void count for the
    current calendar month (PHT). All money aggregates exclude is_seed rows.
    """
    today = timezone.localdate()
    thirty_days_ago = today - timedelta(days=29)

    # Peak hours — bucket in PHT to avoid UTC/local drift near midnight.
    created_times = (
        PosTransaction.objects
        .filter(
            created_at__date__gte=thirty_days_ago,
            status='completed', void=False, is_seed=False,
        )
        .values_list('created_at', flat=True)
    )
    hour_counts = [0] * 24
    for created in created_times:
        hour_counts[timezone.localtime(created).hour] += 1
    peak_hours = [{'hour': h, 'count': hour_counts[h]} for h in range(24)]

    # Per-cashier summary — current month, seed-free (shared with the
    # ISSUE-121 weekly report, which scopes it to a Sat–Fri week).
    month_start = today.replace(day=1)
    cashiers = _cashier_summary(month_start, today)

    return Response({'peak_hours': peak_hours, 'cashiers': cashiers})


@api_view(['GET'])
@permission_classes([IsAdmin])
def remote_status(request):
    """FEATURE-026 (succeeds FLAG-055): phone-friendly live cafe status for the
    owner over Tailscale. Poll-friendly JSON — no WebSocket.

    Returns the current open shift (if any), today's seed-free gross (current
    PHT calendar day), the last backup timestamp (same source as FEATURE-020
    local status), and the last 5 transactions with no PII.
    """
    today = timezone.localdate()

    shift = Shift.objects.filter(is_open=True).select_related('cashier').order_by('-opened_at').first()
    today_qs = PosTransaction.objects.filter(
        created_at__date=today, status='completed', void=False, is_seed=False,
    )
    today_gross = today_qs.aggregate(t=Sum('total_amount'))['t'] or 0

    shift_data = None
    if shift:
        shift_txn_today = PosTransaction.objects.filter(
            shift=shift, created_at__date=today,
            status='completed', void=False, is_seed=False,
        ).count()
        shift_data = {
            'id': shift.id,
            'cashier': shift.cashier.username if shift.cashier else '—',
            'opened_at': shift.opened_at.isoformat(),
            'transaction_count_today': shift_txn_today,
        }

    recent = (
        PosTransaction.objects
        .filter(status='completed', void=False, is_seed=False)
        .order_by('-created_at')[:5]
    )
    recent_data = [
        {
            'time': t.created_at.isoformat(),
            'amount': _money(t.total_amount),
            'payment_method': t.payment_method,
        }
        for t in recent
    ]

    backup = _latest_backup_info()

    return Response({
        'shift_open': shift is not None,
        'shift': shift_data,
        'today_gross': _money(today_gross),
        'last_backup': backup['mtime'] if backup else None,
        'recent_transactions': recent_data,
        'server_time': timezone.now().isoformat(),
    })


def admin_lockdown(request):
    """FIX-PENDING-20: explicit 404 for /admin/lockdown/.

    Without this route the path fell through to the PWA catch-all, which served
    the app shell (a 200) instead of a real 404. Registered before the Django
    admin include in pos_config/urls.py so it shadows the admin namespace.
    """
    raise Http404
