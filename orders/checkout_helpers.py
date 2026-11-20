from decimal import Decimal, ROUND_HALF_UP
import hashlib
import json
import time
import uuid
from typing import NamedTuple, Optional, List, Dict, Any

from django.core import signing
from django.core.exceptions import ValidationError
from django.db import router, transaction, OperationalError
from django.utils import timezone

from catalog.models import ProductVariant
from catalog.utils import format_vnd
from cart.services import get_cart_evaluation
from coupons.evaluation import evaluate_coupon_for_cart
from coupons.models import Coupon, DiscountType
from orders.models import Order

CHECKOUT_TOKEN_SALT = 'orders.checkout.token'
QUOTE_TOKEN_SALT = 'orders.checkout.quote'


class ServerQuote(NamedTuple):
    lines: List[Dict[str, Any]]
    subtotal: Decimal
    subtotal_formatted: str
    discount_total: Decimal
    discount_total_formatted: str
    grand_total: Decimal
    grand_total_formatted: str
    coupon_code: Optional[str]
    coupon_valid: bool
    coupon_error: Optional[str]
    has_unavailable: bool
    unavailable_count: int
    total_quantity: int
    is_valid: bool
    error_message: Optional[str]
    fingerprint: str
    quote_token: str


class QuoteUnavailable:
    def __init__(self, coupon_code: str = ""):
        self.lines: List[Dict[str, Any]] = []
        self.subtotal = Decimal('0.00')
        self.subtotal_formatted = "Chưa sẵn sàng"
        self.discount_total = Decimal('0.00')
        self.discount_total_formatted = "0 đ"
        self.grand_total = Decimal('0.00')
        self.grand_total_formatted = "Chưa sẵn sàng"
        self.coupon_code = coupon_code or ""
        self.coupon_valid = False
        self.coupon_error = None
        self.has_unavailable = True
        self.unavailable_count = 0
        self.total_quantity = 0
        self.is_valid = False
        self.error_message = "Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát."
        self.fingerprint = ""
        self.quote_token = ""
        self.is_unavailable = True


def generate_order_code() -> str:
    return f"QHM{uuid.uuid4().hex[:14].upper()}"


def get_or_create_pending_order_code(request, cart) -> str:
    session_key = f"_qhm_checkout_pending_{cart.id}"
    code = request.session.get(session_key)

    if code and isinstance(code, str):
        if Order.objects.filter(order_code=code).exists():
            code = generate_order_code()
            request.session[session_key] = code
            request.session.modified = True
    else:
        code = generate_order_code()
        request.session[session_key] = code
        request.session.modified = True

    return code


def clear_pending_order_code(request, cart=None) -> None:
    if hasattr(request, 'session'):
        if cart and hasattr(cart, 'id'):
            session_key = f"_qhm_checkout_pending_{cart.id}"
            request.session.pop(session_key, None)
        request.session.pop('pending_checkout_data', None)
        request.session.modified = True


def get_or_create_buy_now_pending_order_code(
    request,
    variant_id: int,
    quantity: int,
    attempt_id: Optional[str] = None,
) -> str:
    bn_ctx = getattr(request, 'session', {}).get('buy_now_context') if hasattr(request, 'session') else None
    if bn_ctx and isinstance(bn_ctx, dict) and bn_ctx.get('order_code'):
        return bn_ctx['order_code']

    session_key = f"_qhm_checkout_pending_bn_{attempt_id}" if attempt_id else f"_qhm_checkout_pending_bn_{variant_id}_{quantity}"
    code = request.session.get(session_key)

    if code and isinstance(code, str):
        return code
    else:
        code = generate_order_code()
        request.session[session_key] = code
        request.session.modified = True

    return code


def clear_buy_now_pending_order_code(
    request,
    variant_id: int,
    quantity: int,
    attempt_id: Optional[str] = None,
) -> None:
    if hasattr(request, 'session'):
        request.session.pop(f"_qhm_checkout_pending_bn_{variant_id}_{quantity}", None)
        if attempt_id:
            request.session.pop(f"_qhm_checkout_pending_bn_{attempt_id}", None)
        request.session.pop('pending_checkout_data', None)
        request.session.modified = True


