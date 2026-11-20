from datetime import datetime
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlencode

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import Q
from django.http import (
    FileResponse,
    Http404,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseNotAllowed,
)
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.views.decorators.http import require_POST

from accounts.permissions import is_admin, is_customer, is_sales_staff
from cart.models import Cart
from cart.services import add_to_cart
from orders.checkout_helpers import (
    calculate_server_quote,
    clear_pending_order_code,
    get_or_create_pending_order_code,
    make_checkout_token,
    map_validation_error_to_form,
    verify_checkout_token,
    verify_quote_token,
)
from orders.forms import (
    CheckoutForm,
    OrderCancelForm,
    OrderConfirmationForm,
    OrderDeliverForm,
    OrderShipForm,
    ReturnRequestForm,
    ReturnReviewForm,
)
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderStatus,
    ReturnAttachment,
    ReturnRequest,
    ReturnStatus,
)
from orders.presentation import STATUS_FILTERS, decorate_order, status_tabs, workspace_orders
from orders.services import cancel_customer_cod_order, confirm_order, deliver_order, ship_order
from orders.workspace_services import (
    place_cod_order,
    return_eligibility,
    review_return_request,
    submit_return_request,
)
from payments.forms import CODCollectionForm
from payments.models import PaymentMethod, PaymentStatus
from payments.services import record_cod_collection


def require_customer(user):
    if not is_customer(user):
        raise PermissionDenied('Chức năng dành cho tài khoản khách hàng.')


def require_staff(user):
    if not is_sales_staff(user):
        raise PermissionDenied('Chức năng dành cho nhân viên bán hàng và quản trị viên.')


def customer_order(user, pk):
    require_customer(user)
    return get_object_or_404(workspace_orders(), pk=pk, customer=user)


def management_url(order_id=None):
    url = reverse('orders:management_orders_list')
    return url + '?' + urlencode({'selected': order_id}) if order_id else url


@login_required
def checkout_view(request):
    require_customer(request.user)
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    cart = Cart.objects.filter(user=request.user).first()
    raw_coupon = (
        request.POST.get('coupon_code', '')
        if request.method == 'POST'
        else request.GET.get('coupon', '')
    )
    action = request.POST.get('action', 'place')
    if action == 'remove_coupon':
        raw_coupon = ''
    quote = calculate_server_quote(cart, raw_coupon)
    profile = getattr(request.user, 'customer_profile', None)
    initial = {
        'recipient_name': request.user.get_full_name() or request.user.username,
        'recipient_phone': profile.phone if profile else '',
        'shipping_address': profile.default_address if profile else '',
        'shipping_method': 'DELIVERY',
        'payment_method': PaymentMethod.COD,
        'coupon_code': raw_coupon,
        'quote_token': quote.quote_token,
        'checkout_token': (
            make_checkout_token(
                request.user.pk, cart.pk, get_or_create_pending_order_code(request, cart)
            )
            if cart
            else ''
        ),
    }
    form = CheckoutForm(initial=initial)
    if request.method == 'POST':
        if action not in ('place', 'apply_coupon', 'remove_coupon'):
            return HttpResponseBadRequest('Thao tác không hợp lệ.')
        data = request.POST.copy()
        if action in ('apply_coupon', 'remove_coupon'):
            data['coupon_code'] = raw_coupon
            data['quote_token'] = quote.quote_token
            form = CheckoutForm(initial=data)
        else:
            form = CheckoutForm(data)
            if form.is_valid():
                order_code = verify_checkout_token(
                    form.cleaned_data['checkout_token'], request.user.pk, cart.pk if cart else -1
                )
                if not order_code:
                    form.add_error(None, 'Phiên đặt hàng không hợp lệ. Vui lòng tải lại trang.')
                else:
                    previous = Order.objects.filter(
                        order_code=order_code,
                        customer=request.user,
                        payment__method=PaymentMethod.COD,
                    ).first()
                    if previous:
                        return redirect('orders:order_detail', pk=previous.pk)
                    if not quote.is_valid:
                        form.add_error(
                            None, quote.error_message or 'Giỏ hàng chưa đủ điều kiện đặt hàng.'
                        )
                    elif quote.coupon_error:
                        form.add_error('coupon_code', quote.coupon_error)
                    elif verify_quote_token(form.cleaned_data['quote_token']) != quote.fingerprint:
                        form.add_error(
                            None,
                            'Giá, tồn kho hoặc ưu đãi vừa thay đổi. Vui lòng kiểm tra tổng tiền và xác nhận lại.',
                        )
                    else:
                        try:
                            with transaction.atomic():
                                fresh_quote = calculate_server_quote(
                                    cart, form.cleaned_data.get('coupon_code')
                                )
                                if fresh_quote.fingerprint != quote.fingerprint:
                                    raise ValidationError(
                                        'Giỏ hàng vừa thay đổi. Vui lòng kiểm tra và thử lại.'
                                    )
                                result = place_cod_order(
                                    request.user, cart, form.cleaned_data, order_code
                                )
                            clear_pending_order_code(request, cart)
                            messages.success(
                                request,
                                'Đặt hàng COD thành công. Cửa hàng sẽ xác nhận đơn của bạn.',
                            )
                            return redirect('orders:order_detail', pk=result.order.pk)
                        except ValidationError as error:
                            map_validation_error_to_form(form, error)
                        except (OperationalError, IntegrityError):
                            form.add_error(
                                None,
                                'Có giao dịch đồng thời. Dữ liệu chưa được ghi trùng; vui lòng tải lại và thử lại.',
                            )
            form.data = form.data.copy()
            form.data['quote_token'] = quote.quote_token
    return render(
        request,
        'orders/checkout.html',
        {
            'form': form,
            'cart': cart,
            'quote': quote,
            'title': 'Thanh toán đơn hàng',
            'default_recipient': initial,
            'province_suggestions': ['Đà Nẵng', 'Hà Nội', 'Thành phố Hồ Chí Minh'],
        },
    )


