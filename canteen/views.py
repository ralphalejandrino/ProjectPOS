from rest_framework import viewsets, status, filters
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.response import Response
from rest_framework.exceptions import ValidationError
from django.db import transaction as db_transaction
from django.utils import timezone
from django.shortcuts import get_object_or_404
from .services import create_pos_transaction, _restore_ingredients
from .models import (
    ItemCategory, Item, ItemLog, PosTransaction, PosTransactionItem, Shift,
    VariantGroup, VariantOption, CategoryVariantGroup, ProductVariantGroup,
    TransactionItemVariant, BusinessProfile, RecipeIngredient, Ingredient,
    IngredientUnit, Supplier, IngredientRestockLog,
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
)
from django.db.models import Sum, Count, F, FloatField
from django.db.models.functions import TruncDate
from datetime import datetime, timedelta, date, timezone as dt_tz
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework.pagination import PageNumberPagination
from rest_framework.views import APIView
from .permissions import IsManagerOrAbove, IsCashierOrAbove
import csv, io, logging

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

    def get_permissions(self):
        if self.action in ['list', 'retrieve']:
            return [IsCashierOrAbove()]
        return [IsManagerOrAbove()]

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
                'discount_type': t.discount_type or '',
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
                'voided_by': transaction.voided_by.get_full_name() or transaction.voided_by.username if transaction.voided_by else None,
                'voided_at': transaction.voided_at.isoformat() if transaction.voided_at else None,
                'void_reason': transaction.purpose_of_void or '',
                'discount_amount':    float(transaction.discount_amount) if transaction.discount_amount else 0.0,
                'discount_type':      transaction.discount_type or '',
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
                cash_received=request.data.get('cash_received'),
                gcash_reference=request.data.get('gcash_reference', ''),
                maya_reference=request.data.get('maya_reference', ''),
                card_reference=request.data.get('card_reference', ''),
                customer_phone=request.data.get('customer_phone', ''),
                discount_amount=request.data.get('discount_amount', 0),
                discount_type=request.data.get('discount_type', ''),
                discount_id_number=request.data.get('discount_id_number', ''),
            )
            
            return Response({
                'id': str(transaction.id),
                'success': True,
                'transaction_no': transaction.transaction_no,
                'total': float(transaction.total_amount.amount),
                'payment_method': payment_method,
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
                _bp = BusinessProfile.objects.first()
                if not _bp or _bp.track_inventory:
                    for item_entry in transaction.items.all():
                        Item.objects.filter(pk=item_entry.item.pk).update(
                            stock=F('stock') + item_entry.quantity
                        )
                        # Ingredient stock restore
                        _restore_ingredients(item_entry.item, item_entry, item_entry.quantity)

                transaction.void = True
                transaction.voided_at = timezone.now()
                transaction.status = 'void'
                transaction.voided_by = request.user
                transaction.purpose_of_void = request.data.get('reason', 'No reason provided')
                transaction.save()

                return Response({'success': True, 'message': 'Transaction voided successfully'})
        except PosTransaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=status.HTTP_404_NOT_FOUND)
        except Exception as e:
            logger.exception('Void failed')
            return Response({'error': 'An unexpected error occurred.'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

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
        if not profile.printer_enabled or not profile.printer_ip:
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
            discount_type = ''
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

    @action(detail=False, methods=['post'], permission_classes=[IsManagerOrAbove],
            url_path='print_zreport')
    def print_zreport(self, request):
        """Fire-and-forget ESC/POS print of Z-report summary."""
        from .receipt_service import print_zreport_summary
        import threading
        threading.Thread(target=print_zreport_summary, args=(request.data,), daemon=True).start()
        return Response({'status': 'print queued'})

    @action(detail=False, methods=['post'], permission_classes=[IsCashierOrAbove],
            url_path='print_xreport')
    def print_xreport(self, request):
        """Fire-and-forget ESC/POS print of X-report summary."""
        from .receipt_service import print_xreport_summary
        import threading
        threading.Thread(target=print_xreport_summary, args=(request.data,), daemon=True).start()
        return Response({'status': 'print queued'})

    @action(detail=False, methods=['get'], permission_classes=[IsManagerOrAbove])
    def zreport(self, request):
        """End-of-day Z-report"""
        from django.db.models import Sum, Count, Min, Max
        from django.db.models.functions import ExtractHour
        import datetime as dt

        date_str = request.query_params.get('date')
        try:
            report_date = dt.date.fromisoformat(date_str) if date_str else timezone.now().date()
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        all_qs = PosTransaction.objects.filter(created_at__date=report_date)
        completed = all_qs.filter(void=False, status='completed')
        voided = all_qs.filter(void=True)

        # --- Totals ---
        _gross_raw = completed.aggregate(total=Sum('total_amount'))['total'] or 0
        gross = float(_gross_raw)  # float kept for JSON serialisation — Decimal used for VAT math only
        transaction_count = completed.count()
        average_transaction = round(gross / transaction_count, 2) if transaction_count else 0

        # --- VAT breakdown (rate read from BusinessProfile) ---
        from .models import BusinessProfile as _BP
        from decimal import Decimal, ROUND_HALF_UP
        _bp = _BP.get_instance()
        if _bp.vat_enabled:
            _gross_d = Decimal(str(_gross_raw))
            _vat_rate_d = Decimal(str(_bp.vat_rate))
            vat_amount = float(
                (_gross_d * _vat_rate_d / (Decimal('100') + _vat_rate_d))
                .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            )
            net_of_vat = float(
                (_gross_d - Decimal(str(vat_amount)))
                .quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
            )
        else:
            vat_amount = 0.0
            net_of_vat = gross

        # --- Voids ---
        void_count = voided.count()
        void_total = float(voided.aggregate(total=Sum('total_amount'))['total'] or 0)

        # --- Void list with employee info ---
        void_list = []
        for txn in voided.select_related('voided_by').order_by('updated_at'):
            amount = float(txn.total_amount.amount if hasattr(txn.total_amount, 'amount') else txn.total_amount)
            void_list.append({
                'transaction_no': txn.transaction_no,
                'amount': amount,
                'voided_by': txn.voided_by.get_full_name() or txn.voided_by.username if txn.voided_by else 'Unknown',
                'voided_at': txn.voided_at.strftime('%I:%M %p') if txn.voided_at else '',
            })

        # --- Discount breakdown ---
        discount_summary = completed.filter(discount_amount__gt=0).values('discount_type').annotate(
            count=Count('id'), total_discount=Sum('discount_amount')
        ).order_by('discount_type')
        _label_map = {'sc': 'Senior Citizen', 'pwd': 'PWD', 'promo': 'Promo'}
        discount_breakdown = [
            {
                'type': d['discount_type'] or 'other',
                'label': _label_map.get(d['discount_type'], d['discount_type'] or 'Other'),
                'count': d['count'],
                'total_discount': float(d['total_discount'] or 0),
            }
            for d in discount_summary
        ]
        total_discounts_given = float(
            completed.filter(discount_amount__gt=0).aggregate(total=Sum('discount_amount'))['total'] or 0
        )

        # --- Net sales (voids already excluded from gross; void_total is informational only) ---
        net_sales = round(gross, 2)

        # --- By payment method ---
        by_method_rows = []
        for method in ['cash', 'gcash', 'maya']:
            mqs = completed.filter(payment_method=method)
            by_method_rows.append({
                'payment_method': method,
                'count': mqs.count(),
                'subtotal': float(mqs.aggregate(t=Sum('total_amount'))['t'] or 0),
            })

        # --- Cash in drawer ---
        cash_expected = next((r['subtotal'] for r in by_method_rows if r['payment_method'] == 'cash'), 0)

        # --- By cashier (with void count) ---
        from django.db.models import Q
        cashier_rows = (
            all_qs.filter(cashier__isnull=False)
            .values('cashier__id', 'cashier__username', 'cashier__first_name', 'cashier__last_name')
            .annotate(
                count=Count('id', filter=Q(void=False)),
                subtotal=Sum('total_amount', filter=Q(void=False)),
                void_count=Count('id', filter=Q(void=True)),
            )
            .order_by('cashier__username')
        )
        by_cashier = [
            {
                'name': (f"{r['cashier__first_name']} {r['cashier__last_name']}".strip()
                         or r['cashier__username']),
                'count': r['count'],
                'subtotal': float(r['subtotal'] or 0),
                'void_count': r['void_count'],
            }
            for r in cashier_rows
        ]

        # --- Top selling items ---
        from .models import PosTransactionItem
        top_items = (
            PosTransactionItem.objects
            .filter(pos_transaction__created_at__date=report_date, pos_transaction__void=False)
            .values('item__name')
            .annotate(units_sold=Sum('quantity'), revenue=Sum('subtotal'))
            .order_by('-units_sold')[:5]
        )
        top_items_data = [
            {
                'name': row['item__name'],
                'units_sold': row['units_sold'],
                'revenue': float(row['revenue'] or 0),
            }
            for row in top_items
        ]

        # --- Total items sold ---
        total_items_sold = int(
            PosTransactionItem.objects
            .filter(pos_transaction__created_at__date=report_date, pos_transaction__void=False)
            .aggregate(total=Sum('quantity'))['total'] or 0
        )

        # --- Busiest hour ---
        busiest = (
            completed.annotate(hour=ExtractHour('created_at'))
            .values('hour')
            .annotate(count=Count('id'))
            .order_by('-count')
            .first()
        )
        busiest_hour = busiest['hour'] if busiest else None

        # --- First / last transaction time ---
        times = completed.aggregate(first=Min('created_at'), last=Max('created_at'))
        first_txn_time = times['first'].isoformat() if times['first'] else None
        last_txn_time = times['last'].isoformat() if times['last'] else None

        # --- Opening / closing transaction numbers ---
        ordered = completed.order_by('created_at')
        opening_txn = ordered.first()
        closing_txn = ordered.last()

        # --- Report metadata ---
        generated_by = request.user.get_full_name() or request.user.username

        return Response({
            'date': str(report_date),
            'generated_at': timezone.now().isoformat(),
            'generated_by': generated_by,
            'transaction_count': transaction_count,
            'total_items_sold': total_items_sold,
            'gross_sales': gross,
            'void_count': void_count,
            'void_total': void_total,
            'net_sales': net_sales,
            'vat_amount': vat_amount,
            'net_of_vat': net_of_vat,
            'average_transaction': average_transaction,
            'cash_expected': cash_expected,
            'first_txn_time': first_txn_time,
            'last_txn_time': last_txn_time,
            'opening_txn_no': opening_txn.transaction_no if opening_txn else None,
            'closing_txn_no': closing_txn.transaction_no if closing_txn else None,
            'busiest_hour': busiest_hour,
            'by_method': by_method_rows,
            'by_cashier': by_cashier,
            'top_items': top_items_data,
            'void_list': void_list,
            'discount_breakdown': discount_breakdown,
            'total_discounts_given': total_discounts_given,
        })

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

        shift_qs = PosTransaction.objects.filter(
            shift=shift
        )
        completed = shift_qs.filter(void=False, status='completed')
        voided = shift_qs.filter(void=True)

        gross = float(completed.aggregate(
            total=Sum('total_amount'))['total'] or 0)
        transaction_count = completed.count()
        void_count = voided.count()
        void_total = float(voided.aggregate(
            total=Sum('total_amount'))['total'] or 0)
        net_sales = round(gross, 2)
        average_transaction = round(
            gross / transaction_count, 2) if transaction_count else 0

        by_method = []
        for method in ['cash', 'gcash', 'maya']:
            mqs = completed.filter(payment_method=method)
            subtotal = float(
                mqs.aggregate(t=Sum('total_amount'))['t'] or 0)
            by_method.append({
                'payment_method': method,
                'count': mqs.count(),
                'subtotal': subtotal,
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
            'net_sales': net_sales,
            'by_payment_method': by_method,
        })

# ============================================================================
# VARIANT VIEWSETS
# ============================================================================

class VariantGroupViewSet(viewsets.ModelViewSet):
    queryset = VariantGroup.objects.prefetch_related('options').all()
    serializer_class = VariantGroupSerializer
    permission_classes = [IsManagerOrAbove]

    @action(detail=True, methods=['patch'], url_path='reorder-options')
    def reorder_options(self, request, pk=None):
        group = self.get_object()
        order = request.data.get('order', [])  # list of {id, sort_order}
        for entry in order:
            VariantOption.objects.filter(id=entry['id'], group=group).update(sort_order=entry['sort_order'])
        return Response({'status': 'reordered'})


class VariantOptionViewSet(viewsets.ModelViewSet):
    serializer_class = VariantOptionSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        return VariantOption.objects.filter(group_id=self.kwargs['group_pk'])

    def perform_create(self, serializer):
        group = get_object_or_404(VariantGroup, pk=self.kwargs['group_pk'])
        serializer.save(group=group)


class CategoryVariantGroupViewSet(viewsets.ModelViewSet):
    serializer_class = CategoryVariantGroupSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        return CategoryVariantGroup.objects.filter(category_id=self.kwargs['category_pk']).select_related('group')

    def perform_create(self, serializer):
        category = get_object_or_404(ItemCategory, pk=self.kwargs['category_pk'])
        group_id = self.request.data.get('group_id')
        group = get_object_or_404(VariantGroup, pk=group_id)
        serializer.save(category=category, group=group)


class ProductVariantGroupViewSet(viewsets.ModelViewSet):
    serializer_class = ProductVariantGroupSerializer
    permission_classes = [IsManagerOrAbove]

    def get_queryset(self):
        return ProductVariantGroup.objects.filter(product_id=self.kwargs['product_pk']).select_related('group')

    def perform_create(self, serializer):
        product = get_object_or_404(Item, pk=self.kwargs['product_pk'])
        group_id = self.request.data.get('group_id')
        group = get_object_or_404(VariantGroup, pk=group_id)
        serializer.save(product=product, group=group)


class DashboardViewSet(viewsets.ViewSet):
    """Dashboard statistics and analytics"""
    permission_classes = [IsManagerOrAbove]

    def list(self, request):
        """Get dashboard data"""
        today = timezone.now().date()
        
        # Today's stats
        today_transactions = PosTransaction.objects.filter(
            created_at__date=today,
            status='completed'
        )
        today_revenue = today_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        today_count = today_transactions.count()
        
        # This week's stats
        week_start = today - timedelta(days=today.weekday())
        week_transactions = PosTransaction.objects.filter(
            created_at__date__gte=week_start,
            status='completed'
        )
        week_revenue = week_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
        # This month's stats
        month_start = today.replace(day=1)
        month_transactions = PosTransaction.objects.filter(
            created_at__date__gte=month_start,
            status='completed'
        )
        month_revenue = month_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        
        # All time stats
        all_transactions = PosTransaction.objects.filter(status='completed')
        total_revenue = all_transactions.aggregate(
            total=Sum('total_amount')
        )['total'] or 0
        total_count = all_transactions.count()
        
        # Last 7 days daily revenue — single query
        seven_days_ago = today - timedelta(days=6)
        daily_totals = dict(
            PosTransaction.objects.filter(
                created_at__date__gte=seven_days_ago,
                status='completed'
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
        
        # Backup health check
        import os, glob
        backup_dir = os.environ.get('BACKUP_PATH', '/tmp/pos_backups')
        backup_warning = False
        last_backup = None
        try:
            backups = sorted(glob.glob(f'{backup_dir}/db_backup_*.sqlite3'))
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
@permission_classes([IsAuthenticated])
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
                    transaction_no=result['reference']
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
@permission_classes([IsAuthenticated])
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
                transaction_no=result['reference'],
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
@permission_classes([IsAuthenticated])
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
                transaction_no=result['reference'],
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
@permission_classes([IsAuthenticated])
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
        
        if gateway not in ['gcash', 'maya']:
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
@permission_classes([IsAuthenticated])
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

    def get_queryset(self):
        qs = Item.objects.all().select_related('category').prefetch_related(
            'variant_group_overrides__group__options',
            'category__variant_groups__group__options',
        )
        sku = self.request.query_params.get('sku')
        search = self.request.query_params.get('search')
        if sku:
            return qs.filter(sku__iexact=sku.strip())
        if search:
            return qs.filter(name__icontains=search.strip())
        return qs

    def get_permissions(self):
        """
        Instantiates and returns the list of permissions that this view requires.
        """
        if self.action in ['create', 'update', 'partial_update', 'destroy']:
            permission_classes = [IsManagerOrAbove]
        else:
            permission_classes = [IsCashierOrAbove]
        return [permission() for permission in permission_classes]

    def get_serializer_class(self):
        if self.action == 'create':
            return ItemCreateSerializer
        if self.action in ('update', 'partial_update'):
            return ItemUpdateSerializer
        return ItemSerializer  # list, retrieve → read serializer with photo fallback
    
    @action(detail=False, methods=['get'])
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
    
    @action(detail=True, methods=['post'])
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
                remarks=f"Changed from {old_stock} to {item.stock}"
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
        """Open a new shift for the current user"""
        # Check if user already has an open shift
        existing_shift = Shift.objects.filter(cashier=request.user, is_open=True).first()
        if existing_shift:
            return Response({
                'error': 'You already have an open shift. Close it before opening a new one.'
            }, status=status.HTTP_400_BAD_REQUEST)

        opening_cash = request.data.get('opening_cash', 0)
        
        shift = Shift.objects.create(
            cashier=request.user,
            opening_cash=opening_cash,
            is_open=True
        )
        
        return Response(ShiftSerializer(shift).data, status=status.HTTP_201_CREATED)

    @action(detail=False, methods=['post'], url_path='close', permission_classes=[IsCashierOrAbove])
    def close_shift(self, request):
        """Close the current open shift for the user"""
        closing_cash = request.data.get('closing_cash')
        if closing_cash is None:
            return Response({'error': 'closing_cash is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            with db_transaction.atomic():
                shift = Shift.objects.select_for_update().get(
                    cashier=request.user,
                    is_open=True
                )

                # Guard against race: another request already closed it
                if shift.closed_at:
                    return Response(
                        {'error': 'Shift is already closed.'},
                        status=status.HTTP_409_CONFLICT
                    )

                # Ownership guard — admins may force-close any shift
                if shift.cashier != request.user and not (
                    request.user.is_superuser or request.user.role == 'admin'
                ):
                    return Response(
                        {'error': 'You can only close your own shift.'},
                        status=status.HTTP_403_FORBIDDEN
                    )

                shift.closing_cash = closing_cash
                shift.closed_at = timezone.now()
                shift.is_open = False
                shift.save()

                return Response(ShiftSerializer(shift).data)
        except Shift.DoesNotExist:
            return Response({'error': 'No open shift found for this user.'}, status=status.HTTP_404_NOT_FOUND)

    @action(detail=False, methods=['get'], url_path='current', permission_classes=[IsCashierOrAbove])
    def current(self, request):
        """Return the currently open shift, or null if none."""
        shift = Shift.objects.filter(cashier=request.user, is_open=True).order_by('-opened_at').first()
        if shift:
            return Response(ShiftSerializer(shift).data)
        return Response(False, status=200)


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
        'printer_enabled': profile.printer_enabled,
        'color_scheme': profile.color_scheme,
        'logo': request.build_absolute_uri(profile.logo.url) if profile.logo else None,
        'vat_enabled': profile.vat_enabled,
        'vat_rate': float(profile.vat_rate),
        'sc_discount_enabled': profile.sc_discount_enabled,
        'sc_discount_rate': float(profile.sc_discount_rate),
        'pwd_discount_enabled': profile.pwd_discount_enabled,
        'pwd_discount_rate': float(profile.pwd_discount_rate),
        'promo_discount_enabled': profile.promo_discount_enabled,
        'track_inventory': profile.track_inventory,
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
    fields = ['business_name', 'tagline', 'contact_number', 'email', 'address', 'tin', 'receipt_header', 'receipt_footer', 'low_stock_threshold', 'printer_ip', 'printer_port', 'printer_enabled', 'color_scheme', 'logo', 'vat_enabled', 'vat_rate', 'sc_discount_enabled', 'sc_discount_rate', 'pwd_discount_enabled', 'pwd_discount_rate', 'promo_discount_enabled', 'track_inventory']
    for field in fields:
        if field in request.data:
            setattr(profile, field, request.data[field])
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


class SupplierViewSet(viewsets.ModelViewSet):
    queryset = Supplier.objects.filter(is_active=True).order_by('name')
    serializer_class = SupplierSerializer
    permission_classes = [IsAuthenticated]


class IngredientViewSet(viewsets.ModelViewSet):
    queryset = Ingredient.objects.filter(is_active=True).select_related('unit','supplier').order_by('name')
    serializer_class = IngredientSerializer
    permission_classes = [IsAuthenticated]

    @action(detail=True, methods=['post'])
    def restock(self, request, pk=None):
        ingredient = self.get_object()
        serializer = IngredientRestockLogSerializer(data=request.data)
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

    @action(detail=False, methods=['get'])
    def low_stock(self, request):
        low = [i for i in self.get_queryset() if i.is_low_stock]
        serializer = self.get_serializer(low, many=True)
        return Response(serializer.data)


class RecipeIngredientViewSet(viewsets.ModelViewSet):
    queryset = RecipeIngredient.objects.select_related('ingredient','item','variant').all()
    serializer_class = RecipeIngredientSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset()
        item_id = self.request.query_params.get('item')
        variant_id = self.request.query_params.get('variant')
        if item_id:
            qs = qs.filter(item_id=item_id)
        if variant_id:
            qs = qs.filter(variant_id=variant_id)
        return qs
