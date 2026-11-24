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
from .common import _is_sqlite_lock_error
from .results import ConfirmOrderResult


def confirm_order(
    order_id: int,
    user,
    note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ConfirmOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền xác nhận đơn hàng.")

    cleaned_note = str(note or '').strip()

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

                if order.order_status == OrderStatus.CONFIRMED:
                    return ConfirmOrderResult(
                        order=order,
                        payment=payment,
                        history_entry=None,
                        already_confirmed=True,
                    )

                if order.order_status != OrderStatus.PENDING_CONFIRMATION:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thuộc diện chờ xác nhận.",
                        code='ineligible_order_status'
                    )

                if payment.expected_amount != order.grand_total:
                    raise ValidationError(
                        f"Số tiền thanh toán dự kiến ({payment.expected_amount:,.0f} đ) không khớp với tổng tiền đơn hàng ({order.grand_total:,.0f} đ). Yêu cầu rà soát đối soát vận hành.",
                        code='amount_mismatch'
                    )

                if payment.method == PaymentMethod.COD:
                    if payment.status != PaymentStatus.UNPAID:
                        raise ValidationError(
                            f"Đơn hàng COD chờ xác nhận phải có trạng thái thanh toán là Chưa thanh toán (UNPAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình xác nhận này.",
                        code='unsupported_payment_method'
                    )

                items = list(order.items.all())
                if not items:
                    raise ValidationError("Đơn hàng không có dòng sản phẩm nào.", code='empty_order')

                for item in items:
                    res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
                    active_res = [r for r in res_list if r.status == InventoryReservationStatus.ACTIVE]
                    if len(active_res) != 1:
                        raise ValidationError(
                            f"Dòng sản phẩm '{item.product_name_snapshot}' ({item.sku_snapshot}) "
                            f"có trạng thái giữ hàng không hợp lệ (số dòng ACTIVE: {len(active_res)}). "
                            f"Cần chuyển sang rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                            code='invalid_reservation_state'
                        )

                    res = active_res[0]
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

                    if res.finished_at is not None:
                        raise ValidationError(
                            f"Dòng giữ hàng của sản phẩm '{item.product_name_snapshot}' có finished_at không hợp lệ khi còn ACTIVE.",
                            code='invalid_reservation_state'
                        )

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.PENDING_CONFIRMATION,
                ).update(order_status=OrderStatus.CONFIRMED)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.CONFIRMED:
                        return ConfirmOrderResult(
                            order=order,
                            payment=payment,
                            history_entry=None,
                            already_confirmed=True,
                        )
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng.", code='concurrent_conflict')

                order.order_status = OrderStatus.CONFIRMED

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.PENDING_CONFIRMATION,
                    new_status=OrderStatus.CONFIRMED,
                    changed_by=user,
                    reason=cleaned_note or "Nhân viên xác nhận đơn hàng hợp lệ.",
                    changed_at=timezone.now(),
                )

                return ConfirmOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
                    already_confirmed=False,
                )

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