_pre_persistence_hook = None
_post_read_hook = None


def attach_conditional_buy_now_session_save(request, bound_attempt_id: Optional[str] = None) -> None:
    if not hasattr(request, 'session') or not request.session.session_key:
        return

    session = request.session
    if getattr(session, '_qhm_conditional_save_attached', False):
        if bound_attempt_id:
            session._qhm_bound_attempt_id = bound_attempt_id
        return

    if bound_attempt_id:
        session._qhm_bound_attempt_id = bound_attempt_id

    original_save = session.save

    def conditional_save(must_create=False):
        global _pre_persistence_hook, _post_read_hook
        pre_hook = getattr(session, '_qhm_pre_persistence_hook', None) or _pre_persistence_hook
        if callable(pre_hook):
            try:
                pre_hook()
            except Exception:
                pass

        if must_create or not session.session_key:
            return original_save(must_create=must_create)

        SessionModel = session.model
        using = router.db_for_write(SessionModel)
        max_attempts = 10

        for attempt_idx in range(max_attempts):
            try:
                record = SessionModel.objects.using(using).filter(
                    session_key=session.session_key
                ).first()

                if record is None:
                    return original_save(must_create=must_create)

                old_session_data = record.session_data
                current_bound_id = getattr(session, '_qhm_bound_attempt_id', None)

                post_hook = getattr(session, '_qhm_post_read_hook', None) or _post_read_hook
                if callable(post_hook):
                    try:
                        post_hook()
                    except Exception:
                        pass
                    _post_read_hook = None
                    if hasattr(session, '_qhm_post_read_hook'):
                        session._qhm_post_read_hook = None

                try:
                    db_dict = session.decode(old_session_data)
                except Exception:
                    db_dict = {}

                db_ctx = db_dict.get('buy_now_context')
                session_data = session._get_session(no_load=True)

                if db_ctx and isinstance(db_ctx, dict):
                    db_aid = db_ctx.get('attempt_id')
                    if db_aid and db_aid != current_bound_id:
                        session_data['buy_now_context'] = db_ctx
                        pending_code_key = f"_qhm_checkout_pending_bn_{db_aid}"
                        if pending_code_key in db_dict:
                            session_data[pending_code_key] = db_dict[pending_code_key]
                        if current_bound_id:
                            session_data.pop(f"_qhm_checkout_pending_bn_{current_bound_id}", None)

                new_session_data = session.encode(session_data)
                new_expiry_date = session.get_expiry_date()

                updated = SessionModel.objects.using(using).filter(
                    session_key=session.session_key,
                    session_data=old_session_data
                ).update(
                    session_data=new_session_data,
                    expire_date=new_expiry_date
                )

                if updated == 1:
                    session.modified = False
                    return
                else:
                    time.sleep(0.02)
                    continue

            except OperationalError:
                time.sleep(0.02)
                continue
            except Exception:
                time.sleep(0.02)
                continue

        return

    session.save = conditional_save
    session._qhm_conditional_save_attached = True


def get_session_binding(session_key: Optional[str]) -> Optional[str]:
    if not session_key or not isinstance(session_key, str):
        return None
    return hashlib.sha256(f"qhm.checkout.session:{session_key}".encode('utf-8')).hexdigest()[:32]


def make_checkout_token(
    user_id: int,
    cart_id: int,
    order_code: str,
    source: str = 'cart',
    variant_id: Optional[int] = None,
    quantity: Optional[int] = None,
    attempt_id: Optional[str] = None,
    session_key: Optional[str] = None,
) -> str:
    payload = {
        'user_id': user_id,
        'cart_id': cart_id,
        'order_code': order_code,
        'source': source,
    }
    if source == 'buy_now':
        payload['variant_id'] = variant_id
        payload['quantity'] = quantity
        if attempt_id is not None:
            payload['attempt_id'] = str(attempt_id)
        if session_key is not None:
            payload['session_hash'] = get_session_binding(session_key)
    return signing.dumps(payload, salt=CHECKOUT_TOKEN_SALT)


def verify_checkout_token(
    token: Optional[str],
    user_id: Optional[int] = None,
    cart_id: Optional[int] = None,
    expected_source: str = 'cart',
    expected_variant_id: Optional[int] = None,
    expected_quantity: Optional[int] = None,
    expected_attempt_id: Optional[str] = None,
    expected_session_key: Optional[str] = None,
    expected_order_code: Optional[str] = None,
    expected_user_id: Optional[int] = None,
) -> Optional[str]:
    target_user_id = user_id if user_id is not None else expected_user_id
    if target_user_id is None:
        return None
    if not token or not isinstance(token, str):
        return None
    try:
        data = signing.loads(token, salt=CHECKOUT_TOKEN_SALT)
        if not isinstance(data, dict):
            return None
        if data.get('user_id') != target_user_id:
            return None
        token_source = data.get('source', 'cart')
        if token_source != expected_source:
            return None
        if expected_source == 'cart':
            if cart_id is not None and data.get('cart_id') != cart_id:
                return None
        elif expected_source == 'buy_now':
            if expected_variant_id is not None and data.get('variant_id') != expected_variant_id:
                return None
            if expected_quantity is not None and data.get('quantity') != expected_quantity:
                return None
            if expected_attempt_id is not None and data.get('attempt_id') != str(expected_attempt_id):
                return None
            if expected_session_key is not None:
                expected_hash = get_session_binding(expected_session_key)
                token_hash = data.get('session_hash')
                if not token_hash and data.get('session_key'):
                    token_hash = get_session_binding(data.get('session_key'))
                if token_hash != expected_hash:
                    return None
        order_code = data.get('order_code')
        if not order_code or not isinstance(order_code, str):
            return None
        if expected_order_code is not None and order_code != expected_order_code:
            return None
        return order_code
    except (signing.BadSignature, Exception):
        return None


def make_quote_token(fingerprint: str) -> str:
    return signing.dumps({'fp': fingerprint}, salt=QUOTE_TOKEN_SALT)


def verify_quote_token(quote_token: Optional[str]) -> Optional[str]:
    if not quote_token or not isinstance(quote_token, str):
        return None
    try:
        data = signing.loads(quote_token, salt=QUOTE_TOKEN_SALT)
        if isinstance(data, dict):
            return data.get('fp')
    except (signing.BadSignature, Exception):
        pass
    return None


