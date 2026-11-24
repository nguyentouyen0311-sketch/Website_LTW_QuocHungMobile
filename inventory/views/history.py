"""Inventory transaction history views."""

import uuid

from django.core.paginator import Paginator
from django.db.models import Count, Q
from django.http import HttpResponseForbidden
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.permissions import is_admin, is_inventory_staff, is_sales_staff
from inventory.models import InventoryTransaction

from .common import parse_positive_id


def inventory_transaction_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    tx_type = request.GET.get('type', '').strip().upper()

    base_qs = (
        InventoryTransaction.objects
        .select_related(
            'inventory',
            'inventory__variant',
            'inventory__variant__product',
            'performed_by',
            'adjustment',
            'order',
            'return_request',
        )
        .prefetch_related('inventory__variant__attribute_values__attribute_value')
        .order_by('-created_at', '-id')
    )

    total_tx_count = base_qs.count()
    type_counts = dict(base_qs.order_by().values_list('transaction_type').annotate(count=Count('id')))
    queryset = base_qs

    if q:
        search_filter = (
            Q(inventory__variant__sku__icontains=q)
            | Q(inventory__variant__product__name__icontains=q)
            | Q(performed_by__username__icontains=q)
            | Q(adjustment__reason__icontains=q)
        )
        clean_code = q.upper()
        if clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(inventory__variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(inventory__variant__product__id=pid)

        try:
            val_uuid = uuid.UUID(q)
            search_filter |= Q(request_key=val_uuid)
        except (ValueError, TypeError):
            pass

        queryset = queryset.filter(search_filter)

    if tx_type in dict(InventoryTransaction.TRANSACTION_TYPE_CHOICES):
        queryset = queryset.filter(transaction_type=tx_type)
    else:
        tx_type = 'ALL'

    paginator = Paginator(queryset, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'transaction_list': page_obj.object_list,
        'q': q,
        'tx_type': tx_type,
        'total_tx_count': total_tx_count,
        'count_purchase': type_counts.get(InventoryTransaction.TYPE_PURCHASE_IN, 0),
        'count_ship': type_counts.get(InventoryTransaction.TYPE_SHIP, 0),
        'count_adjust': type_counts.get(InventoryTransaction.TYPE_ADJUSTMENT, 0),
        'count_return': type_counts.get(InventoryTransaction.TYPE_RETURN_RESTOCK, 0),
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_history',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_history.html', context)
