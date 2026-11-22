from decimal import Decimal
from django.conf import settings
from django.contrib import messages
from django.contrib.sessions.backends.base import UpdateError
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.http import HttpResponseForbidden, Http404, HttpResponseNotAllowed
from django.views.decorators.http import require_POST
from django.core.paginator import Paginator
from django.db.models import Q
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import (
    ROLE_ADMIN,
    ROLE_SALES_STAFF,
    is_admin,
    is_customer,
    is_sales_staff,
    role_required,
)
from catalog.models import Category, ProductReview
from catalog.utils import format_vnd
from cart.models import Cart
from cart.services import add_to_cart, parse_strictly_positive_int
from inventory.models import InventoryReservationStatus
from orders.checkout_helpers import (
    QuoteUnavailable,
    calculate_server_quote,
    clear_pending_order_code,
    get_eligible_public_coupon_choices,
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
    ReturnRequestForm,
    ReturnReviewForm,
    ReturnRefundConfirmationForm,
    OrderShipForm,
    PurchasedProductReviewForm,
)
from orders.models import (
    Order, OrderCancellation, OrderStatus, ReturnRequest, ReturnRequestStatus,
)
from orders.services import (
    create_cod_order_from_cart,
    create_cod_order_with_coupon_from_cart,
    create_qr_order_from_cart,
    create_qr_order_with_coupon_from_cart,
    notify_customer_transfer,
    confirm_order,
    ConfirmOrderResult,
    ship_order,
    ShipOrderResult,
    deliver_order,
    DeliverOrderResult,
    cancel_customer_cod_order,
    CancelOrderResult,
    _is_sqlite_lock_error,
)
from payments.models import Payment, PaymentMethod, PaymentStatus, RefundStatus
from payments.cod_forms import CODCollectionForm
from payments.cod_services import record_cod_collection
from payments.return_refund_services import create_return_refund_request, confirm_return_refund
from payments.qr import (
    generate_qr_svg,
    generate_vietqr_payload,
    get_qr_transfer_config,
    is_qr_transfer_available,
)


class _QuoteMismatch(Exception):
    def __init__(self, fresh_quote):
        super().__init__("Thông tin giỏ hàng đã thay đổi, vui lòng kiểm tra và xác nhận lại.")
        self.fresh_quote = fresh_quote


def _is_session_lock_error(exc: Exception) -> bool:
    if isinstance(exc, OperationalError):
        return _is_sqlite_lock_error(exc)
    if isinstance(exc, UpdateError):
        underlying = getattr(exc, '__cause__', None) or getattr(exc, '__context__', None)
        if isinstance(underlying, OperationalError):
            return _is_sqlite_lock_error(underlying)
    msg = str(exc).lower()
    return 'locked' in msg or 'busy' in msg


def _save_pending_checkout_data(request, db_available=True):
    if hasattr(request, 'session') and request.method == 'POST':
        request.session['pending_checkout_data'] = {
            'recipient_name': request.POST.get('recipient_name', ''),
            'recipient_phone': request.POST.get('recipient_phone', ''),
            'shipping_address': request.POST.get('shipping_address', ''),
            'coupon_code': request.POST.get('coupon_code', ''),
        }
        if not db_available:
            request.session.modified = False
            return
        try:
            request.session.save()
            request.session.modified = False
        except (OperationalError, UpdateError) as exc:
            if not _is_session_lock_error(exc):
                raise
            request.session.modified = False