@login_required
def my_orders_view(request):
    require_customer(request.user)
    base = workspace_orders().filter(customer=request.user)
    status = request.GET.get('status', 'all')
    if status not in ('all', *STATUS_FILTERS.keys()):
        status = 'all'
    query = request.GET.get('q', '').strip()[:150]
    orders = base.filter(STATUS_FILTERS[status]) if status != 'all' else base
    if query:
        orders = orders.filter(
            Q(order_code__icontains=query) | Q(items__product_name_snapshot__icontains=query)
        )
    page = Paginator(orders.distinct().order_by('-created_at', '-id'), 3).get_page(
        request.GET.get('page')
    )
    for order in page:
        decorate_order(order)
    return render(
        request,
        'orders/my_orders.html',
        {
            'page_obj': page,
            'tabs': status_tabs(base, status, customer=True),
            'status': status,
            'search_q': query,
            'account_nav': 'orders',
            'title': 'Đơn hàng của tôi',
            'query_string': urlencode({'status': status, 'q': query}),
        },
    )


@login_required
def order_detail_view(request, pk, cancel_form=None):
    order = decorate_order(customer_order(request.user, pk))
    return render(
        request,
        'orders/order_detail.html',
        {
            'order': order,
            'payment': order.payment,
            'cancel_form': cancel_form,
            'title': f'Chi tiết đơn hàng #{order.order_code}',
        },
    )


@login_required
def customer_order_cancel_view(request, pk):
    order = decorate_order(customer_order(request.user, pk))
    if request.method == 'GET':
        return redirect('orders:return_request', pk=pk)
    if request.method != 'POST':
        return HttpResponseNotAllowed(['GET', 'POST'])
    form = OrderCancelForm(request.POST)
    if form.is_valid():
        try:
            cancel_customer_cod_order(pk, request.user, **form.cleaned_data)
            messages.success(request, 'Đã hủy đơn và giải phóng hàng giữ chỗ.')
            return redirect('orders:order_detail', pk=pk)
        except ValidationError as error:
            map_validation_error_to_form(form, error)
    return order_detail_view(request, pk, cancel_form=form)


@login_required
def return_request_view(request, pk):
    order = decorate_order(customer_order(request.user, pk))
    if request.method not in ('GET', 'POST'):
        return HttpResponseNotAllowed(['GET', 'POST'])
    form = ReturnRequestForm(request.POST or None, request.FILES or None, order=order)
    if request.method == 'POST' and form.is_valid():
        try:
            result, created = submit_return_request(
                request.user, order.pk, form.cleaned_data, form.cleaned_data['attachments']
            )
            messages.success(
                request,
                (
                    'Đã gửi yêu cầu xử lý. CSKH sẽ phản hồi trong 30 phút.'
                    if result.status == ReturnStatus.PENDING
                    else 'Yêu cầu hủy đơn đã được xử lý thành công.'
                ),
            )
            return redirect('orders:order_detail', pk=order.pk)
        except ValidationError as error:
            map_validation_error_to_form(form, error)
        except (OperationalError, IntegrityError):
            form.add_error(
                None,
                'Đơn hàng vừa có thay đổi hoặc đã có yêu cầu khác. Vui lòng tải lại trước khi gửi.',
            )
    eligible, eligibility_message = return_eligibility(order)
    return render(
        request,
        'orders/return_request.html',
        {
            'order': order,
            'form': form,
            'eligible': eligible,
            'eligibility_message': eligibility_message,
            'title': 'Yêu cầu đổi trả & hủy đơn',
            'selected_items': set(form['items'].value() or []),
            'selected_type': form['request_type'].value(),
        },
    )


@login_required
def return_attachment_view(request, pk):
    attachment = get_object_or_404(ReturnAttachment.objects.select_related('request__order'), pk=pk)
    if attachment.request.customer_id != request.user.pk and not is_sales_staff(request.user):
        raise Http404
    try:
        stream = attachment.file.open('rb')
    except FileNotFoundError:
        raise Http404('Tệp minh chứng không còn tồn tại.')
    response = FileResponse(
        stream,
        as_attachment=True,
        filename=Path(attachment.original_name).name,
        content_type=attachment.content_type,
    )
    response['X-Content-Type-Options'] = 'nosniff'
    response['Cache-Control'] = 'private, no-store'
    return response


@login_required
def management_orders_view(request, selected_pk=None, action_form=None, action_name=None):
    require_staff(request.user)
    base = workspace_orders()
    status = request.GET.get('status', 'all')
    if status not in ('all', *STATUS_FILTERS.keys()):
        status = 'all'
    orders = base.filter(STATUS_FILTERS[status]) if status != 'all' else base
    query = request.GET.get('q', '').strip()[:150]
    if query:
        orders = orders.filter(
            Q(order_code__icontains=query)
            | Q(recipient_name__icontains=query)
            | Q(recipient_phone__icontains=query)
            | Q(items__sku_snapshot__icontains=query)
        )
    month = request.GET.get('month', '')
    if month:
        try:
            parsed = datetime.strptime(month, '%Y-%m')
            orders = orders.filter(created_at__year=parsed.year, created_at__month=parsed.month)
        except ValueError:
            month = ''
    page = Paginator(orders.distinct().order_by('-created_at', '-id'), 5).get_page(
        request.GET.get('page')
    )
    for order in page:
        decorate_order(order)
    selected = None
    selected_id = selected_pk or request.GET.get('selected')
    if (
        selected_id
        and len(str(selected_id)) <= 18
        and str(selected_id).isascii()
        and str(selected_id).isdigit()
    ):
        selected = get_object_or_404(base, pk=int(selected_id))
    elif page.object_list:
        selected = page.object_list[0]
    if selected:
        decorate_order(selected)
        selected.can_confirm = (
            selected.can_cancel
            and selected.order_status == OrderStatus.PENDING_CONFIRMATION
            and selected.payment.expected_amount == selected.grand_total
            and bool(selected.ui_items)
            and all(
                any(res.status == 'ACTIVE' for res in item.reservations.all())
                for item in selected.ui_items
            )
        )
    forms = {
        'confirm': OrderConfirmationForm(),
        'ship': OrderShipForm(pickup=bool(selected and selected.shipping_method == 'PICKUP')),
        'deliver': OrderDeliverForm(),
        'cancel': OrderCancelForm(),
        'collect': CODCollectionForm(initial={'amount': selected.grand_total if selected else ''}),
        'review': ReturnReviewForm(),
    }
    if selected and selected.latest_request:
        record = selected.latest_request
        forms['review'] = ReturnReviewForm(
            allow_complete=is_admin(request.user)
            and record.request_type == 'REFUND'
            and record.status == ReturnStatus.APPROVED
        )
    if action_name in forms and action_form is not None:
        forms[action_name] = action_form
    return render(
        request,
        'orders/management/workspace.html',
        {
            'page_obj': page,
            'tabs': status_tabs(base, status),
            'status': status,
            'search_q': query,
            'month': month,
            'selected_order': selected,
            'action_forms': forms,
            'action_name': action_name,
            'query_string': urlencode({'status': status, 'q': query, 'month': month}),
            'active_nav': 'orders',
            'title': 'Quản lý đơn hàng & Xử lý vận đơn',
            'is_admin': is_admin(request.user),
        },
    )


