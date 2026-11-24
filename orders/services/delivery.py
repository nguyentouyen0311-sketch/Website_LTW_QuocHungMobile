import random
import time
from typing import Optional
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import OperationalError, transaction
from django.utils import timezone
from accounts.permissions import is_sales_staff, is_admin
from inventory.models import InventoryReservation, InventoryReservationStatus
from orders.models import Order, OrderStatus, OrderStatusHistory
from payments.models import Payment, PaymentMethod, PaymentStatus
from .common import ConcurrencyConflict, _is_sqlite_lock_error
from .results import DeliverOrderResult
from .shipping import _validate_ship_ledger_records


def _validate_consistent_delivered_outcome(order: Order, payment: Payment, items: list) -> DeliverOrderResult:
    if order.delivered_at is None:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: đơn DELIVERED thiếu mốc thời gian delivered_at.",
            code='inconsistent_replay'
        )

    now = timezone.now()
    if order.delivered_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian delivered_at nằm ở tương lai.",
            code='inconsistent_replay'
        )

    if order.created_at and order.delivered_at < order.created_at - timezone.timedelta(seconds=1):
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm tạo đơn hàng.",
            code='inconsistent_replay'
        )

    if order.created_at and order.created_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian tạo đơn hàng nằm ở tương lai.",
            code='inconsistent_replay'
        )

    shipping_history_qs = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.CONFIRMED,
        new_status=OrderStatus.SHIPPING,
    )
    if shipping_history_qs.count() != 1:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số bản ghi lịch sử CONFIRMED -> SHIPPING là {shipping_history_qs.count()} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )
    shipping_history = shipping_history_qs.first()

    if shipping_history.changed_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian xuất kho nằm ở tương lai.",
            code='inconsistent_replay'
        )

    delivered_history_qs = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.SHIPPING,
        new_status=OrderStatus.DELIVERED,
    )
    if delivered_history_qs.count() != 1:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số bản ghi lịch sử SHIPPING -> DELIVERED là {delivered_history_qs.count()} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )
    delivered_history = delivered_history_qs.first()

    if delivered_history.changed_at > now:
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: mốc thời gian ghi nhận lịch sử DELIVERED nằm ở tương lai.",
            code='inconsistent_replay'
        )

    if abs((order.delivered_at - delivered_history.changed_at).total_seconds()) > 2:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: mốc thời gian delivered_at ({order.delivered_at.isoformat()}) "
            f"không khớp thời điểm ghi nhận lịch sử DELIVERED ({delivered_history.changed_at.isoformat()}).",
            code='inconsistent_replay'
        )

    if order.delivered_at < shipping_history.changed_at - timezone.timedelta(seconds=1):
        raise ValidationError(
            "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm xuất kho.",
            code='inconsistent_replay'
        )

    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: số tiền thanh toán ({payment.expected_amount:,.0f} đ) "
            f"lệch tổng tiền đơn ({order.grand_total:,.0f} đ).",
            code='inconsistent_replay'
        )
    if payment.method == PaymentMethod.COD:
        if payment.status not in (PaymentStatus.UNPAID, PaymentStatus.PAID):
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: đơn COD đã giao phải có thanh toán UNPAID hoặc PAID, hiện tại: {payment.status}.",
                code='inconsistent_replay'
            )
    else:
        raise ValidationError(
            f"Dữ liệu giao hàng không nhất quán: phương thức thanh toán '{payment.method}' không được hỗ trợ.",
            code='inconsistent_replay'
        )

    if not items:
        raise ValidationError("Dữ liệu giao hàng không nhất quán: đơn hàng không có dòng sản phẩm.", code='inconsistent_replay')

    for item in items:
        res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng sản phẩm '{item.product_name_snapshot}' "
                f"có {len(res_list)} bản ghi giữ kho (yêu cầu đúng 1 bản ghi SHIPPED).",
                code='inconsistent_replay'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng giữ hàng '{item.product_name_snapshot}' "
                f"chưa hoàn tất trạng thái SHIPPED (status={res.status}, finished_at={res.finished_at}).",
                code='inconsistent_replay'
            )
        if res.finished_at > now:
            raise ValidationError(
                "Dữ liệu giao hàng không nhất quán: mốc thời gian hoàn tất giữ kho nằm ở tương lai.",
                code='inconsistent_replay'
            )
        if res.variant_id != item.variant_id or res.quantity != item.quantity:
            raise ValidationError(
                f"Dữ liệu giao hàng không nhất quán: dòng giữ kho không khớp biến thể hoặc số lượng đã đặt.",
                code='inconsistent_replay'
            )
        if order.delivered_at < res.finished_at - timezone.timedelta(seconds=1):
            raise ValidationError(
                "Dữ liệu giao hàng không nhất quán: thời điểm giao hàng trước thời điểm hoàn tất giữ kho.",
                code='inconsistent_replay'
            )

    _validate_ship_ledger_records(order, items, error_code='inconsistent_replay')

    return DeliverOrderResult(
        order=order,
        payment=payment,
        history_entry=None,
        already_delivered=True,
    )


