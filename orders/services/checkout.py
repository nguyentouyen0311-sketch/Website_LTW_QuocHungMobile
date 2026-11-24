from decimal import Decimal
import random
import time
from typing import Optional
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, connection, transaction
from django.db.models import F
from django.utils import timezone
from accounts.permissions import is_customer
from cart.models import Cart
from coupons.evaluation import evaluate_coupon_for_cart
from coupons.models import Coupon, CouponUsage, CouponUsageStatus
from inventory.models import Inventory, InventoryReservation, InventoryReservationStatus
from orders.models import Order, OrderItem, OrderStatus, OrderStatusHistory, validate_delivery_phone
from payments.models import Payment, PaymentMethod, PaymentStatus
from .common import ConcurrencyConflict, _is_sqlite_lock_error
from .pricing import _allocate_coupon_discount
from .results import OrderCreationResult


def _resolve_and_validate_cart(customer, cart_id: Optional[int] = None, cart: Optional[Cart] = None) -> Cart:
    if cart is not None and cart_id is not None:
        if cart.id != cart_id:
            err = ValidationError({
                'cart': ValidationError('Đối tượng giỏ hàng và ID giỏ hàng không khớp.', code='cart_mismatch')
            })
            err.code = 'cart_mismatch'
            raise err

    target_cart = None
    if cart_id is not None:
        target_cart = Cart.objects.filter(id=cart_id).first()
    elif cart is not None:
        target_cart = Cart.objects.filter(id=cart.id).first()
    else:
        target_cart = Cart.objects.filter(user=customer).first()

    if target_cart is None or target_cart.user_id != customer.id:
        err = ValidationError({
            'cart': ValidationError(
                'Giỏ hàng không tồn tại hoặc không thuộc sở hữu của khách hàng.',
                code='cart_not_owned'
            )
        })
        err.code = 'cart_not_owned'
        raise err

    return target_cart


def _format_variant_attributes_display(variant) -> str:
    links = list(variant.attribute_values.select_related('attribute', 'attribute_value').all())
    if links:
        links.sort(key=lambda x: (x.attribute.name, x.id))
        return ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
    return "Phiên bản mặc định"


def _validate_and_resolve_order_replay(
    existing_order: Order,
    customer,
    coupon_code: Optional[str],
    expected_method: PaymentMethod = PaymentMethod.COD,
) -> OrderCreationResult:
    if existing_order.customer_id != customer.id:
        err = ValidationError({
            'order_code': ValidationError(
                'Mã đơn hàng đã được sử dụng bởi một khách hàng khác.',
                code='order_code_conflict'
            )
        })
        err.code = 'order_code_conflict'
        raise err

    has_coupon_usage = CouponUsage.objects.filter(order=existing_order).exists()
    if coupon_code is None:
        if has_coupon_usage:
            err = ValidationError({
                'order_code': ValidationError(
                    'Mã đơn hàng đã tồn tại với thông tin voucher khác.',
                    code='order_code_immutable'
                )
            })
            err.code = 'order_code_immutable'
            raise err
    else:
        if not has_coupon_usage:
            err = ValidationError({
                'order_code': ValidationError(
                    'Không thể thay đổi hoặc gán mới mã giảm giá cho đơn hàng đã tồn tại.',
                    code='order_code_immutable'
                )
            })
            err.code = 'order_code_immutable'
            raise err

    existing_payment = Payment.objects.filter(order=existing_order).first()
    if existing_payment is None:
        err = ValidationError({
            'payment': ValidationError(
                'Đơn hàng lịch sử thiếu bản ghi thanh toán phục vụ replay.',
                code='payment_missing_for_replay'
            )
        })
        err.code = 'payment_missing_for_replay'
        raise err

    if existing_payment.method != expected_method:
        err = ValidationError({
            'payment_method': ValidationError(
                f'Phương thức thanh toán của đơn hàng không khớp {expected_method}.',
                code='payment_method_conflict'
            )
        })
        err.code = 'payment_method_conflict'
        raise err

    return OrderCreationResult(order=existing_order, is_replay=True)


