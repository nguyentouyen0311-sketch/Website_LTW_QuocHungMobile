from django.conf import settings
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.http import HttpResponseNotAllowed
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from catalog.models import Category, ProductReview
from catalog.utils import format_vnd
from orders.forms import OrderCancelForm
from orders.models import Order, OrderCancellation, OrderStatus
from orders.services.cancellation import cancel_customer_cod_order
from payments.models import Payment, PaymentStatus, RefundStatus


def customer_orders_list_view(request):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    orders = (
        Order.objects.filter(customer=request.user)
        .select_related('payment')
        .prefetch_related('items')
        .order_by('-created_at', '-id')
    )
    status = request.GET.get('status', '').strip()
    if status in OrderStatus.values:
        orders = orders.filter(order_status=status)
    else:
        status = ''

    paginator = Paginator(orders, 10)
    page_obj = paginator.get_page(request.GET.get('page'))
    for order in page_obj:
        order.grand_total_formatted = format_vnd(order.grand_total)
        order.item_count = sum(item.quantity for item in order.items.all())

    return render(request, 'orders/my_orders.html', {
        'page_obj': page_obj,
        'status': status,
        'status_choices': OrderStatus.choices,
        'active_nav': 'orders',
        'title': 'Đơn hàng của tôi — Quốc Hùng Mobile',
    })


def order_detail_view(request, pk, cancel_form=None):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    if request.method != 'GET' and cancel_form is None:
        return HttpResponseNotAllowed(['GET'])

    order = get_object_or_404(
        Order.objects.prefetch_related('items', 'status_history').select_related('customer'),
        pk=pk,
        customer=request.user,
    )

    payment = Payment.objects.filter(order=order).first()

    order.subtotal_formatted = format_vnd(order.subtotal)
    order.discount_total_formatted = format_vnd(order.discount_total)
    order.grand_total_formatted = format_vnd(order.grand_total)

    order_items = []
    for item in order.items.all().order_by('id'):
        item.unit_price_formatted = format_vnd(item.unit_price)
        item.discount_amount_formatted = format_vnd(item.discount_amount)
        item.line_total_formatted = format_vnd(item.line_total)
        item.review = ProductReview.objects.filter(order_item=item).first()
        order_items.append(item)

    if payment:
        payment.expected_amount_formatted = format_vnd(payment.expected_amount)


    refund = None
    if payment and payment.status == PaymentStatus.REFUNDED:
        refund = order.refunds.filter(status=RefundStatus.COMPLETED).order_by('-completed_at', '-id').first()
        if refund:
            refund.amount_formatted = format_vnd(refund.amount)

    can_cancel = order.is_eligible_for_customer_cancellation
    if cancel_form is None:
        cancel_form = OrderCancelForm()

    cancellation = getattr(order, 'cancellation', None)
    if not cancellation:
        cancellation = OrderCancellation.objects.filter(order=order).first()

    status_history = order.status_history.all().order_by('changed_at', 'id')
    categories = Category.objects.filter(is_visible=True).order_by('name')

    context = {
        'order': order,
        'order_items': order_items,
        'payment': payment,
        'refund': refund,
        'can_cancel': can_cancel,
        'cancel_form': cancel_form,
        'cancellation': cancellation,
        'status_history': status_history,
        'categories': categories,
        'active_nav': 'cart',
        'title': f"Đơn hàng #{order.order_code} — Quốc Hùng Mobile",
    }
    return render(request, 'orders/order_detail.html', context)


def customer_order_cancel_view(request, pk):
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])

    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    order = get_object_or_404(Order, pk=pk, customer=request.user)

    if request.method == 'GET':
        if not order.is_eligible_for_customer_cancellation:
            messages.error(request, f"Đơn hàng #{order.order_code} hiện không đủ điều kiện để hủy trước xuất kho.")
            return redirect('orders:order_detail', pk=pk)

        cancel_form = OrderCancelForm()
        payment = Payment.objects.filter(order=order).first()
        categories = Category.objects.filter(is_visible=True).order_by('name')

        context = {
            'order': order,
            'payment': payment,
            'cancel_form': cancel_form,
            'categories': categories,
            'active_nav': 'cart',
            'title': f"Xác nhận Hủy Đơn Hàng #{order.order_code} — Quốc Hùng Mobile",
        }
        return render(request, 'orders/order_cancel_confirm.html', context)

    form = OrderCancelForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
        return order_detail_view(request, pk, cancel_form=form)

    reason_code = form.cleaned_data['reason_code']
    description = form.cleaned_data.get('description', '')

    try:
        result = cancel_customer_cod_order(
            order_id=order.pk,
            customer=request.user,
            reason_code=reason_code,
            description=description,
        )
        if result.is_replay:
            messages.info(
                request,
                f"Đơn hàng #{order.order_code} đã được ghi nhận hủy trước đó (kết quả bảo toàn)."
            )
        else:
            messages.success(
                request,
                f"Đã hủy đơn hàng #{order.order_code} thành công. Giữ chỗ tồn kho đã được giải phóng."
            )
        return redirect('orders:order_detail', pk=pk)

    except ValidationError as ve:
        if hasattr(ve, 'message_dict'):
            for field, errs in ve.message_dict.items():
                for err in errs:
                    messages.error(request, err)
        elif hasattr(ve, 'messages'):
            for msg in ve.messages:
                messages.error(request, msg)
        elif hasattr(ve, 'message'):
            messages.error(request, ve.message)
        else:
            messages.error(request, str(ve))
        return redirect('orders:order_detail', pk=pk)

    except PermissionDenied as pd:
        raise pd