def deliver_order(
    order_id: int,
    user,
    delivery_note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> DeliverOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền hoàn tất giao hàng.")

    cleaned_note = str(delivery_note or '').strip()
    if len(cleaned_note) > 500:
        raise ValidationError('Ghi chú giao hàng không được vượt quá 500 ký tự.', code='invalid_note')

    for attempt in range(max_retries):
        try:
            with transaction.atomic():
                try:
                    order = Order.objects.select_for_update().select_related('customer').prefetch_related('items').get(pk=order_id)
                except Order.DoesNotExist:
                    raise ValidationError('Đơn hàng không tồn tại.', code='order_not_found')

                payment = Payment.objects.select_for_update().filter(order=order).first()
                if not payment:
                    raise ValidationError('Đơn hàng không có bản ghi thanh toán liên kết.', code='missing_payment')

                if payment.method != PaymentMethod.COD:
                    raise ValidationError("Chỉ hỗ trợ xử lý đơn hàng COD.", code="unsupported_payment_method")

                items = list(order.items.select_for_update().select_related('variant').all())
                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                if order.order_status == OrderStatus.DELIVERED:
                    return _validate_consistent_delivered_outcome(order, payment, items)

                if order.order_status != OrderStatus.SHIPPING:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể hoàn tất giao hàng. "
                        f"Chỉ có thể hoàn tất giao hàng cho đơn hàng đang ở trạng thái Đang giao hàng (SHIPPING).",
                        code='ineligible_order_status'
                    )

                if order.delivered_at is not None:
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái SHIPPING nhưng đã có mốc thời gian delivered_at. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                if OrderStatusHistory.objects.filter(order=order, old_status=OrderStatus.SHIPPING, new_status=OrderStatus.DELIVERED).exists():
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái SHIPPING nhưng đã tồn tại bản ghi lịch sử DELIVERED. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ). Yêu cầu rà soát đối soát vận hành.",
                        code='amount_mismatch'
                    )

                if payment.method == PaymentMethod.COD:
                    if payment.status not in (PaymentStatus.UNPAID, PaymentStatus.PAID):
                        raise ValidationError(
                            f"Đơn hàng COD giao hàng phải có trạng thái thanh toán là Chưa thanh toán (UNPAID) hoặc Đã thanh toán (PAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình giao hàng này.",
                        code='unsupported_payment_method'
                    )

                now = timezone.now()

                if order.created_at and order.created_at > now:
                    raise ValidationError(
                        "Dữ liệu đơn hàng không nhất quán: mốc thời gian tạo đơn nằm ở tương lai so với thời điểm giao hàng. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_delivery_state'
                    )

                for item in items:
                    res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    if len(res_list) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có số lượng dòng giữ kho không hợp lệ ({len(res_list)} dòng, yêu cầu đúng 1 dòng SHIPPED). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    res = res_list[0]
                    if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' chưa hoàn tất xuất kho (status={res.status}, finished_at={res.finished_at}). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    if res.finished_at > now:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có mốc hoàn tất finished_at nằm ở tương lai so với thời điểm giao hàng. "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )
                    if order.created_at and res.finished_at < order.created_at - timezone.timedelta(seconds=1):
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có mốc hoàn tất finished_at trước thời điểm tạo đơn hàng.",
                            code='invalid_reservation_state'
                        )
                    if res.variant_id != item.variant_id:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' không khớp biến thể đã đặt.",
                            code='invalid_reservation_state'
                        )
                    if res.quantity != item.quantity:
                        raise ValidationError(
                            f"Số lượng giữ hàng ({res.quantity}) không khớp với số lượng đặt mua ({item.quantity}) của sản phẩm '{item.product_name_snapshot}'.",
                            code='invalid_reservation_state'
                        )

                _validate_ship_ledger_records(order, items, error_code='inconsistent_ship_ledger')

                shipping_history_qs = OrderStatusHistory.objects.filter(
                    order=order,
                    old_status=OrderStatus.CONFIRMED,
                    new_status=OrderStatus.SHIPPING,
                )
                if shipping_history_qs.count() != 1:
                    raise ValidationError(
                        f"Phát hiện xung đột dữ liệu: Đơn hàng thiếu bản ghi lịch sử CONFIRMED -> SHIPPING (hiện có {shipping_history_qs.count()} bản ghi).",
                        code='inconsistent_shipping_history'
                    )
                shipping_history = shipping_history_qs.first()
                if shipping_history.changed_at > now:
                    raise ValidationError(
                        "Dữ liệu xuất kho không nhất quán: mốc thời gian xuất kho nằm ở tương lai so với thời điểm giao hàng.",
                        code='inconsistent_shipping_history'
                    )
                if order.created_at and shipping_history.changed_at < order.created_at - timezone.timedelta(seconds=1):
                    raise ValidationError(
                        "Dữ liệu xuất kho không nhất quán: mốc thời gian xuất kho trước thời điểm tạo đơn hàng.",
                        code='inconsistent_shipping_history'
                    )

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.SHIPPING,
                ).update(order_status=OrderStatus.DELIVERED, delivered_at=now)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.DELIVERED:
                        return _validate_consistent_delivered_outcome(order, payment, items)
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng sang DELIVERED.", code='concurrent_conflict')

                order.order_status = OrderStatus.DELIVERED
                order.delivered_at = now

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.SHIPPING,
                    new_status=OrderStatus.DELIVERED,
                    changed_by=user,
                    reason=cleaned_note or "Giao hàng thành công đến người nhận.",
                    changed_at=now,
                )

                return DeliverOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
                    already_delivered=False,
                )

        except ConcurrencyConflict:
            if attempt >= max_retries - 1:
                raise ValidationError(
                    "Xung đột dữ liệu khi cập nhật đơn hàng đồng thời. Vui lòng thử lại sau giây lát.",
                    code='concurrent_conflict'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

        except OperationalError as oe:
            if not _is_sqlite_lock_error(oe) or attempt >= max_retries - 1:
                raise ValidationError(
                    "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
                    code='database_busy'
                )
            time.sleep(initial_retry_delay * (attempt + 1) + random.uniform(0.01, 0.03))

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )
