from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render
from accounts.permissions import (
    ROLE_ADMIN,
    ROLE_SALES_STAFF,
    is_admin,
    is_sales_staff,
    role_required,
)
from catalog.utils import format_vnd
from inventory.models import InventoryReservationStatus
from orders.forms import OrderConfirmationForm, OrderDeliverForm, OrderShipForm
from orders.models import Order, OrderStatus
from orders.services.confirmation import confirm_order
from orders.services.shipping import ship_order
from orders.services.delivery import deliver_order
from payments.models import PaymentMethod, PaymentStatus
from payments.cod_forms import CODCollectionForm
from payments.cod_services import record_cod_collection


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def pending_orders_list_view(request):
    orders_qs = Order.objects.filter(
        order_status=OrderStatus.PENDING_CONFIRMATION
    ).select_related('customer', 'payment').prefetch_related(
        'items', 'items__variant'
    ).order_by('-created_at', '-id')

    search_query = request.GET.get('q', '').strip()
    if search_query:
        orders_qs = orders_qs.filter(
            Q(order_code__icontains=search_query) |
            Q(recipient_phone__icontains=search_query) |
            Q(recipient_name__icontains=search_query)
        )

    method_filter = request.GET.get('method', '').strip().upper()
    if method_filter == PaymentMethod.COD:
        orders_qs = orders_qs.filter(payment__method=PaymentMethod.COD)

    total_count = orders_qs.count()

    paginator = Paginator(orders_qs, 20)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    for o in page_obj:
        o.grand_total_formatted = format_vnd(o.grand_total)
        p = getattr(o, 'payment', None)
        o.can_confirm = False
        o.eligibility_reason = "Thiếu bản ghi thanh toán"
        if p:
            if p.expected_amount != o.grand_total:
                o.can_confirm = False
                o.eligibility_reason = "Lệch số tiền thanh toán và đơn hàng"
            elif p.method == PaymentMethod.COD:
                if p.status == PaymentStatus.UNPAID:
                    o.can_confirm = True
                    o.eligibility_reason = "Đủ điều kiện (COD Chưa thanh toán)"
                else:
                    o.eligibility_reason = f"COD không hợp lệ ({p.get_status_display()})"
            else:
                o.eligibility_reason = "Phương thức thanh toán cũ không còn được hỗ trợ xử lý."

    context = {
        'orders': page_obj,
        'page_obj': page_obj,
        'search_query': search_query,
        'method_filter': method_filter,
        'total_count': total_count,
        'active_nav': 'order_pending_confirmation',
        'is_sales_staff': is_sales_staff(request.user),
        'is_admin': is_admin(request.user),
        'title': 'Đơn hàng chờ xác nhận — Quốc Hùng Mobile',
    }
    return render(request, 'orders/management/pending_orders_list.html', context)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def order_management_detail_view(request, pk, confirmation_form=None, ship_form=None, deliver_form=None, cod_collection_form=None):
    order = get_object_or_404(
        Order.objects.select_related('customer', 'payment').prefetch_related(
            'items', 'items__variant', 'status_history', 'items__reservations', 'inventory_transactions'
        ),
        pk=pk
    )

    order.subtotal_formatted = format_vnd(order.subtotal)
    order.discount_total_formatted = format_vnd(order.discount_total)
    order.grand_total_formatted = format_vnd(order.grand_total)

    order_items = []
    has_reservation_error = False
    for item in order.items.all().order_by('id'):
        item.unit_price_formatted = format_vnd(item.unit_price)
        item.discount_amount_formatted = format_vnd(item.discount_amount)
        item.line_total_formatted = format_vnd(item.line_total)

        res_list = list(item.reservations.all())
        active_res = [r for r in res_list if r.status == InventoryReservationStatus.ACTIVE]
        shipped_res = [r for r in res_list if r.status == InventoryReservationStatus.SHIPPED]
        item.active_res = active_res
        item.shipped_res = shipped_res

        is_valid_active = (
            len(active_res) == 1 and
            active_res[0].quantity == item.quantity and
            active_res[0].variant_id == item.variant_id and
            active_res[0].finished_at is None
        )

        is_valid_shipped = (
            len(shipped_res) == 1 and
            shipped_res[0].quantity == item.quantity and
            shipped_res[0].variant_id == item.variant_id and
            shipped_res[0].finished_at is not None and
            len(active_res) == 0
        )

        item.is_valid_active_reservation = is_valid_active
        item.is_valid_shipped_reservation = is_valid_shipped

        if order.order_status in (OrderStatus.SHIPPING, OrderStatus.DELIVERED):
            if is_valid_shipped:
                item.reservation_display_state = 'shipped'
                item.has_valid_reservation = True
            else:
                item.reservation_display_state = 'invalid'
                item.has_valid_reservation = False
                has_reservation_error = True
        elif order.order_status in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
            if is_valid_active:
                item.reservation_display_state = 'active'
                item.has_valid_reservation = True
            else:
                item.reservation_display_state = 'invalid'
                item.has_valid_reservation = False
                has_reservation_error = True
        else:
            if is_valid_shipped:
                item.reservation_display_state = 'shipped'
                item.has_valid_reservation = True
            elif is_valid_active:
                item.reservation_display_state = 'active'
                item.has_valid_reservation = True
            else:
                item.reservation_display_state = 'invalid'
                item.has_valid_reservation = False
                has_reservation_error = True

        order_items.append(item)

    payment = getattr(order, 'payment', None)
    if payment:
        payment.expected_amount_formatted = format_vnd(payment.expected_amount)

    can_confirm = False
    ineligible_reason = ""

    if order.order_status == OrderStatus.PENDING_CONFIRMATION:
        if not payment:
            ineligible_reason = "Đơn hàng không có bản ghi thanh toán liên kết."
        elif payment.expected_amount != order.grand_total:
            ineligible_reason = f"Số tiền thanh toán dự kiến ({format_vnd(payment.expected_amount)}) không khớp với tổng tiền đơn hàng ({format_vnd(order.grand_total)}). Yêu cầu rà soát đối soát vận hành."
        elif has_reservation_error:
            ineligible_reason = "Trạng thái giữ kho của sản phẩm không hợp lệ hoặc thiếu bản ghi ACTIVE. Yêu cầu rà soát đối soát vận hành."
        elif payment.method == PaymentMethod.COD:
            if payment.status == PaymentStatus.UNPAID:
                can_confirm = True
            else:
                ineligible_reason = f"Đơn hàng COD chờ xác nhận phải ở trạng thái Chưa thanh toán (UNPAID). Hiện tại là {payment.get_status_display()}."
        else:
            ineligible_reason = f"Phương thức thanh toán {payment.method} không được hỗ trợ xác nhận."
    elif order.order_status == OrderStatus.CONFIRMED:
        ineligible_reason = "Đơn hàng đã ở trạng thái ĐÃ XÁC NHẬN (CONFIRMED)."
    else:
        ineligible_reason = f"Đơn hàng đang ở trạng thái {order.get_order_status_display()}, không thuộc diện chờ xác nhận."

    can_ship = False
    ineligible_ship_reason = ""

    if order.order_status == OrderStatus.CONFIRMED:
        if not payment:
            ineligible_ship_reason = "Đơn hàng không có bản ghi thanh toán liên kết."
        elif payment.expected_amount != order.grand_total:
            ineligible_ship_reason = f"Số tiền thanh toán dự kiến ({format_vnd(payment.expected_amount)}) không khớp với tổng tiền đơn hàng ({format_vnd(order.grand_total)}). Yêu cầu rà soát đối soát vận hành."
        elif has_reservation_error:
            ineligible_ship_reason = "Trạng thái giữ kho của sản phẩm không hợp lệ hoặc thiếu bản ghi ACTIVE. Yêu cầu rà soát đối soát vận hành."
        elif payment.method == PaymentMethod.COD:
            if payment.status == PaymentStatus.UNPAID:
                can_ship = True
            else:
                ineligible_ship_reason = f"Đơn hàng COD xuất kho phải ở trạng thái Chưa thanh toán (UNPAID). Hiện tại là {payment.get_status_display()}."
        else:
            ineligible_ship_reason = f"Phương thức thanh toán {payment.method} không được hỗ trợ xuất kho."
    elif order.order_status == OrderStatus.SHIPPING:
        ineligible_ship_reason = "Đơn hàng đã ở trạng thái ĐANG GIAO HÀNG (SHIPPING)."
    else:
        ineligible_ship_reason = f"Đơn hàng đang ở trạng thái {order.get_order_status_display()}, không thuộc diện xuất kho giao hàng."

    can_deliver = False
    ineligible_deliver_reason = ""

    if order.order_status == OrderStatus.SHIPPING:
        if not payment:
            ineligible_deliver_reason = "Đơn hàng không có bản ghi thanh toán liên kết."
        elif payment.expected_amount != order.grand_total:
            ineligible_deliver_reason = f"Số tiền thanh toán dự kiến ({format_vnd(payment.expected_amount)}) không khớp với tổng tiền đơn hàng ({format_vnd(order.grand_total)}). Yêu cầu rà soát đối soát vận hành."
        elif has_reservation_error:
            ineligible_deliver_reason = "Trạng thái giữ kho của sản phẩm không hợp lệ hoặc chưa hoàn tất SHIPPED. Yêu cầu rà soát đối soát vận hành."
        elif payment.method == PaymentMethod.COD:
            if payment.status in (PaymentStatus.UNPAID, PaymentStatus.PAID):
                can_deliver = True
            else:
                ineligible_deliver_reason = f"Đơn hàng COD giao hàng phải ở trạng thái Chưa thanh toán (UNPAID) hoặc Đã thanh toán (PAID). Hiện tại là {payment.get_status_display()}."
        else:
            ineligible_deliver_reason = f"Phương thức thanh toán {payment.method} không được hỗ trợ giao hàng."
    elif order.order_status == OrderStatus.DELIVERED:
        ineligible_deliver_reason = "Đơn hàng đã ở trạng thái ĐÃ GIAO HÀNG (DELIVERED)."
    else:
        ineligible_deliver_reason = f"Đơn hàng đang ở trạng thái {order.get_order_status_display()}, không thuộc diện hoàn tất giao hàng."

    if confirmation_form is None:
        confirmation_form = OrderConfirmationForm()

    if ship_form is None:
        ship_form = OrderShipForm()

    if deliver_form is None:
        deliver_form = OrderDeliverForm()

    cod_collection = getattr(payment, 'cod_collection', None) if payment else None
    can_collect_cod = bool(
        payment
        and payment.method == PaymentMethod.COD
        and payment.status == PaymentStatus.UNPAID
        and order.order_status in (OrderStatus.SHIPPING, OrderStatus.DELIVERED)
        and cod_collection is None
    )
    if cod_collection_form is None:
        cod_collection_form = CODCollectionForm(initial={'amount': payment.expected_amount} if can_collect_cod else None)

    context = {
        'order': order,
        'payment': payment,
        'order_items': order_items,
        'has_reservation_error': has_reservation_error,
        'status_history': order.status_history.all().order_by('changed_at', 'id'),
        'can_confirm': can_confirm,
        'ineligible_reason': ineligible_reason,
        'confirmation_form': confirmation_form,
        'can_ship': can_ship,
        'ineligible_ship_reason': ineligible_ship_reason,
        'ship_form': ship_form,
        'can_deliver': can_deliver,
        'ineligible_deliver_reason': ineligible_deliver_reason,
        'deliver_form': deliver_form,
        'cod_collection': cod_collection,
        'can_collect_cod': can_collect_cod,
        'cod_collection_form': cod_collection_form,
        'inventory_transactions': order.inventory_transactions.all().order_by('created_at', 'id'),
        'active_nav': 'order_pending_confirmation',
        'is_sales_staff': is_sales_staff(request.user),
        'is_admin': is_admin(request.user),
        'title': f"Quản lý Đơn hàng #{order.order_code} — Quốc Hùng Mobile",
    }
    return render(request, 'orders/management/order_management_detail.html', context)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