def _create_cod_order_core(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    coupon_code: Optional[str] = None,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if customer is None:
        err = ValidationError({'customer': ValidationError('Khách hàng không được để trống.', code='customer_required')})
        err.code = 'customer_required'
        raise err

    if not getattr(customer, 'is_authenticated', False) or not getattr(customer, 'is_active', True):
        raise PermissionDenied('Tài khoản khách hàng chưa được xác thực hoặc bị vô hiệu hóa.')

    field_errors = {}

    clean_order_code = str(order_code).strip() if order_code is not None else ''
    if not clean_order_code:
        field_errors['order_code'] = ValidationError('Mã đơn hàng không được để trống.', code='order_code_required')
    elif len(clean_order_code) > 30:
        field_errors['order_code'] = ValidationError('Mã đơn hàng không được vượt quá 30 ký tự.', code='order_code_max_length')

    clean_name = str(recipient_name).strip() if recipient_name is not None else ''
    if not clean_name:
        field_errors['recipient_name'] = ValidationError('Tên người nhận không được để trống.', code='recipient_name_required')
    elif len(clean_name) > 120:
        field_errors['recipient_name'] = ValidationError('Tên người nhận không được vượt quá 120 ký tự.', code='recipient_name_max_length')

    clean_phone = ''
    if not recipient_phone or not str(recipient_phone).strip():
        field_errors['recipient_phone'] = ValidationError('Số điện thoại người nhận không được để trống.', code='recipient_phone_required')
    else:
        try:
            clean_phone = validate_delivery_phone(recipient_phone)
        except ValidationError as e:
            code = getattr(e, 'code', None) or 'invalid_phone'
            field_errors['recipient_phone'] = ValidationError(e.message, code=code)

    clean_address = str(shipping_address).strip() if shipping_address is not None else ''
    if not clean_address:
        field_errors['shipping_address'] = ValidationError('Địa chỉ giao hàng không được để trống.', code='shipping_address_required')

    if field_errors:
        err = ValidationError(field_errors)
        raise err

    in_nested_tx = connection.in_atomic_block
    effective_retries = 1 if in_nested_tx else max(1, int(max_retries))

    for attempt in range(effective_retries):
        try:
            with transaction.atomic():
                if not is_customer(customer):
                    raise PermissionDenied('Tài khoản không có vai trò Khách hàng (Customer) để đặt hàng.')

                target_cart = _resolve_and_validate_cart(customer, cart_id=cart_id, cart=cart)

                existing_in_tx = Order.objects.filter(order_code=clean_order_code).first()
                if existing_in_tx:
                    return _validate_and_resolve_order_replay(
                        existing_order=existing_in_tx,
                        customer=customer,
                        coupon_code=coupon_code,
                        expected_method=PaymentMethod.COD,
                    )


                cart_items = list(
                    target_cart.items.select_related(
                        'variant__product__category',
                        'variant__inventory'
                    ).all()
                )

                if not cart_items:
                    err = ValidationError({
                        'cart': ValidationError('Giỏ hàng trống, không thể tạo đơn hàng.', code='cart_empty')
                    })
                    err.code = 'cart_empty'
                    raise err

                cart_items.sort(key=lambda item: item.variant_id)

                pricing_instant = timezone.now()
                subtotal = Decimal('0.00')
                evaluated_lines = []

                for item in cart_items:
                    variant = item.variant
                    product = variant.product
                    category = product.category

                    if not category.is_visible:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Sản phẩm "{product.name}" thuộc danh mục đang tạm ẩn, không thể đặt hàng.',
                                code='category_hidden'
                            )
                        })
                        err.code = 'category_hidden'
                        raise err

                    if not product.is_active or product.is_deleted:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Sản phẩm "{product.name}" đã ngừng kinh doanh hoặc không còn tồn tại.',
                                code='product_inactive'
                            )
                        })
                        err.code = 'product_inactive'
                        raise err

                    if not variant.is_active:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Phiên bản "{variant.sku}" của sản phẩm "{product.name}" đã ngừng kinh doanh.',
                                code='variant_inactive'
                            )
                        })
                        err.code = 'variant_inactive'
                        raise err

                    if variant.price is None or variant.price < Decimal('0.00'):
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Đơn giá của phiên bản "{variant.sku}" không hợp lệ.',
                                code='invalid_price'
                            )
                        })
                        err.code = 'invalid_price'
                        raise err

                    if item.quantity <= 0:
                        err = ValidationError({
                            'cart': ValidationError(
                                f'Số lượng của phiên bản "{variant.sku}" không hợp lệ.',
                                code='invalid_quantity'
                            )
                        })
                        err.code = 'invalid_quantity'
                        raise err

                    inv = getattr(variant, 'inventory', None)
                    if inv is None:
                        err = ValidationError({
                            'inventory': ValidationError(
                                f'Sản phẩm "{product.name}" ({variant.sku}) chưa có cấu hình kho hàng.',
                                code='inventory_missing'
                            )
                        })
                        err.code = 'inventory_missing'
                        raise err

                    available_stock = inv.physical_sellable - inv.reserved
                    if available_stock < item.quantity:
                        err = ValidationError({
                            'inventory': ValidationError(
                                f'Sản phẩm "{product.name}" ({variant.sku}) không đủ tồn kho khả dụng '
                                f'(yêu cầu: {item.quantity}, khả dụng: {max(0, available_stock)}).',
                                code='insufficient_stock'
                            )
                        })
                        err.code = 'insufficient_stock'
                        raise err

                    current_unit_price = variant.price
                    line_total = current_unit_price * Decimal(item.quantity)
                    subtotal += line_total

                    evaluated_lines.append({
                        'item': item,
                        'variant': variant,
                        'product': product,
                        'inv': inv,
                        'unit_price': current_unit_price,
                        'line_total': line_total,
                    })

                coupon = None
                total_discount = Decimal('0.00')
                allocated_discounts = {i: Decimal('0.00') for i in range(len(evaluated_lines))}

                if coupon_code is not None:
                    eval_result = evaluate_coupon_for_cart(target_cart, coupon_code, at=pricing_instant)
                    total_discount = eval_result['discount_amount']

                    coupon = Coupon.objects.filter(id=eval_result['coupon_id']).first()
                    if coupon is None:
                        err = ValidationError({
                            'coupon_code': ValidationError(
                                'Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.',
                                code='coupon_invalid'
                            )
                        })
                        err.code = 'coupon_invalid'
                        raise err

                    rows_updated = Coupon.objects.filter(
                        id=coupon.id,
                        is_active=True,
                        starts_at__lte=pricing_instant,
                        ends_at__gt=pricing_instant,
                        used_count=coupon.used_count,
                        reserved_count=coupon.reserved_count,
                    ).filter(
                        used_count__lt=F('max_uses') - F('reserved_count')
                    ).update(
                        used_count=F('used_count') + 1
                    )

                    if rows_updated == 0:
                        refreshed = Coupon.objects.filter(id=coupon.id).first()
                        if not refreshed or not refreshed.is_active:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.',
                                    code='coupon_invalid'
                                )
                            })
                            err.code = 'coupon_invalid'
                            raise err
                        if pricing_instant < refreshed.starts_at:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá chưa đến thời gian áp dụng.',
                                    code='coupon_not_started'
                                )
                            })
                            err.code = 'coupon_not_started'
                            raise err
                        if pricing_instant >= refreshed.ends_at:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá đã hết hạn sử dụng.',
                                    code='coupon_expired'
                                )
                            })
                            err.code = 'coupon_expired'
                            raise err
                        if refreshed.used_count + refreshed.reserved_count >= refreshed.max_uses:
                            err = ValidationError({
                                'coupon_code': ValidationError(
                                    'Mã giảm giá đã hết lượt sử dụng.',
                                    code='coupon_exhausted'
                                )
                            })
                            err.code = 'coupon_exhausted'
                            raise err
                        raise ConcurrencyConflict(
                            f'Xung đột cập nhật hạn mức mã giảm giá {coupon.code}.'
                        )

                    eligible_product_ids = None
                    if not coupon.applies_to_all:
                        eligible_product_ids = set(
                            coupon.coupon_products.values_list('product_id', flat=True)
                        )
                    allocated_discounts = _allocate_coupon_discount(
                        evaluated_lines=evaluated_lines,
                        coupon=coupon,
                        total_discount=total_discount,
                        eligible_product_ids=eligible_product_ids,
                    )

                grand_total = subtotal - total_discount
                if grand_total < Decimal('0.00'):
                    grand_total = Decimal('0.00')

                for line in evaluated_lines:
                    inv = line['inv']
                    qty = line['item'].quantity

                    rows_updated = Inventory.objects.filter(
                        id=inv.id,
                        physical_sellable__gte=F('reserved') + qty
                    ).update(
                        reserved=F('reserved') + qty,
                        updated_at=timezone.now(),
                    )

                    if rows_updated == 0:
                        cur_inv = (
                            Inventory.objects
                            .filter(id=inv.id)
                            .values('physical_sellable', 'reserved')
                            .first()
                        )
                        if cur_inv and (cur_inv['physical_sellable'] - cur_inv['reserved']) < qty:
                            err = ValidationError({
                                'inventory': ValidationError(
                                    f'Sản phẩm "{line["product"].name}" ({line["variant"].sku}) không đủ tồn kho khả dụng.',
                                    code='insufficient_stock'
                                )
                            })
                            err.code = 'insufficient_stock'
                            raise err
                        raise ConcurrencyConflict(
                            f'Xung đột cập nhật tồn kho đồng thời cho biến thể {line["variant"].sku}.'
                        )

                initial_order_status = OrderStatus.PENDING_CONFIRMATION
                order = Order(
                    order_code=clean_order_code,
                    customer=customer,
                    recipient_name=clean_name,
                    recipient_phone=clean_phone,
                    shipping_address=clean_address,
                    order_status=initial_order_status,
                    subtotal=subtotal,
                    discount_total=total_discount,
                    grand_total=grand_total,
                )
                order.full_clean()
                order.save()

                for i, line in enumerate(evaluated_lines):
                    item = line['item']
                    variant = line['variant']
                    product = line['product']
                    unit_price = line['unit_price']
                    line_gross = line['line_total']
                    line_discount = allocated_discounts[i]
                    line_net = line_gross - line_discount
                    if line_net < Decimal('0.00'):
                        line_net = Decimal('0.00')

                    order_item = OrderItem(
                        order=order,
                        variant=variant,
                        product_name_snapshot=product.name[:150],
                        variant_snapshot=_format_variant_attributes_display(variant)[:255],
                        sku_snapshot=variant.sku[:60],
                        quantity=item.quantity,
                        unit_price=unit_price,
                        discount_amount=line_discount,
                        line_total=line_net,
                    )
                    order_item.full_clean()
                    order_item.save()

                    reservation = InventoryReservation(
                        variant=variant,
                        order_item=order_item,
                        quantity=item.quantity,
                        status=InventoryReservationStatus.ACTIVE,
                        finished_at=None,
                    )
                    reservation.full_clean()
                    reservation.save()

                if coupon is not None:
                    c_status = CouponUsageStatus.USED
                    c_finalized = pricing_instant

                    coupon_usage = CouponUsage(
                        coupon=coupon,
                        order=order,
                        discount_amount=total_discount,
                        status=c_status,
                        finalized_at=c_finalized,
                    )
                    coupon_usage.full_clean()
                    coupon_usage.save()

                hist_status = OrderStatus.PENDING_CONFIRMATION
                hist_reason = 'Khách hàng tạo đơn hàng COD từ giỏ hàng.'

                history = OrderStatusHistory(
                    order=order,
                    old_status='',
                    new_status=hist_status,
                    changed_by=customer,
                    reason=hist_reason,
                    changed_at=timezone.now(),
                )
                history.full_clean()
                history.save()

                payment = Payment(
                    order=order,
                    method=PaymentMethod.COD,
                    status=PaymentStatus.UNPAID,
                    expected_amount=order.grand_total,
                )
                payment.full_clean()
                payment.save()

                purchased_item_ids = [line['item'].id for line in evaluated_lines]
                target_cart.items.filter(id__in=purchased_item_ids).delete()
                target_cart.save(update_fields=['updated_at'])

                return OrderCreationResult(order=order, is_replay=False)

        except ConcurrencyConflict:
            if in_nested_tx or attempt == effective_retries - 1:
                raise ValidationError(
                    'Hệ thống đang có nhiều giao dịch đặt hàng đồng thời. Vui lòng thử lại sau giây lát.',
                    code='concurrency_conflict'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
            continue
        except OperationalError as exc:
            if _is_sqlite_lock_error(exc):
                if in_nested_tx or attempt == effective_retries - 1:
                    raise ValidationError(
                        'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.',
                        code='database_busy'
                    )
                time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                continue
            raise
        except IntegrityError:
            try:
                _resolve_and_validate_cart(customer, cart_id=cart_id, cart=cart)
                existing = Order.objects.filter(order_code=clean_order_code).first()
                if existing:
                    return _validate_and_resolve_order_replay(
                        existing_order=existing,
                        customer=customer,
                        coupon_code=coupon_code,
                        expected_method=PaymentMethod.COD,
                    )
            except OperationalError as recov_exc:
                if _is_sqlite_lock_error(recov_exc):
                    if in_nested_tx or attempt == effective_retries - 1:
                        raise ValidationError(
                            'Hệ thống đang bận xử lý giao dịch. Vui lòng thử lại sau giây lát.',
                            code='database_busy'
                        )
                    time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.04))
                    continue
                raise
            raise

    raise ValidationError('Không thể hoàn tất tạo đơn hàng do vượt quá số lần thử lại.', code='retry_exhausted')