def _get_initial_checkout_data(request, cart=None, checkout_token='', quote_token='', db_available=True):
    initial_data = {
        # Django's default get_full_name() returns "first_name last_name".
        # Our account model stores Vietnamese given name in first_name and
        # family/middle names in last_name, so checkout must render surname
        # first instead of reversing the customer's name on new orders.
        'recipient_name': ' '.join(
            part for part in (request.user.last_name, request.user.first_name) if part
        ) or request.user.username,
        'payment_method': PaymentMethod.COD,
        'checkout_token': checkout_token,
        'quote_token': quote_token,
    }
    if db_available:
        try:
            profile = getattr(request.user, 'customer_profile', None)
            if profile:
                if profile.phone:
                    initial_data['recipient_phone'] = profile.phone
                if profile.default_address:
                    initial_data['shipping_address'] = profile.default_address
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise

    if hasattr(request, 'session'):
        try:
            pending_data = request.session.get('pending_checkout_data')
            if isinstance(pending_data, dict):
                if pending_data.get('recipient_name'):
                    initial_data['recipient_name'] = pending_data['recipient_name']
                if pending_data.get('recipient_phone'):
                    initial_data['recipient_phone'] = pending_data['recipient_phone']
                if pending_data.get('shipping_address'):
                    initial_data['shipping_address'] = pending_data['shipping_address']
                if pending_data.get('coupon_code'):
                    initial_data['coupon_code'] = pending_data['coupon_code']
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
    return initial_data


def _refresh_quote_token(form, quote):
    """Keep a bound checkout form aligned with the server-calculated quote.

    Django renders values from ``form.data`` for a bound form, so changing only
    ``initial`` after a preview/quote mismatch leaves the old hidden token in
    the HTML.  That made a cart with a restored coupon impossible to submit:
    every subsequent request compared against the original, coupon-less quote.
    """
    quote_token = getattr(quote, 'quote_token', '')
    if not quote_token:
        return

    form.initial['quote_token'] = quote_token
    if form.is_bound:
        form_data = form.data.copy()
        form_data['quote_token'] = quote_token
        form.data = form_data


def _build_checkout_form(*args, cart=None, **kwargs):
    """Create a checkout form whose voucher choices match the live cart."""
    try:
        choices = get_eligible_public_coupon_choices(cart) if cart else []
    except OperationalError as exc:
        if not _is_sqlite_lock_error(exc):
            raise
        choices = []
    return CheckoutForm(*args, eligible_coupon_choices=choices, **kwargs)


def _render_safe_checkout(request, context):
    from django.template.loader import get_template
    from django.template import Context
    from django.middleware.csrf import get_token
    from django.http import HttpResponse

    template = get_template('orders/checkout.html')
    ctx_dict = dict(context)
    ctx_dict['csrf_token'] = get_token(request)
    ctx_dict['user'] = getattr(request, 'user', None)
    ctx_dict['request'] = request
    ctx_dict['messages'] = []
    ctx_dict['cart_item_count'] = 0
    ctx_dict['can_use_cart'] = True
    content = template.template.render(Context(ctx_dict))
    response = HttpResponse(content, status=200)
    response.context = context
    return response


def _render_checkout(request, context):
    context.setdefault('qr_available', is_qr_transfer_available())
    context.setdefault('qr_config', get_qr_transfer_config())

    if context.get('quote_unavailable') and hasattr(request, 'session'):
        try:
            if request.session.modified:
                request.session.save()
                request.session.modified = False
        except (OperationalError, UpdateError) as exc:
            if not _is_session_lock_error(exc):
                raise
            request.session.modified = False

    if context.get('quote_unavailable'):
        return _render_safe_checkout(request, context)

    try:
        return render(request, 'orders/checkout.html', context)
    except OperationalError as oe:
        if not _is_sqlite_lock_error(oe):
            raise
        return _render_safe_checkout(request, context)


@require_POST
def buy_now_view(request):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.path}")
    if not is_customer(request.user):
        raise PermissionDenied("Chỉ tài khoản Khách hàng mới có thể đặt hàng.")
    try:
        variant_id = int(request.POST.get('variant_id', ''))
        quantity = parse_strictly_positive_int(request.POST.get('quantity', '1'))
        Cart.objects.filter(user=request.user, is_direct_checkout=True).delete()
        direct_cart = Cart.objects.create(user=request.user, is_direct_checkout=True)
        add_to_cart(direct_cart, variant_id, quantity)
        request.session['_qhm_direct_checkout_cart_id'] = direct_cart.id
        request.session.modified = True
    except (ValueError, ValidationError) as error:
        messages.error(request, getattr(error, 'message', str(error)))
        return redirect(request.META.get('HTTP_REFERER') or 'catalog:public_catalog')
    return redirect('orders:checkout')


