"""Purchase receiving views."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import is_inventory_staff
from inventory.forms import PurchaseReceivingForm
from inventory.models import Inventory
from inventory.services import generate_receiving_token, receive_purchase

from .common import _add_validation_error_to_form, _build_inventory_form_context


def inventory_receive_view(request, inventory_id):
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
        token, _ = generate_receiving_token(inventory_id=inventory.id, actor_id=request.user.id)
        form = PurchaseReceivingForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_receive.html', context)

    elif request.method == 'POST':
        form = PurchaseReceivingForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            token = form.cleaned_data['request_token']
            try:
                tx, is_replay = receive_purchase(
                    inventory_id=inventory.id,
                    quantity=quantity,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu nhập hàng trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Số lượng: {quantity}, Tồn thực tế: {tx.physical_after}, Mã yêu cầu: {tx.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã nhập thành công {quantity} sản phẩm cho biến thể '{inventory.variant.sku}'. "
                        f"Tồn thực tế mới: {tx.physical_after} (Khả dụng: {tx.physical_after - inventory.reserved})."
                    )
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_receive.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])
