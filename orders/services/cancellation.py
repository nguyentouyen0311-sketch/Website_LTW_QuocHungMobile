from decimal import Decimal
import random
import time
from typing import Optional
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from accounts.permissions import is_customer
from coupons.models import CouponUsage, CouponUsageStatus
from inventory.models import (
    Inventory,
    InventoryReservation,
    InventoryReservationStatus,
    InventoryTransaction,
)
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderCancellation,
    OrderItem,
    OrderStatus,
    OrderStatusHistory,
)
from payments.models import Payment, PaymentMethod, PaymentStatus, CODCollection, Refund
from .common import ConcurrencyConflict, _is_sqlite_lock_error
from .results import CancelOrderResult


def _validate_consistent_cancelled_outcome(
    order: Order,
    payment: Payment,
    items: list[OrderItem],
    user,
) -> CancelOrderResult:
    if order.order_status != OrderStatus.CANCELLED:
        raise ValidationError(
            f"Trạng thái đơn hàng '{order.get_order_status_display()}' không phải là Đã hủy (CANCELLED).",
            code='inconsistent_cancelled_outcome'
        )

    if user is None or not getattr(user, 'is_authenticated', False) or user.id != order.customer_id:
        raise ValidationError(
            "Người dùng thực hiện không phải là khách hàng sở hữu đơn hàng.",
            code='inconsistent_cancelled_outcome'
        )

    cancellation = getattr(order, 'cancellation', None)
    if not cancellation:
        cancellation = OrderCancellation.objects.filter(order=order).first()
    if not cancellation:
        raise ValidationError(
            "Đơn hàng ở trạng thái CANCELLED nhưng thiếu bản ghi hủy đơn (OrderCancellation).",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.requested_by_id != order.customer_id:
        raise ValidationError(
            "Người yêu cầu hủy trong bản ghi OrderCancellation không khớp với khách hàng sở hữu đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.reason_code not in CancellationReasonCode.values:
        raise ValidationError(
            f"Mã lý do hủy '{cancellation.reason_code}' trong bản ghi OrderCancellation không hợp lệ.",
            code='inconsistent_cancelled_outcome'
        )

    cleaned_desc = str(cancellation.description or '').strip()
    if cancellation.reason_code == CancellationReasonCode.OTHER and not cleaned_desc:
        raise ValidationError(
            "Bản ghi OrderCancellation với lý do 'Khác' nhưng thiếu mô tả chi tiết.",
            code='inconsistent_cancelled_outcome'
        )

    if cleaned_desc and len(cleaned_desc) > 500:
        raise ValidationError(
            "Mô tả chi tiết trong OrderCancellation vượt quá 500 ký tự.",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.cancelled_at is None:
        raise ValidationError(
            "Bản ghi OrderCancellation thiếu thời điểm hủy (cancelled_at is NULL).",
            code='inconsistent_cancelled_outcome'
        )

    if cancellation.reservation_released_at is None:
        raise ValidationError(
            "Bản ghi OrderCancellation chưa ghi nhận thời điểm giải phóng tồn kho (reservation_released_at is NULL).",
            code='inconsistent_cancelled_outcome'
        )

    cancel_histories = list(OrderStatusHistory.objects.filter(
        order=order,
        new_status=OrderStatus.CANCELLED,
    ))
    if len(cancel_histories) != 1:
        raise ValidationError(
            f"Đơn hàng phải có chính xác 01 bản ghi lịch sử chuyển sang CANCELLED (tìm thấy {len(cancel_histories)} bản ghi).",
            code='inconsistent_cancelled_outcome'
        )
    cancel_history = cancel_histories[0]

    if cancel_history.changed_by_id != order.customer_id:
        raise ValidationError(
            "Người thực hiện trong lịch sử hủy không khớp với khách hàng sở hữu đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if cancel_history.old_status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
        raise ValidationError(
            f"Trạng thái trước khi hủy trong lịch sử ('{cancel_history.old_status}') không hợp lệ cho đơn hủy trước xuất kho.",
            code='inconsistent_cancelled_outcome'
        )

    reason_display = dict(CancellationReasonCode.choices).get(cancellation.reason_code, cancellation.reason_code)
    expected_history_reason = f"Khách hàng hủy đơn: {reason_display}"
    if cancellation.description:
        expected_history_reason += f" - {cancellation.description}"

    if cancel_history.reason != expected_history_reason:
        raise ValidationError(
            f"Nội dung lý do trong lịch sử hủy không khớp với hồ sơ hủy canonical ('{cancel_history.reason}' != '{expected_history_reason}').",
            code='inconsistent_cancelled_outcome'
        )

    shipping_lifecycle_statuses = [
        OrderStatus.SHIPPING,
        OrderStatus.DELIVERED,
        OrderStatus.DELIVERY_FAILED,
        OrderStatus.RETURNING_TO_WAREHOUSE,
        OrderStatus.FAILED_CLOSED,
    ]
    if OrderStatusHistory.objects.filter(order=order, new_status__in=shipping_lifecycle_statuses).exists():
        raise ValidationError(
            "Đơn hàng tồn tại lịch sử vận chuyển hoặc giao hàng, mâu thuẫn với quy trình hủy trước xuất kho.",
            code='inconsistent_cancelled_outcome'
        )

    now = timezone.now()
    event_time = cancellation.cancelled_at
    rel_time = cancellation.reservation_released_at
    changed_time = cancel_history.changed_at

    if rel_time != event_time:
        raise ValidationError(
            "Thời điểm giải phóng giữ kho trong OrderCancellation không đồng nhất với thời điểm hủy.",
            code='inconsistent_cancelled_outcome'
        )

    if changed_time != event_time:
        raise ValidationError(
            "Thời điểm ghi nhận trong lịch sử hủy không đồng nhất với thời điểm hủy đơn.",
            code='inconsistent_cancelled_outcome'
        )

    if event_time > now:
        raise ValidationError(
            "Thời điểm hủy đơn ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if rel_time > now:
        raise ValidationError(
            "Thời điểm giải phóng giữ kho ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if changed_time > now:
        raise ValidationError(
            "Thời điểm ghi nhận trong lịch sử hủy ở tương lai so với thời gian hiện tại của hệ thống.",
            code='inconsistent_cancelled_outcome'
        )

    if order.created_at:
        if event_time < order.created_at:
            raise ValidationError(
                "Thời điểm hủy đơn xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if rel_time < order.created_at:
            raise ValidationError(
                "Thời điểm giải phóng giữ kho xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if changed_time < order.created_at:
            raise ValidationError(
                "Thời điểm ghi nhận trong lịch sử hủy xảy ra trước thời điểm tạo đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if order.created_at > now:
            raise ValidationError(
                "Thời điểm tạo đơn hàng ở tương lai so với thời gian hiện tại của hệ thống.",
                code='inconsistent_cancelled_outcome'
            )

    if payment.method != PaymentMethod.COD or payment.status != PaymentStatus.UNPAID:
        raise ValidationError(
            f"Thanh toán của đơn hàng hủy không nhất quán (phương thức: {payment.method}, trạng thái: {payment.status}).",
            code='inconsistent_cancelled_outcome'
        )
    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            "Số tiền thanh toán dự kiến không khớp với tổng tiền đơn hàng.",
            code='inconsistent_cancelled_outcome'
        )
    if CODCollection.objects.filter(payment=payment).exists():
        raise ValidationError(
            "Đơn hàng có chứng từ thu tiền COD bất thường.",
            code='inconsistent_cancelled_outcome'
        )
    if Refund.objects.filter(order=order).exists():
        raise ValidationError(
            "Đơn hàng có phát sinh bản ghi hoàn tiền (Refund) bất thường.",
            code='inconsistent_cancelled_outcome'
        )

    if not items:
        raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='inconsistent_cancelled_outcome')

    for item in items:
        res_list = list(InventoryReservation.objects.filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dòng sản phẩm '{item.product_name_snapshot}' có số lượng bản ghi giữ kho bất thường ({len(res_list)}).",
                code='inconsistent_cancelled_outcome'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.RELEASED:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' chưa được giải phóng (trạng thái: {res.status}).",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at is None:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' ở trạng thái RELEASED nhưng finished_at là NULL.",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at != event_time:
            raise ValidationError(
                f"Thời điểm finished_at của dòng giữ kho ({res.finished_at}) không đồng nhất với thời điểm hủy ({event_time}).",
                code='inconsistent_cancelled_outcome'
            )
        if res.finished_at > now:
            raise ValidationError(
                f"Thời điểm finished_at của dòng giữ kho ({res.finished_at}) ở tương lai so với thời gian hiện tại.",
                code='inconsistent_cancelled_outcome'
            )
        if res.created_at:
            if res.created_at > now:
                raise ValidationError(
                    f"Thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}' ở tương lai.",
                    code='inconsistent_cancelled_outcome'
                )
            if res.finished_at < res.created_at:
                raise ValidationError(
                    f"Thời điểm hủy đơn xảy ra trước thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}'.",
                    code='inconsistent_cancelled_outcome'
                )
            if order.created_at and res.created_at < order.created_at:
                raise ValidationError(
                    f"Thời điểm tạo giữ kho trước thời điểm tạo đơn hàng.",
                    code='inconsistent_cancelled_outcome'
                )
        if res.variant_id != item.variant_id:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' lệch biến thể.",
                code='inconsistent_cancelled_outcome'
            )
        if res.quantity != item.quantity:
            raise ValidationError(
                f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' lệch số lượng.",
                code='inconsistent_cancelled_outcome'
            )

    if InventoryTransaction.objects.filter(order=order).exists():
        raise ValidationError(
            "Đơn hàng đã phát sinh bản ghi biến động xuất kho (InventoryTransaction), không hợp lệ cho đơn hủy trước xuất.",
            code='inconsistent_cancelled_outcome'
        )
    if order.delivered_at is not None or order.failed_delivery_at is not None:
        raise ValidationError(
            "Đơn hàng có dấu mốc thời gian giao hàng, không hợp lệ cho đơn hủy trước xuất.",
            code='inconsistent_cancelled_outcome'
        )

    coupon_usage = getattr(order, 'coupon_usage', None)
    if not coupon_usage:
        coupon_usage = CouponUsage.objects.filter(order=order).first()

    if coupon_usage:
        if coupon_usage.status != CouponUsageStatus.USED:
            raise ValidationError(
                f"Bản ghi coupon của đơn COD phải giữ trạng thái USED. Hiện tại: {coupon_usage.status}.",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.discount_amount != order.discount_total:
            raise ValidationError(
                f"Mức giảm giá của coupon usage ({coupon_usage.discount_amount}) không khớp với discount_total của đơn hàng ({order.discount_total}).",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.order_id != order.id:
            raise ValidationError(
                "Bản ghi coupon usage không khớp với đơn hàng.",
                code='inconsistent_cancelled_outcome'
            )
        if coupon_usage.created_at:
            if coupon_usage.created_at > now:
                raise ValidationError(
                    "Thời điểm tạo coupon usage ở tương lai.",
                    code='inconsistent_cancelled_outcome'
                )
            if order.created_at and coupon_usage.created_at < order.created_at:
                raise ValidationError(
                    "Thời điểm tạo coupon usage trước thời điểm tạo đơn hàng.",
                    code='inconsistent_cancelled_outcome'
                )
    else:
        if order.discount_total > Decimal('0.00'):
            raise ValidationError(
                "Đơn hàng có giảm giá nhưng thiếu bản ghi sử dụng coupon.",
                code='inconsistent_cancelled_outcome'
            )

    return CancelOrderResult(
        order=order,
        cancellation=cancellation,
        history_entry=cancel_history,
        is_replay=True,
    )


def cancel_customer_cod_order(
    order_id: int,
    customer,
    reason_code: str,
    description: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.05,
) -> CancelOrderResult:
    if not (customer and customer.is_authenticated and customer.is_active):
        raise PermissionDenied("Yêu cầu đăng nhập tài khoản Khách hàng để thực hiện hủy đơn.")

    if not is_customer(customer):
        raise PermissionDenied("Bạn không có quyền thực hiện chức năng này. Chỉ tài khoản Khách hàng mới có quyền hủy đơn hàng của mình.")

    if not reason_code or reason_code not in CancellationReasonCode.values:
        raise ValidationError(
            f"Lý do hủy đơn không hợp lệ: '{reason_code}'.",
            code='invalid_cancellation_reason'
        )

    cleaned_desc = str(description or '').strip()
    if reason_code == CancellationReasonCode.OTHER:
        if not cleaned_desc:
            raise ValidationError(
                {'description': 'Vui lòng nhập mô tả chi tiết khi chọn lý do "Khác".'},
                code='missing_other_description'
            )

    if cleaned_desc and len(cleaned_desc) > 500:
        raise ValidationError(
            {'description': 'Mô tả chi tiết không được vượt quá 500 ký tự.'},
            code='description_too_long'
        )

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                if order.customer_id != customer.id:
                    raise PermissionDenied("Bạn không có quyền hủy đơn hàng của khách hàng khác.")

                event_now = timezone.now()
                if order.created_at and order.created_at > event_now:
                    raise ValidationError(
                        "Thời điểm tạo đơn hàng ở tương lai, không thể xử lý hủy đơn.",
                        code='invalid_order_chronology'
                    )

                payment = Payment.objects.filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                items = list(order.items.all().order_by('id'))

                if order.order_status == OrderStatus.CANCELLED:
                    return _validate_consistent_cancelled_outcome(order, payment, items, customer)

                if order.order_status not in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể hủy trước xuất kho.",
                        code='ineligible_order_status'
                    )

                if payment.method != PaymentMethod.COD:
                    raise ValidationError(
                        f"Chức năng hủy trực tiếp chỉ áp dụng cho đơn hàng COD. Phương thức hiện tại: {payment.get_method_display()}.",
                        code='ineligible_payment_method'
                    )

                if payment.status != PaymentStatus.UNPAID:
                    raise ValidationError(
                        f"Đơn hàng COD chỉ được hủy khi chưa thanh toán (UNPAID). Trạng thái hiện tại: {payment.get_status_display()}.",
                        code='invalid_cod_payment_status'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ).",
                        code='amount_mismatch'
                    )


                if CODCollection.objects.filter(payment=payment).exists():
                    raise ValidationError(
                        "Đơn hàng đã có chứng từ thu tiền COD, không thể hủy.",
                        code='existing_cod_collection'
                    )

                if Refund.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã phát sinh bản ghi hoàn tiền, không thể hủy.",
                        code='existing_refund'
                    )

                if order.delivered_at is not None or order.failed_delivery_at is not None:
                    raise ValidationError(
                        "Đơn hàng đã có dấu mốc thời gian giao hàng, không thuộc diện hủy trước xuất kho.",
                        code='already_delivered'
                    )

                if InventoryTransaction.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã phát sinh bản ghi xuất kho (SHIP), không thể hủy trước xuất kho.",
                        code='existing_stock_issue'
                    )

                shipping_lifecycle_statuses = [
                    OrderStatus.SHIPPING,
                    OrderStatus.DELIVERED,
                    OrderStatus.DELIVERY_FAILED,
                    OrderStatus.RETURNING_TO_WAREHOUSE,
                    OrderStatus.FAILED_CLOSED,
                ]
                if OrderStatusHistory.objects.filter(order=order, new_status__in=shipping_lifecycle_statuses).exists():
                    raise ValidationError(
                        "Đơn hàng đã từng bước qua quy trình vận chuyển, không thể hủy trước xuất kho.",
                        code='invalid_lifecycle_history'
                    )

                if OrderCancellation.objects.filter(order=order).exists():
                    raise ValidationError(
                        "Đơn hàng đã tồn tại bản ghi hủy đơn mâu thuẫn với trạng thái chưa hủy hiện thời.",
                        code='contradictory_cancellation_record'
                    )

                if OrderStatusHistory.objects.filter(order=order, new_status=OrderStatus.CANCELLED).exists():
                    raise ValidationError(
                        "Đơn hàng đã tồn tại lịch sử trạng thái CANCELLED mâu thuẫn với trạng thái chưa hủy hiện thời.",
                        code='contradictory_cancellation_history'
                    )

                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                variant_reservations = {}
                for item in items:
                    res_list = list(InventoryReservation.objects.filter(order_item=item))
                    if len(res_list) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có số dòng giữ kho không hợp lệ ({len(res_list)} dòng). Cần rà soát đối soát vận hành.",
                            code='invalid_reservation_state'
                        )
                    res = res_list[0]
                    if res.status != InventoryReservationStatus.ACTIVE:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' không ở trạng thái ACTIVE (hiện tại: {res.status}).",
                            code='invalid_reservation_state'
                        )
                    if res.finished_at is not None:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' có finished_at không hợp lệ khi còn ACTIVE.",
                            code='invalid_reservation_state'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ kho của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ kho ({res.quantity}) không khớp với số lượng đặt ({item.quantity}) của '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )
                    if res.created_at:
                        if res.created_at > event_now:
                            raise ValidationError(
                                f"Thời điểm tạo giữ kho của sản phẩm '{item.product_name_snapshot}' ở tương lai.",
                                code='invalid_reservation_chronology'
                            )
                        if order.created_at and res.created_at < order.created_at:
                            raise ValidationError(
                                f"Thời điểm tạo giữ kho trước thời điểm tạo đơn hàng.",
                                code='invalid_reservation_chronology'
                            )
                    variant_reservations.setdefault(item.variant_id, []).append(res)

                coupon_usage = getattr(order, 'coupon_usage', None)
                if not coupon_usage:
                    coupon_usage = CouponUsage.objects.filter(order=order).first()

                if coupon_usage:
                    if coupon_usage.status != CouponUsageStatus.USED:
                        raise ValidationError(
                            f"Bản ghi coupon của đơn COD phải ở trạng thái USED. Hiện tại: {coupon_usage.status}.",
                            code='invalid_coupon_usage_status'
                        )
                    if coupon_usage.discount_amount != order.discount_total:
                        raise ValidationError(
                            f"Mức giảm giá của coupon usage ({coupon_usage.discount_amount}) không khớp với discount_total của đơn hàng ({order.discount_total}).",
                            code='inconsistent_coupon_discount'
                        )
                    if coupon_usage.order_id != order.id:
                        raise ValidationError(
                            "Bản ghi coupon usage không khớp với đơn hàng.",
                            code='inconsistent_coupon_owner'
                        )
                    if coupon_usage.created_at:
                        if coupon_usage.created_at > event_now:
                            raise ValidationError(
                                "Thời điểm tạo coupon usage ở tương lai.",
                                code='invalid_coupon_chronology'
                            )
                        if order.created_at and coupon_usage.created_at < order.created_at:
                            raise ValidationError(
                                "Thời điểm tạo coupon usage trước thời điểm tạo đơn hàng.",
                                code='invalid_coupon_chronology'
                            )
                else:
                    if order.discount_total > Decimal('0.00'):
                        raise ValidationError(
                            "Đơn hàng có giảm giá nhưng thiếu bản ghi sử dụng coupon.",
                            code='missing_coupon_usage'
                        )

                sorted_variant_ids = sorted(variant_reservations.keys())

                for vid in sorted_variant_ids:
                    tot_qty = sum(r.quantity for r in variant_reservations[vid])
                    inv = Inventory.objects.filter(variant_id=vid).first()
                    if not inv:
                        raise ValidationError(
                            f"Không tìm thấy bản ghi tồn kho cho biến thể ID {vid}.",
                            code='missing_inventory'
                        )
                    if inv.reserved < tot_qty:
                        raise ValidationError(
                            f"Tồn đã giữ của biến thể SKU '{inv.variant.sku}' ({inv.reserved}) không đủ để giải phóng ({tot_qty}).",
                            code='inconsistent_inventory'
                        )

                old_status = order.order_status

                rows_order = Order.objects.filter(
                    pk=order.pk,
                    order_status=old_status,
                ).update(order_status=OrderStatus.CANCELLED)

                if rows_order != 1:
                    raise ConcurrencyConflict("Xung đột trạng thái đơn hàng khi thực hiện hủy đơn.")

                for vid in sorted_variant_ids:
                    tot_qty = sum(r.quantity for r in variant_reservations[vid])
                    inv = Inventory.objects.get(variant_id=vid)
                    rows_inv = Inventory.objects.filter(
                        pk=inv.pk,
                        reserved=inv.reserved,
                        reserved__gte=tot_qty,
                    ).update(
                        reserved=F('reserved') - tot_qty,
                        updated_at=event_now,
                    )
                    if rows_inv != 1:
                        raise ConcurrencyConflict(f"Xung đột tồn kho khi giải phóng giữ chỗ biến thể SKU '{inv.variant.sku}'.")

                for vid in sorted_variant_ids:
                    for res in variant_reservations[vid]:
                        rows_res = InventoryReservation.objects.filter(
                            pk=res.pk,
                            status=InventoryReservationStatus.ACTIVE,
                            finished_at__isnull=True,
                        ).update(
                            status=InventoryReservationStatus.RELEASED,
                            finished_at=event_now,
                        )
                        if rows_res != 1:
                            raise ConcurrencyConflict(f"Xung đột giữ kho khi giải phóng dòng ID {res.pk}.")

                cancellation = OrderCancellation.objects.create(
                    order=order,
                    reason_code=reason_code,
                    description=cleaned_desc if cleaned_desc else None,
                    requested_by=customer,
                    cancelled_at=event_now,
                    reservation_released_at=event_now,
                )

                reason_display = dict(CancellationReasonCode.choices).get(reason_code, reason_code)
                history_reason = f"Khách hàng hủy đơn: {reason_display}"
                if cleaned_desc:
                    history_reason += f" - {cleaned_desc}"

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=old_status,
                    new_status=OrderStatus.CANCELLED,
                    changed_by=customer,
                    reason=history_reason,
                    changed_at=event_now,
                )

                order.refresh_from_db()

                return CancelOrderResult(
                    order=order,
                    cancellation=cancellation,
                    history_entry=history_entry,
                    is_replay=False,
                )

        except (ConcurrencyConflict, OperationalError) as exc:
            if isinstance(exc, OperationalError) and not _is_sqlite_lock_error(exc):
                raise
            if attempt == max_retries - 1:
                raise ValidationError(
                    "Hệ thống đang bận do có thao tác xử lý đồng thời trên đơn hàng hoặc kho hàng. Vui lòng thử lại sau.",
                    code='concurrency_exhausted'
                )
            backoff_delay = initial_retry_delay * (2 ** attempt) + random.uniform(0.01, 0.03)
            time.sleep(backoff_delay)