def checkout_view(request):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    if not is_customer(request.user):
        raise PermissionDenied("Bạn không có quyền truy cập chức năng này. Chỉ tài khoản Khách hàng (Customer) mới có quyền đặt hàng.")

    cart = None
    cart_lookup_locked = False
    try:
        direct_cart_id = request.session.get('_qhm_direct_checkout_cart_id')
        if direct_cart_id:
            cart = Cart.objects.filter(
                id=direct_cart_id, user=request.user, is_direct_checkout=True
            ).first()
            if cart is None:
                request.session.pop('_qhm_direct_checkout_cart_id', None)
        if cart is None:
            cart = Cart.objects.filter(user=request.user, is_direct_checkout=False).first()
    except OperationalError as oe:
        if not _is_sqlite_lock_error(oe):
            raise
        cart_lookup_locked = True

    categories = []
    if not cart_lookup_locked:
        try:
            categories = list(Category.objects.filter(is_visible=True).order_by('name'))
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            categories = []

    if cart_lookup_locked:
        raw_coupon = request.POST.get('coupon_code', '').strip().upper() if request.method == 'POST' else ''
        quote = QuoteUnavailable(coupon_code=raw_coupon)
        if request.method == 'POST':
            _save_pending_checkout_data(request)
            form = _build_checkout_form(request.POST)
            form.add_error(None, 'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.')
        else:
            initial_data = _get_initial_checkout_data(request, db_available=False)
            form = _build_checkout_form(initial=initial_data)
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': True,
            'cart': None,
            'categories': [],
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    if request.method == 'GET':
        if not cart or cart.items.count() == 0:
            try:
                quote = calculate_server_quote(None)
            except OperationalError as oe:
                if not _is_sqlite_lock_error(oe):
                    raise
                quote = QuoteUnavailable()
            context = {
                'form': None,
                'quote': quote,
                'quote_unavailable': getattr(quote, 'is_unavailable', False),
                'cart': cart,
                'categories': categories,
                'active_nav': 'cart',
                'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
            }
            return _render_checkout(request, context)

        order_code = get_or_create_pending_order_code(request, cart)
        checkout_token = make_checkout_token(request.user.id, cart.id, order_code)

        initial_data = _get_initial_checkout_data(
            request,
            cart=cart,
            checkout_token=checkout_token,
            db_available=True,
        )

        try:
            quote = calculate_server_quote(
                cart,
                coupon_code=initial_data.get('coupon_code', ''),
            )
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            quote = QuoteUnavailable()

        initial_data['quote_token'] = getattr(quote, 'quote_token', '')

        form = _build_checkout_form(initial=initial_data, cart=cart)
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': getattr(quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    if request.method != 'POST':
        return redirect('orders:checkout')

    action = request.POST.get('action', 'place')
    if action not in ('preview', 'place'):
        _save_pending_checkout_data(request)
        form = _build_checkout_form(request.POST, cart=cart)
        form.add_error(None, "Thao tác không được hỗ trợ.")
        try:
            quote = calculate_server_quote(cart, coupon_code=request.POST.get('coupon_code', ''))
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            quote = QuoteUnavailable()
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': getattr(quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    form = _build_checkout_form(request.POST, cart=cart)
    raw_coupon = request.POST.get('coupon_code', '').strip().upper()

    if not form.is_valid():
        _save_pending_checkout_data(request)
        try:
            quote = calculate_server_quote(cart, coupon_code=raw_coupon)
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            quote = QuoteUnavailable(coupon_code=raw_coupon)
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': getattr(quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    cleaned = form.cleaned_data
    coupon_code = cleaned.get('coupon_code') or None
    raw_checkout_token = cleaned.get('checkout_token') or request.POST.get('checkout_token')

    cart_id = cart.id if cart else 0
    order_code = verify_checkout_token(raw_checkout_token, request.user.id, cart_id)

    if not order_code:
        _save_pending_checkout_data(request)
        form.add_error(None, "Phiên đặt hàng không hợp lệ, đã hết hiệu lực hoặc bị thay đổi. Vui lòng tải lại trang.")
        try:
            quote = calculate_server_quote(cart, coupon_code=raw_coupon)
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            quote = QuoteUnavailable(coupon_code=raw_coupon)
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': getattr(quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    if action == 'preview':
        try:
            quote = calculate_server_quote(cart, coupon_code=coupon_code)
            if coupon_code and not quote.coupon_valid:
                form.add_error('coupon_code', quote.coupon_error or 'Mã giảm giá không hợp lệ.')
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            _save_pending_checkout_data(request)
            form.add_error(None, 'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.')
            quote = QuoteUnavailable(coupon_code=coupon_code or "")
        _refresh_quote_token(form, quote)
        context = {
            'form': form,
            'quote': quote,
            'quote_unavailable': getattr(quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
            'is_preview': True,
        }
        return _render_checkout(request, context)

    existing_order = None
    try:
        existing_order = Order.objects.filter(order_code=order_code).first()
    except OperationalError as oe:
        if not _is_sqlite_lock_error(oe):
            raise
        _save_pending_checkout_data(request)
        form.add_error(None, 'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.')
        context = {
            'form': form,
            'quote': QuoteUnavailable(coupon_code=coupon_code or ""),
            'quote_unavailable': True,
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    if existing_order is not None:
        try:
            payment_method = cleaned.get('payment_method', PaymentMethod.COD)
            if payment_method == PaymentMethod.QR:
                if coupon_code:
                    result = create_qr_order_with_coupon_from_cart(
                        customer=request.user,
                        recipient_name=cleaned['recipient_name'],
                        recipient_phone=cleaned['recipient_phone'],
                        shipping_address=cleaned['shipping_address'],
                        order_code=order_code,
                        coupon_code=coupon_code,
                        cart_id=cart.id if cart else None,
                        cart=cart,
                    )
                else:
                    result = create_qr_order_from_cart(
                        customer=request.user,
                        recipient_name=cleaned['recipient_name'],
                        recipient_phone=cleaned['recipient_phone'],
                        shipping_address=cleaned['shipping_address'],
                        order_code=order_code,
                        cart_id=cart.id if cart else None,
                        cart=cart,
                    )
            else:
                if coupon_code:
                    result = create_cod_order_with_coupon_from_cart(
                        customer=request.user,
                        recipient_name=cleaned['recipient_name'],
                        recipient_phone=cleaned['recipient_phone'],
                        shipping_address=cleaned['shipping_address'],
                        order_code=order_code,
                        coupon_code=coupon_code,
                        cart_id=cart.id if cart else None,
                        cart=cart,
                    )
                else:
                    result = create_cod_order_from_cart(
                        customer=request.user,
                        recipient_name=cleaned['recipient_name'],
                        recipient_phone=cleaned['recipient_phone'],
                        shipping_address=cleaned['shipping_address'],
                        order_code=order_code,
                        cart_id=cart.id if cart else None,
                        cart=cart,
                    )
            clear_pending_order_code(request, cart)
            messages.info(request, f"Đơn hàng #{result.order.order_code} đã được đặt trước đó.")
            return redirect('orders:order_detail', pk=result.order.pk)
        except ValidationError as e:
            map_validation_error_to_form(form, e)
            try:
                quote = calculate_server_quote(cart, coupon_code=coupon_code)
            except OperationalError as oe:
                if _is_sqlite_lock_error(oe):
                    quote = QuoteUnavailable(coupon_code=coupon_code or "")
                else:
                    raise
            context = {
                'form': form,
                'quote': quote,
                'quote_unavailable': getattr(quote, 'is_unavailable', False),
                'cart': cart,
                'categories': categories,
                'active_nav': 'cart',
                'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
            }
            return _render_checkout(request, context)
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            _save_pending_checkout_data(request)
            form.add_error(None, 'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.')
            context = {
                'form': form,
                'quote': QuoteUnavailable(coupon_code=coupon_code or ""),
                'quote_unavailable': True,
                'cart': cart,
                'categories': categories,
                'active_nav': 'cart',
                'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
            }
            return _render_checkout(request, context)

    created_order = None
    is_replay = False
    try:
        with transaction.atomic():
            committed_order = Order.objects.filter(order_code=order_code).first()
            if committed_order is not None:
                payment_method = cleaned.get('payment_method', PaymentMethod.COD)
                if payment_method == PaymentMethod.QR:
                    if coupon_code:
                        replay_res = create_qr_order_with_coupon_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            coupon_code=coupon_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                    else:
                        replay_res = create_qr_order_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                else:
                    if coupon_code:
                        replay_res = create_cod_order_with_coupon_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            coupon_code=coupon_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                    else:
                        replay_res = create_cod_order_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                created_order = replay_res.order
                is_replay = True
            else:
                submitted_fp = verify_quote_token(cleaned.get('quote_token'))
                fresh_quote = calculate_server_quote(cart, coupon_code=coupon_code)

                if not fresh_quote.is_valid or fresh_quote.has_unavailable:
                    raise ValidationError(
                        fresh_quote.error_message or "Giỏ hàng có sản phẩm không khả dụng hoặc đã thay đổi."
                    )

                if coupon_code and not fresh_quote.coupon_valid:
                    raise ValidationError({
                        'coupon_code': fresh_quote.coupon_error or 'Mã giảm giá không hợp lệ.'
                    })

                if submitted_fp is None or fresh_quote.fingerprint != submitted_fp:
                    raise _QuoteMismatch(fresh_quote)

                payment_method = cleaned.get('payment_method', PaymentMethod.COD)
                if payment_method == PaymentMethod.QR:
                    if coupon_code:
                        creation_res = create_qr_order_with_coupon_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            coupon_code=coupon_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                    else:
                        creation_res = create_qr_order_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                else:
                    if coupon_code:
                        creation_res = create_cod_order_with_coupon_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            coupon_code=coupon_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                    else:
                        creation_res = create_cod_order_from_cart(
                            customer=request.user,
                            recipient_name=cleaned['recipient_name'],
                            recipient_phone=cleaned['recipient_phone'],
                            shipping_address=cleaned['shipping_address'],
                            order_code=order_code,
                            cart_id=cart.id if cart else None,
                            cart=cart,
                        )
                created_order = creation_res.order
                is_replay = creation_res.is_replay

    except _QuoteMismatch as qm:
        fresh_quote = qm.fresh_quote
        _refresh_quote_token(form, fresh_quote)
        messages.warning(request, "Thông tin giỏ hàng đã thay đổi, vui lòng kiểm tra và xác nhận lại.")
        context = {
            'form': form,
            'quote': fresh_quote,
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
            'quote_changed_warning': True,
        }
        return _render_checkout(request, context)

    except ValidationError as ve:
        map_validation_error_to_form(form, ve)
        try:
            fresh_quote = calculate_server_quote(cart, coupon_code=coupon_code)
        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe):
                raise
            fresh_quote = QuoteUnavailable(coupon_code=coupon_code or "")
        context = {
            'form': form,
            'quote': fresh_quote,
            'quote_unavailable': getattr(fresh_quote, 'is_unavailable', False),
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    except OperationalError as oe:
        if not _is_sqlite_lock_error(oe):
            raise
        _save_pending_checkout_data(request)
        form.add_error(None, 'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.')
        context = {
            'form': form,
            'quote': QuoteUnavailable(coupon_code=coupon_code or ""),
            'quote_unavailable': True,
            'cart': cart,
            'categories': categories,
            'active_nav': 'cart',
            'title': 'Xác nhận đặt hàng — Quốc Hùng Mobile',
        }
        return _render_checkout(request, context)

    if created_order is not None:
        clear_pending_order_code(request, cart)
        if cart and cart.is_direct_checkout:
            request.session.pop('_qhm_direct_checkout_cart_id', None)
            cart.delete()
        if is_replay:
            messages.info(request, f"Đơn hàng #{created_order.order_code} đã được đặt trước đó.")
        else:
            messages.success(request, f"Đặt hàng thành công! Đơn hàng của bạn đã được ghi nhận với mã #{created_order.order_code}.")
        return redirect('orders:order_detail', pk=created_order.pk)

    return redirect('orders:checkout')


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


def order_detail_view(request, pk, cancel_form=None):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    if request.method == 'POST' and cancel_form is None:
        return order_notify_transfer_view(request, pk)

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

    qr_svg = ''
    qr_payload = ''
    recipient_snapshot = None
    has_recipient_snapshot = False
    is_expired = False

    if payment and payment.method == PaymentMethod.QR:
        now = timezone.now()
        is_expired = bool(payment.expires_at and now >= payment.expires_at)
        ref_text = payment.qr_reference or order.order_code
        recipient_snapshot = payment.get_recipient_snapshot()
        has_recipient_snapshot = recipient_snapshot is not None

        is_reconciliation = (order.order_status == OrderStatus.PENDING_RECONCILIATION)
        if has_recipient_snapshot and payment.status == PaymentStatus.UNPAID and not is_expired and not is_reconciliation:
            qr_payload = generate_vietqr_payload(
                bank_bin=recipient_snapshot.get('bank_bin', ''),
                account_number=recipient_snapshot.get('account_number', ''),
                amount=payment.expected_amount,
                reference=ref_text,
            )
            qr_svg = generate_qr_svg(qr_payload, border=4, size=240)

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
        'qr_svg': qr_svg,
        'qr_payload': qr_payload,
        'recipient_snapshot': recipient_snapshot,
        'has_recipient_snapshot': has_recipient_snapshot,
        'is_expired': is_expired,
        'can_cancel': can_cancel,
        'cancel_form': cancel_form,
        'cancellation': cancellation,
        'status_history': status_history,
        'categories': categories,
        'active_nav': 'cart',
        'title': f"Đơn hàng #{order.order_code} — Quốc Hùng Mobile",
    }
    return render(request, 'orders/order_detail.html', context)


def purchased_product_review_view(request, pk, item_pk):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    order = get_object_or_404(Order, pk=pk, customer=request.user)
    item = get_object_or_404(order.items.select_related('variant__product'), pk=item_pk)
    if order.order_status != OrderStatus.DELIVERED:
        messages.error(request, 'Bạn chỉ có thể đánh giá sản phẩm sau khi đơn hàng đã giao thành công.')
        return redirect('orders:order_detail', pk=order.pk)

    existing_review = ProductReview.objects.filter(order_item=item).first()
    if existing_review:
        messages.info(request, 'Bạn đã đánh giá sản phẩm này trong đơn hàng.')
        return redirect(f"{reverse('catalog:public_product_detail', args=[item.variant.product_id])}#reviews")

    if request.method == 'POST':
        form = PurchasedProductReviewForm(request.POST)
        if form.is_valid():
            ProductReview.objects.create(
                product=item.variant.product,
                user=request.user,
                order_item=item,
                reviewer_name=request.user.get_full_name().strip() or request.user.username,
                rating=int(form.cleaned_data['rating']),
                variant_text=item.variant_snapshot,
                content=form.cleaned_data['content'],
                is_verified_purchase=True,
            )
            messages.success(request, 'Cảm ơn bạn. Đánh giá đã được đăng trên trang sản phẩm.')
            return redirect(f"{reverse('catalog:public_product_detail', args=[item.variant.product_id])}#reviews")
    else:
        form = PurchasedProductReviewForm()

    return render(request, 'orders/purchased_product_review.html', {
        'order': order, 'item': item, 'form': form,
        'categories': Category.objects.filter(is_visible=True).order_by('name'),
        'active_nav': 'cart', 'title': f'Đánh giá {item.product_name_snapshot} — Quốc Hùng Mobile',
    })


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


def order_notify_transfer_view(request, pk):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    if not is_customer(request.user):
        raise PermissionDenied("Bạn không có quyền thực hiện thao tác này. Chỉ tài khoản Khách hàng mới có quyền thông báo chuyển khoản.")

    if request.method != 'POST':
        return redirect('orders:order_detail', pk=pk)

    order = get_object_or_404(Order, pk=pk, customer=request.user)

    try:
        result = notify_customer_transfer(order_id=order.id, customer=request.user)
        status = result.get('status')
        if status == 'expired':
            messages.warning(
                request,
                result.get('message') or "Đơn hàng đã hết thời hạn thanh toán chuyển khoản QR (30 phút). Thông tin giao dịch cần được nhân viên đối soát và xử lý thủ công. Chỗ giữ hàng và ưu đãi của bạn vẫn được bảo toàn."
            )
        elif status == 'reconciliation':
            messages.info(
                request,
                result.get('message') or "Đơn hàng đang trong quá trình rà soát và đối soát thông tin thanh toán. Chỗ giữ hàng và ưu đãi của bạn vẫn được bảo toàn nguyên vẹn."
            )
        elif status == 'already_notified':
            messages.info(
                request,
                "Bạn đã gửi thông báo chuyển khoản trước đó. Đơn hàng đang chờ nhân viên kiểm tra và xác minh."
            )
        else:
            messages.success(
                request,
                "Đã ghi nhận thông báo chuyển khoản thành công! Nhân viên sẽ kiểm tra và xác minh giao dịch của bạn."
            )
    except ValidationError as e:
        msg = e.message if hasattr(e, 'message') else str(e)
        messages.error(request, msg)

    return redirect('orders:order_detail', pk=pk)


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
    if method_filter in [PaymentMethod.COD, PaymentMethod.QR]:
        orders_qs = orders_qs.filter(payment__method=method_filter)

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
            elif p.method == PaymentMethod.QR:
                if p.status == PaymentStatus.PAID:
                    o.can_confirm = True
                    o.eligibility_reason = "Đủ điều kiện (QR Đã thanh toán)"
                elif p.status == PaymentStatus.PENDING_VERIFY:
                    o.eligibility_reason = "Chờ nhân viên xác minh QR"
                elif p.status == PaymentStatus.UNPAID:
                    o.eligibility_reason = "Chờ khách chuyển khoản QR"
                else:
                    o.eligibility_reason = f"QR không hợp lệ ({p.get_status_display()})"

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
        elif payment.method == PaymentMethod.QR:
            if payment.status == PaymentStatus.PAID:
                can_confirm = True
            elif payment.status == PaymentStatus.PENDING_VERIFY:
                ineligible_reason = "Đơn hàng QR đã có thông báo chuyển khoản nhưng chưa được nhân viên xác minh thanh toán PAID."
            elif payment.status == PaymentStatus.UNPAID:
                ineligible_reason = "Đơn hàng QR chưa hoàn tất chuyển khoản và chưa thanh toán."
            else:
                ineligible_reason = f"Đơn hàng QR ở trạng thái thanh toán không hợp lệ ({payment.get_status_display()})."
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
        elif payment.method == PaymentMethod.QR:
            if payment.status == PaymentStatus.PAID:
                can_ship = True
            elif payment.status == PaymentStatus.PENDING_VERIFY:
                ineligible_ship_reason = "Đơn hàng QR đã có thông báo chuyển khoản nhưng chưa được nhân viên xác minh thanh toán PAID."
            elif payment.status == PaymentStatus.UNPAID:
                ineligible_ship_reason = "Đơn hàng QR chưa hoàn tất chuyển khoản và chưa thanh toán."
            else:
                ineligible_ship_reason = f"Đơn hàng QR ở trạng thái thanh toán không hợp lệ ({payment.get_status_display()})."
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
        elif payment.method == PaymentMethod.QR:
            if payment.status == PaymentStatus.PAID:
                can_deliver = True
            elif payment.status == PaymentStatus.PENDING_VERIFY:
                ineligible_deliver_reason = "Đơn hàng QR đã có thông báo chuyển khoản nhưng chưa được nhân viên xác minh thanh toán PAID."
            elif payment.status == PaymentStatus.UNPAID:
                ineligible_deliver_reason = "Đơn hàng QR chưa hoàn tất chuyển khoản và chưa thanh toán."
            else:
                ineligible_deliver_reason = f"Đơn hàng QR ở trạng thái thanh toán không hợp lệ ({payment.get_status_display()})."
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