def create_cod_order_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    voucher_code: Optional[str] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if voucher_code is not None and str(voucher_code).strip() != '':
        err = ValidationError({
            'voucher_code': ValidationError(
                'Đơn hàng COD trong phạm vi này chưa hỗ trợ áp dụng voucher / mã giảm giá.',
                code='voucher_unsupported'
            )
        })
        err.code = 'voucher_unsupported'
        raise err

    return _create_cod_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        coupon_code=None,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )


def create_cod_order_with_coupon_from_cart(
    customer,
    recipient_name: str,
    recipient_phone: str,
    shipping_address: str,
    order_code: str,
    coupon_code: str,
    cart_id: Optional[int] = None,
    cart: Optional[Cart] = None,
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> OrderCreationResult:
    if coupon_code is None or not isinstance(coupon_code, str) or not coupon_code.strip():
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không được để trống.', code='coupon_required')
        })
        err.code = 'coupon_required'
        raise err

    clean_coupon_code = coupon_code.strip().upper()
    if len(clean_coupon_code) > 40:
        err = ValidationError({
            'coupon_code': ValidationError('Mã giảm giá không hợp lệ hoặc đã bị vô hiệu hóa.', code='coupon_invalid')
        })
        err.code = 'coupon_invalid'
        raise err

    return _create_cod_order_core(
        customer=customer,
        recipient_name=recipient_name,
        recipient_phone=recipient_phone,
        shipping_address=shipping_address,
        order_code=order_code,
        coupon_code=clean_coupon_code,
        cart_id=cart_id,
        cart=cart,
        max_retries=max_retries,
        initial_retry_delay=initial_retry_delay,
    )
