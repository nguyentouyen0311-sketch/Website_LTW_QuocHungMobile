"""Stocktake and inventory-adjustment views."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import is_inventory_staff
from inventory.forms import StocktakeAdjustmentForm
from inventory.models import Inventory
from inventory.services import adjust_inventory, generate_adjustment_token

from .common import _add_validation_error_to_form, _build_inventory_form_context


def inventory_adjust_view(request, inventory_id):
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

    if request.method == 'GET':
        token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
        form = StocktakeAdjustmentForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_adjust.html', context)

    elif request.method == 'POST':
        form = StocktakeAdjustmentForm(request.POST)
        if form.is_valid():
            counted_quantity = form.cleaned_data['counted_quantity']
            reason = form.cleaned_data['reason']
            token = form.cleaned_data['request_token']
            try:
                adj, is_replay = adjust_inventory(
                    inventory_id=inventory.id,
                    counted_quantity=counted_quantity,
                    reason=reason,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu điều chỉnh kiểm kê trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Tồn mới: {adj.new_quantity}, Tồn cũ: {adj.old_quantity}, Chênh lệch: {adj.quantity_delta:+d}, Mã giao dịch: {adj.transaction.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã điều chỉnh tồn kho cho biến thể '{inventory.variant.sku}': từ {adj.old_quantity} thành {adj.new_quantity} "
                        f"(Chênh lệch: {adj.quantity_delta:+d}, Khả dụng mới: {adj.new_quantity - inventory.reserved}). Lý do: {adj.reason}."
                    )
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                exc_code = getattr(exc, 'code', None)
                if exc_code == 'stale_snapshot':
                    inventory.refresh_from_db()
                    fresh_token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
                    form.data = form.data.copy()
                    form.data['request_token'] = fresh_token

                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        inventory.refresh_from_db()
        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_adjust.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])