@login_required
def pending_orders_list_view(request):
    require_staff(request.user)
    query = request.GET.copy()
    query.setdefault('status', 'pending')
    return redirect(reverse('orders:management_orders_list') + '?' + query.urlencode())


@login_required
def order_management_detail_view(request, pk):
    return management_orders_view(request, selected_pk=pk)


@login_required
@require_POST
def management_action_view(request, pk, action):
    require_staff(request.user)
    order = get_object_or_404(workspace_orders(), pk=pk)
    form_types = {
        'confirm': OrderConfirmationForm,
        'ship': OrderShipForm,
        'deliver': OrderDeliverForm,
        'cancel': OrderCancelForm,
        'collect': CODCollectionForm,
        'review': ReturnReviewForm,
    }
    if action not in form_types:
        raise Http404
    record = None
    if action == 'review':
        return_id = request.POST.get('request_id', '')
        if not return_id.isascii() or not return_id.isdigit() or len(return_id) > 18:
            raise Http404('Mã yêu cầu không hợp lệ.')
        record = get_object_or_404(ReturnRequest, pk=return_id, order=order)
        form = ReturnReviewForm(
            request.POST,
            allow_complete=is_admin(request.user)
            and record.request_type == 'REFUND'
            and record.status == ReturnStatus.APPROVED,
        )
    else:
        form = (
            OrderShipForm(request.POST, pickup=order.shipping_method == 'PICKUP')
            if action == 'ship'
            else form_types[action](request.POST)
        )
    if form.is_valid():
        data = form.cleaned_data
        try:
            with transaction.atomic():
                order = Order.objects.select_for_update().get(pk=pk)
                if action == 'confirm':
                    confirm_order(pk, request.user, data['confirmation_note'])
                elif action == 'ship':
                    ship_order(pk, request.user, data['shipping_note'])
                    for field in (
                        'carrier_name',
                        'tracking_number',
                        'carrier_contact',
                        'estimated_delivery_at',
                    ):
                        setattr(order, field, data.get(field))
                    order.save(
                        update_fields=[
                            'carrier_name',
                            'tracking_number',
                            'carrier_contact',
                            'estimated_delivery_at',
                        ]
                    )
                elif action == 'deliver':
                    deliver_order(pk, request.user, data['delivery_note'])
                elif action == 'cancel':
                    cancel_customer_cod_order(pk, order.customer, **data, actor=request.user)
                elif action == 'collect':
                    record_cod_collection(
                        order.payment.pk,
                        request.user,
                        data['amount'],
                        data['reference'],
                        data['note'],
                    )
                else:
                    review_return_request(
                        record.pk,
                        request.user,
                        data['decision'],
                        data['note'],
                        data.get('refund_reference', ''),
                        data.get('received_return', False),
                    )
            messages.success(request, 'Thao tác đã được ghi nhận thành công.')
            return redirect(management_url(pk))
        except ValidationError as error:
            map_validation_error_to_form(form, error)
        except (OperationalError, IntegrityError):
            form.add_error(
                None, 'Dữ liệu vừa thay đổi hoặc trùng chứng từ. Vui lòng tải lại và kiểm tra.'
            )
    return management_orders_view(request, selected_pk=pk, action_form=form, action_name=action)


@login_required
def print_order_view(request, pk):
    order = get_object_or_404(workspace_orders(), pk=pk)
    if order.customer_id != request.user.pk and not is_sales_staff(request.user):
        raise Http404
    return render(
        request,
        'orders/print_order.html',
        {'order': decorate_order(order), 'delivery_slip': request.GET.get('type') == 'delivery'},
    )


@login_required
@require_POST
def buy_again_view(request, pk):
    order = customer_order(request.user, pk)
    cart, _ = Cart.objects.get_or_create(user=request.user)
    try:
        with transaction.atomic():
            for item in order.items.all():
                add_to_cart(cart, item.variant_id, str(item.quantity))
        messages.success(
            request, 'Đã thêm các sản phẩm còn khả dụng vào giỏ hàng theo giá hiện tại.'
        )
        return redirect('cart:cart_detail')
    except ValidationError as error:
        messages.error(request, '; '.join(error.messages))
        return redirect('orders:my_orders')


def confirm_order_action_view(request, pk):
    return management_action_view(request, pk, 'confirm')


def ship_order_action_view(request, pk):
    return management_action_view(request, pk, 'ship')


def deliver_order_action_view(request, pk):
    return management_action_view(request, pk, 'deliver')