def calculate_server_quote(
    cart, coupon_code: Optional[str] = None, pricing_instant=None
) -> ServerQuote:
    pricing_at = pricing_instant or timezone.now()
    eval_res = get_cart_evaluation(cart)
    raw_lines = eval_res.get('lines', [])

    if not cart or not raw_lines:
        fp = "EMPTY_CART"
        q_token = make_quote_token(fp)
        return ServerQuote(
            lines=[],
            subtotal=Decimal('0.00'),
            subtotal_formatted=format_vnd(Decimal('0.00')),
            discount_total=Decimal('0.00'),
            discount_total_formatted=format_vnd(Decimal('0.00')),
            grand_total=Decimal('0.00'),
            grand_total_formatted=format_vnd(Decimal('0.00')),
            coupon_code=None,
            coupon_valid=False,
            coupon_error=None,
            has_unavailable=False,
            unavailable_count=0,
            total_quantity=0,
            is_valid=False,
            error_message="Giỏ hàng của bạn đang trống.",
            fingerprint=fp,
            quote_token=q_token,
        )

    has_unavailable = eval_res.get('has_unavailable_lines', False)
    unavailable_count = eval_res.get('unavailable_count', 0)
    total_quantity = eval_res.get('total_quantity', 0)
    subtotal = eval_res.get('subtotal_available', Decimal('0.00'))

    processed_lines = []
    for line in raw_lines:
        item = line['item']
        variant = line['variant']
        unit_price = (
            line.get('current_price')
            if line.get('current_price') is not None
            else (variant.price or Decimal('0.00'))
        )
        qty = getattr(item, 'quantity', line.get('quantity', 1))
        line_total = (
            line.get('line_total')
            if line.get('line_total') is not None
            else (unit_price * Decimal(qty))
        )
        processed_lines.append(
            {
                'item_id': item.id,
                'variant_id': variant.id,
                'product_name': variant.product.name,
                'attrs_display': line.get('attributes_display') or line.get('attrs_display', ''),
                'sku': variant.sku,
                'image': line.get('image'),
                'quantity': qty,
                'unit_price': unit_price,
                'unit_price_formatted': format_vnd(unit_price),
                'line_total': line_total,
                'line_total_formatted': format_vnd(line_total),
                'is_available': line.get('is_available', False),
                'availability_message': line.get('status_text')
                or line.get('availability_message', ''),
                'has_price_changed': line.get('has_price_changed', False),
                'price_change_message': line.get('old_price_formatted')
                or line.get('price_change_message', ''),
            }
        )

    clean_code = (
        str(coupon_code).strip().upper() if coupon_code and str(coupon_code).strip() else None
    )
    discount_total = Decimal('0.00')
    coupon_valid = False
    coupon_error = None

    if clean_code:
        try:
            coupon_res = evaluate_coupon_for_cart(cart, clean_code, at=pricing_at)
            discount_total = coupon_res['discount_amount']
            coupon_valid = True
        except ValidationError as e:
            coupon_valid = False
            coupon_error = e.message if hasattr(e, 'message') else str(e)
            discount_total = Decimal('0.00')

    grand_total = max(Decimal('0.00'), subtotal - discount_total)

    is_valid = not has_unavailable and (subtotal > Decimal('0.00') or len(processed_lines) > 0)
    error_message = None
    if has_unavailable:
        error_message = "Giỏ hàng có sản phẩm không khả dụng. Vui lòng cập nhật trước khi đặt hàng."

    line_fingerprints = [
        f"{l['item_id']}:{l['variant_id']}:{l['quantity']}:{l['unit_price']}:{l['is_available']}"
        for l in sorted(processed_lines, key=lambda x: x['item_id'])
    ]
    raw_fp_str = "|".join(
        [
            ",".join(line_fingerprints),
            f"subtotal={subtotal}",
            f"coupon={clean_code or ''}",
            f"coupon_valid={coupon_valid}",
            f"discount={discount_total}",
            f"grand_total={grand_total}",
        ]
    )
    fingerprint = hashlib.sha256(raw_fp_str.encode('utf-8')).hexdigest()[:32]
    quote_token = make_quote_token(fingerprint)

    return ServerQuote(
        lines=processed_lines,
        subtotal=subtotal,
        subtotal_formatted=format_vnd(subtotal),
        discount_total=discount_total,
        discount_total_formatted=format_vnd(discount_total),
        grand_total=grand_total,
        grand_total_formatted=format_vnd(grand_total),
        coupon_code=clean_code,
        coupon_valid=coupon_valid,
        coupon_error=coupon_error,
        has_unavailable=has_unavailable,
        unavailable_count=unavailable_count,
        total_quantity=total_quantity,
        is_valid=is_valid,
        error_message=error_message,
        fingerprint=fingerprint,
        quote_token=quote_token,
    )


def map_validation_error_to_form(form, err: ValidationError) -> None:
    if hasattr(err, 'message_dict'):
        for field, messages in err.message_dict.items():
            if field in form.fields:
                form.add_error(field, messages)
            else:
                form.add_error(None, messages)
    elif hasattr(err, 'messages'):
        form.add_error(None, err.messages)
    else:
        form.add_error(None, str(err))


def format_variant_attributes_display(variant) -> str:
    if not variant:
        return "Phiên bản mặc định"
    links = list(variant.attribute_values.select_related('attribute', 'attribute_value').all())
    if links:
        links.sort(key=lambda x: (x.attribute.name, x.id))
        return ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
    return "Phiên bản mặc định"