@require_POST
def collect_cod_action_view(request, pk):
    order = get_object_or_404(Order.objects.select_related('payment'), pk=pk)
    form = CODCollectionForm(request.POST)
    if not form.is_valid():
        return order_management_detail_view(request, pk, cod_collection_form=form)
    try:
        receipt = record_cod_collection(
            order.payment.pk,
            request.user,
            form.cleaned_data['amount'],
            form.cleaned_data['reference'],
            form.cleaned_data.get('note', ''),
        )
        messages.success(request, f'Đã ghi nhận thu COD với chứng từ {receipt.reference}.')
        return redirect('orders:management_detail', pk=pk)
    except ValidationError as error:
        error_items = error.message_dict.items() if hasattr(error, 'message_dict') else [(None, error.messages)]
        for field, errors in error_items:
            for message in errors:
                form.add_error(field, message)
        return order_management_detail_view(request, pk, cod_collection_form=form)


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def confirm_order_action_view(request, pk):
    if request.method != 'POST':
        messages.error(request, "Thao tác xác nhận đơn hàng chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('orders:management_detail', pk=pk)

    form = OrderConfirmationForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
        return order_management_detail_view(request, pk, confirmation_form=form)

    note = form.cleaned_data.get('confirmation_note', '')

    try:
        result = confirm_order(order_id=pk, user=request.user, note=note)
        if result.already_confirmed:
            messages.info(
                request,
                f"Đơn hàng #{result.order.order_code} đã ở trạng thái Đã xác nhận (CONFIRMED) trước đó (kết quả bảo toàn)."
            )
        else:
            messages.success(
                request,
                f"Đã xác nhận đơn hàng #{result.order.order_code} thành công! Trạng thái chuyển sang Đã xác nhận (CONFIRMED)."
            )
        return redirect('orders:management_detail', pk=pk)

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
        return redirect('orders:management_detail', pk=pk)

    except PermissionDenied as pd:
        raise pd


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def ship_order_action_view(request, pk):
    if request.method != 'POST':
        messages.error(request, "Thao tác xuất kho và giao hàng chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('orders:management_detail', pk=pk)

    form = OrderShipForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
        return order_management_detail_view(request, pk, ship_form=form)

    note = form.cleaned_data.get('shipping_note', '')

    try:
        result = ship_order(order_id=pk, user=request.user, shipping_note=note)
        if result.already_shipped:
            messages.info(
                request,
                f"Đơn hàng #{result.order.order_code} đã ở trạng thái Đang giao hàng (SHIPPING) trước đó (kết quả bảo toàn)."
            )
        else:
            messages.success(
                request,
                f"Đã xuất kho và bắt đầu giao hàng cho đơn #{result.order.order_code} thành công! Trạng thái chuyển sang Đang giao hàng (SHIPPING)."
            )
        return redirect('orders:management_detail', pk=pk)

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
        return redirect('orders:management_detail', pk=pk)

    except PermissionDenied as pd:
        raise pd


@role_required(ROLE_SALES_STAFF, ROLE_ADMIN)
def deliver_order_action_view(request, pk):
    if request.method != 'POST':
        messages.error(request, "Thao tác hoàn tất giao hàng chỉ hỗ trợ phương thức POST an toàn.")
        return redirect('orders:management_detail', pk=pk)

    form = OrderDeliverForm(request.POST)
    if not form.is_valid():
        for field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
        return order_management_detail_view(request, pk, deliver_form=form)

    note = form.cleaned_data.get('delivery_note', '')

    try:
        result = deliver_order(order_id=pk, user=request.user, delivery_note=note)
        if result.already_delivered:
            messages.info(
                request,
                f"Đơn hàng #{result.order.order_code} đã ở trạng thái Đã giao hàng (DELIVERED) trước đó (kết quả bảo toàn)."
            )
        else:
            messages.success(
                request,
                f"Đã hoàn tất giao hàng cho đơn #{result.order.order_code} thành công! Trạng thái chuyển sang Đã giao hàng (DELIVERED)."
            )
        return redirect('orders:management_detail', pk=pk)

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
        return redirect('orders:management_detail', pk=pk)

    except PermissionDenied as pd:
        raise pd
