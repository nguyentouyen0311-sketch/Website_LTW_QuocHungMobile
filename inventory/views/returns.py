"""Views for completing approved return and exchange requests."""

from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import redirect, render
from django.urls import reverse

from accounts.permissions import is_inventory_staff
from inventory.services import confirm_return_receipt


def returned_stock_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này. Chỉ Nhân viên kho hoặc Quản trị viên mới được phép quản lý hàng đổi trả.")

    from orders.models import ReturnRequest, ReturnRequestStatus

    pending_receipt_requests = (
        ReturnRequest.objects.filter(status=ReturnRequestStatus.APPROVED)
        .select_related('order', 'order_item', 'customer', 'replacement_variant')
        .order_by('-updated_at')
    )
    completed_requests = (
        ReturnRequest.objects.filter(status=ReturnRequestStatus.COMPLETED)
        .select_related('order', 'order_item', 'customer', 'replacement_variant')
        .order_by('-updated_at')[:20]
    )
    return render(request, 'inventory/returned_stock_list.html', {
        'pending_receipt_requests': pending_receipt_requests,
        'completed_requests': completed_requests,
        'active_nav': 'returned_stock',
        'is_inventory_staff': True,
        'title': 'Hoàn tất đổi/trả — Quốc Hùng Mobile',
    })


def confirm_receipt_action_view(request, pk):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if not is_inventory_staff(request.user):
        return HttpResponseForbidden('Bạn không có quyền tiếp nhận hàng đổi trả.')
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        confirm_return_receipt(return_request_id=pk, actor=request.user)
        messages.success(request, f'Đã hoàn tất yêu cầu đổi/trả #{pk}. Sản phẩm nhận về không được nhập lại vào kho bán.')
    except (ValidationError, PermissionDenied) as exc:
        messages.error(request, str(getattr(exc, 'message', exc)))
    return redirect('inventory:returned_stock_list')