def evaluate_coupon_for_buy_now(variant, quantity: int, code: str, at=None) -> Dict[str, Any]:
    if not variant or quantity <= 0:
        raise ValidationError(
            "Thông tin phiên bản hoặc số lượng Mua ngay không hợp lệ.",
            code="buy_now_invalid"
        )

    unit_price = variant.price if (variant.price and variant.price >= Decimal('0.00')) else Decimal('0.00')
    subtotal = unit_price * Decimal(quantity)

    if not code or not isinstance(code, str) or not code.strip():
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    normalized_code = code.strip().upper()
    if len(normalized_code) > 40:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    try:
        coupon = Coupon.objects.get(code=normalized_code)
    except Coupon.DoesNotExist:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    if not coupon.is_active:
        raise ValidationError(
            "Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.",
            code="coupon_invalid"
        )

    eval_at = at or timezone.now()
    if eval_at < coupon.starts_at:
        raise ValidationError(
            "Mã giảm giá chưa đến thời gian áp dụng.",
            code="coupon_not_started"
        )

    if eval_at >= coupon.ends_at:
        raise ValidationError(
            "Mã giảm giá đã hết hạn sử dụng.",
            code="coupon_expired"
        )

    current_consumed = (coupon.used_count or 0) + (coupon.reserved_count or 0)
    if current_consumed >= coupon.max_uses:
        raise ValidationError(
            "Mã giảm giá đã hết lượt sử dụng.",
            code="coupon_exhausted"
        )

    if coupon.min_order_amount and coupon.min_order_amount > Decimal('0.00'):
        if subtotal < coupon.min_order_amount:
            min_str = format_vnd(coupon.min_order_amount)
            raise ValidationError(
                f"Đơn hàng chưa đạt giá trị tối thiểu ({min_str}) để áp dụng mã giảm giá này.",
                code="min_order_not_reached"
            )

    is_eligible = coupon.applies_to_all or coupon.coupon_products.filter(product_id=variant.product_id).exists()
    if not is_eligible:
        raise ValidationError(
            "Sản phẩm Mua ngay không thỏa mãn điều kiện áp dụng mã giảm giá này.",
            code="no_eligible_products"
        )

    if coupon.discount_type == DiscountType.PERCENT:
        raw_discount = (subtotal * coupon.value) / Decimal('100')
        if coupon.max_discount is not None:
            raw_discount = min(raw_discount, coupon.max_discount)
    elif coupon.discount_type == DiscountType.FIXED:
        raw_discount = min(coupon.value, subtotal)
    else:
        raise ValidationError(
            "Loại hình giảm giá không được hỗ trợ.",
            code="unsupported_discount_type"
        )

    discount_amount = raw_discount.quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    discount_amount = max(Decimal('0.00'), min(discount_amount, subtotal))
    total_after_discount = max(Decimal('0.00'), subtotal - discount_amount)

    return {
        'coupon_id': coupon.id,
        'code': coupon.code,
        'cart_subtotal': subtotal,
        'eligible_subtotal': subtotal,
        'discount_amount': discount_amount,
        'total_after_discount': total_after_discount,
        'has_unavailable_lines': False,
    }


