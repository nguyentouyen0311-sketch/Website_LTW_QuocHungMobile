from django.conf import settings
from django.contrib import messages
from django.contrib.sessions.backends.base import UpdateError
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.views.decorators.http import require_POST
from django.shortcuts import redirect, render, resolve_url
from accounts.permissions import is_customer
from catalog.models import Category
from cart.models import Cart
from cart.services import add_to_cart, parse_strictly_positive_int
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
from orders.forms import CheckoutForm
from orders.models import Order
from orders.services.checkout import (
    create_cod_order_from_cart,
    create_cod_order_with_coupon_from_cart,
)
from orders.services.common import _is_sqlite_lock_error
from payments.models import PaymentMethod


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
