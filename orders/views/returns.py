from django.conf import settings
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.views.decorators.http import require_POST
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.utils import timezone
from accounts.permissions import ROLE_ADMIN, ROLE_SALES_STAFF, role_required
from catalog.utils import format_vnd
from orders.forms import ReturnRequestForm, ReturnReviewForm, ReturnRefundConfirmationForm
from orders.models import Order, OrderStatus, ReturnRequest, ReturnRequestStatus
from payments.return_refund_services import create_return_refund_request, confirm_return_refund


def return_request_create_view(request, pk):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")
    order = get_object_or_404(Order.objects.prefetch_related('items__variant__product'), pk=pk, customer=request.user)
    if order.order_status != OrderStatus.DELIVERED:
        messages.error(request, 'Chỉ có thể gửi yêu cầu đổi/trả sau khi đơn hàng đã giao thành công.')
        return redirect('orders:order_detail', pk=order.pk)
    form = ReturnRequestForm(request.POST or None, order=order)
    if request.method == 'POST' and form.is_valid():
        item = form.cleaned_data['order_item']
        request_obj = ReturnRequest(
            order=order,
            order_item=item,
            customer=request.user,
            request_type=form.cleaned_data['request_type'],
            reason_code=form.cleaned_data['reason_code'],
            description=form.cleaned_data['description'].strip(),
            quantity=form.cleaned_data['quantity'],
            replacement_variant=form.cleaned_data.get('replacement_variant'),
        )
        try:
            request_obj.full_clean()
            request_obj.save()
            messages.success(request, f'Đã gửi yêu cầu đổi/trả #{request_obj.pk}. CSKH sẽ phản hồi sớm nhất.')
            return redirect('orders:order_detail', pk=order.pk)
        except ValidationError as error:
            for field, errors in error.message_dict.items():
                for message in errors:
                    form.add_error(field if field in form.fields else None, message)
    return render(request, 'orders/return_request_form.html', {'order': order, 'form': form, 'title': 'Yêu cầu đổi/trả — Quốc Hùng Mobile'})


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def return_request_management_view(request):
    requests = ReturnRequest.objects.select_related(
        'order', 'order__payment', 'order_item', 'customer', 'replacement_variant__product', 'refund_record'
    ).order_by('-updated_at')
    return render(request, 'orders/management/return_request_list.html', {
        'return_requests': requests,
        'active_nav': 'return_requests',
        'title': 'Yêu cầu đổi/trả — Quốc Hùng Mobile',
    })


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
@require_POST
def return_request_review_view(request, pk):
    request_obj = get_object_or_404(ReturnRequest, pk=pk)
    form = ReturnReviewForm(request.POST)
    if form.is_valid() and request_obj.status == ReturnRequestStatus.PENDING:
        request_obj.status = form.cleaned_data['decision']
        request_obj.review_note = form.cleaned_data['review_note'].strip()
        request_obj.reviewed_by = request.user
        request_obj.reviewed_at = timezone.now()
        request_obj.save(update_fields=['status', 'review_note', 'reviewed_by', 'reviewed_at', 'updated_at'])
        messages.success(request, f'Đã cập nhật yêu cầu #{request_obj.pk}.')
    elif request_obj.status != ReturnRequestStatus.PENDING:
        messages.error(request, 'Yêu cầu này đã được xử lý trước đó.')
    else:
        messages.error(request, 'Dữ liệu xử lý chưa hợp lệ.')
    return redirect('orders:return_request_management')


@role_required(ROLE_ADMIN)
@require_POST
def create_return_refund_view(request, pk):
    try:
        refund, created = create_return_refund_request(pk, request.user)
        if created:
            messages.success(request, f'Đã lập hồ sơ hoàn tiền #{refund.pk} ({format_vnd(refund.amount)}), đang chờ xử lý.')
        else:
            messages.info(request, f'Hồ sơ hoàn tiền #{refund.pk} đã tồn tại.')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
    return redirect('orders:return_request_management')


@role_required(ROLE_ADMIN)
@require_POST
def confirm_return_refund_view(request, pk):
    form = ReturnRefundConfirmationForm(request.POST)
    if form.is_valid():
        try:
            refund, confirmed = confirm_return_refund(
                pk,
                request.user,
                form.cleaned_data['bank_reference'],
                form.cleaned_data['completed_at'],
                form.cleaned_data['audit_note'],
            )
            messages.success(request, f'Đã ghi nhận hoàn tiền #{refund.pk} ({format_vnd(refund.amount)}).')
        except ValidationError as error:
            messages.error(request, '; '.join(error.messages))
    else:
        messages.error(request, 'Dữ liệu xác nhận hoàn tiền chưa hợp lệ.')
    return redirect('orders:return_request_management')
