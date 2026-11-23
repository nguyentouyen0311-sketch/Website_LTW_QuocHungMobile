"""Inventory browsing, detail, and threshold-management views."""

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import F, Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import is_admin, is_inventory_staff, is_sales_staff
from inventory.forms import InventoryThresholdForm
from inventory.models import Inventory

from .common import parse_positive_id


def inventory_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    alert_filter = request.GET.get('alert', '').strip().lower()

    base_qs = (
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        )
        .prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        )
        .annotate(available_count=F('physical_sellable') - F('reserved'))
    )

    total_variants_count = base_qs.count()
    total_low_stock_count = base_qs.filter(available_count__lte=F('min_threshold')).count()
    total_out_of_stock_count = base_qs.filter(available_count__lte=0).count()

    queryset = base_qs

    if q:
        search_filter = (
            Q(variant__sku__icontains=q)
            | Q(variant__product__name__icontains=q)
        )
        clean_code = q.upper()
        if clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(variant__product__id=pid)

        queryset = queryset.filter(search_filter)

    if alert_filter == 'low_stock':
        queryset = queryset.filter(available_count__lte=F('min_threshold'))
    elif alert_filter == 'out_of_stock':
        queryset = queryset.filter(available_count__lte=0)
    elif alert_filter == 'in_stock':
        queryset = queryset.filter(available_count__gt=F('min_threshold'))
    else:
        alert_filter = 'all'

    queryset = queryset.order_by('variant__product__name', 'variant__id')

    paginator = Paginator(queryset, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'inventory_list': page_obj.object_list,
        'q': q,
        'alert_filter': alert_filter,
        'total_variants_count': total_variants_count,
        'total_low_stock_count': total_low_stock_count,
        'total_out_of_stock_count': total_out_of_stock_count,
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_list.html', context)


def _build_inventory_detail_context(request, inventory, form=None):
    if form is None:
        form = InventoryThresholdForm(instance=inventory)

    transactions_qs = (
        inventory.transactions
        .select_related('performed_by', 'adjustment')
        .order_by('-created_at', '-id')
    )
    total_tx_count = transactions_qs.count()
    tx_paginator = Paginator(transactions_qs, 10)
    tx_page_number = request.GET.get('tx_page') or request.POST.get('tx_page', 1)
    tx_page_obj = tx_paginator.get_page(tx_page_number)

    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'tx_page_obj': tx_page_obj,
        'total_tx_count': total_tx_count,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }


def inventory_detail_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    context = _build_inventory_detail_context(request, inventory)
    return render(request, 'inventory/inventory_detail.html', context)


def inventory_update_threshold_view(request, inventory_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    old_threshold = inventory.min_threshold
    form = InventoryThresholdForm(request.POST, instance=inventory)
    if form.is_valid():
        updated_inv = form.save()
        messages.success(
            request,
            f"Đã cập nhật ngưỡng cảnh báo cho SKU '{inventory.variant.sku}' "
            f"từ {old_threshold} thành {updated_inv.min_threshold} thành công.",
        )
        return redirect('inventory:inventory_detail', inventory_id=inventory.id)

    context = _build_inventory_detail_context(request, inventory, form=form)
    return render(request, 'inventory/inventory_detail.html', context, status=200)