def calculate_buy_now_server_quote(
    variant_id: int,
    quantity: int,
    coupon_code: Optional[str] = None,
    pricing_instant=None,
) -> ServerQuote:
    eval_at = pricing_instant or timezone.now()
    variant = (
        ProductVariant.objects
        .select_related('product__category', 'inventory')
        .prefetch_related('attribute_values__attribute', 'attribute_values__attribute_value', 'images', 'product__images')
        .filter(id=variant_id)
        .first()
    )

    if not variant:
        fp = "BUY_NOW_VARIANT_MISSING"
        return ServerQuote(
            lines=[],
            subtotal=Decimal('0.00'),
            subtotal_formatted=format_vnd(Decimal('0.00')),
            discount_total=Decimal('0.00'),
            discount_total_formatted=format_vnd(Decimal('0.00')),
            grand_total=Decimal('0.00'),
            grand_total_formatted=format_vnd(Decimal('0.00')),
            coupon_code=None,
            coupon_valid=False,
            coupon_error=None,
            has_unavailable=True,
            unavailable_count=1,
            total_quantity=0,
            is_valid=False,
            error_message="Phiên bản sản phẩm không tồn tại hoặc đã ngừng kinh doanh.",
            fingerprint=fp,
            quote_token=make_quote_token(fp),
        )

    product = variant.product
    category = product.category
    is_available = True
    avail_msg = ""

    if not category.is_visible:
        is_available = False
        avail_msg = f'Sản phẩm "{product.name}" thuộc danh mục đang tạm ẩn.'
    elif not product.is_active or product.is_deleted:
        is_available = False
        avail_msg = f'Sản phẩm "{product.name}" đã ngừng kinh doanh hoặc không còn tồn tại.'
    elif not variant.is_active:
        is_available = False
        avail_msg = f'Phiên bản "{variant.sku}" của sản phẩm "{product.name}" đã ngừng kinh doanh.'
    elif variant.price is None or variant.price < Decimal('0.00'):
        is_available = False
        avail_msg = f'Đơn giá của phiên bản "{variant.sku}" không hợp lệ.'
    elif quantity <= 0:
        is_available = False
        avail_msg = 'Số lượng đặt mua phải là số nguyên dương.'
    else:
        inv = getattr(variant, 'inventory', None)
        if inv is None:
            is_available = False
            avail_msg = f'Sản phẩm "{product.name}" ({variant.sku}) chưa có cấu hình kho hàng.'
        else:
            available_stock = inv.physical_sellable - inv.reserved
            if available_stock < quantity:
                is_available = False
                avail_msg = (
                    f'Sản phẩm "{product.name}" ({variant.sku}) không đủ tồn kho khả dụng '
                    f'(yêu cầu: {quantity}, khả dụng: {max(0, available_stock)}).'
                )

    unit_price = variant.price if (variant.price and variant.price >= Decimal('0.00')) else Decimal('0.00')
    subtotal = unit_price * Decimal(max(1, quantity))

    clean_code = str(coupon_code).strip().upper() if coupon_code and str(coupon_code).strip() else None
    discount_total = Decimal('0.00')
    coupon_valid = False
    coupon_error = None

    if clean_code and is_available:
        try:
            coupon_res = evaluate_coupon_for_buy_now(variant, quantity, clean_code, at=eval_at)
            discount_total = coupon_res['discount_amount']
            coupon_valid = True
        except ValidationError as e:
            coupon_valid = False
            coupon_error = e.message if hasattr(e, 'message') else str(e)
            discount_total = Decimal('0.00')

    grand_total = max(Decimal('0.00'), subtotal - discount_total)

    img_url = None
    v_img = variant.images.first()
    if v_img and hasattr(v_img, 'image') and v_img.image:
        img_url = v_img.image.url
    elif product.images.exists():
        p_img = product.images.first()
        if p_img and hasattr(p_img, 'image') and p_img.image:
            img_url = p_img.image.url

    attrs_display = format_variant_attributes_display(variant)

    line = {
        'item_id': f"bn_{variant.id}",
        'variant_id': variant.id,
        'product_name': product.name,
        'attrs_display': attrs_display,
        'sku': variant.sku,
        'image': img_url,
        'quantity': quantity,
        'unit_price': unit_price,
        'unit_price_formatted': format_vnd(unit_price),
        'line_total': subtotal,
        'line_total_formatted': format_vnd(subtotal),
        'is_available': is_available,
        'availability_message': avail_msg,
        'has_price_changed': False,
        'price_change_message': '',
    }

    raw_fp_str = "|".join([
        f"BUY_NOW:v={variant.id}:qty={quantity}:price={unit_price}:avail={is_available}",
        f"subtotal={subtotal}",
        f"coupon={clean_code or ''}",
        f"coupon_valid={coupon_valid}",
        f"discount={discount_total}",
        f"grand_total={grand_total}",
    ])
    fingerprint = hashlib.sha256(raw_fp_str.encode('utf-8')).hexdigest()[:32]
    quote_token = make_quote_token(fingerprint)

    return ServerQuote(
        lines=[line],
        subtotal=subtotal,
        subtotal_formatted=format_vnd(subtotal),
        discount_total=discount_total,
        discount_total_formatted=format_vnd(discount_total),
        grand_total=grand_total,
        grand_total_formatted=format_vnd(grand_total),
        coupon_code=clean_code,
        coupon_valid=coupon_valid,
        coupon_error=coupon_error,
        has_unavailable=not is_available,
        unavailable_count=0 if is_available else 1,
        total_quantity=quantity,
        is_valid=is_available,
        error_message=avail_msg if not is_available else None,
        fingerprint=fingerprint,
        quote_token=quote_token,
    )
