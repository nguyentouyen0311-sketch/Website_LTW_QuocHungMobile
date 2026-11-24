import random
import time
from typing import Optional
import uuid
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, OperationalError, transaction
from django.db.models import F
from django.utils import timezone
from accounts.permissions import is_sales_staff, is_admin
from inventory.models import (
    Inventory,
    InventoryReservation,
    InventoryReservationStatus,
    InventoryTransaction,
)
from orders.models import Order, OrderStatus, OrderStatusHistory
from payments.models import Payment, PaymentMethod, PaymentStatus
from .common import ConcurrencyConflict, _is_sqlite_lock_error
from .results import ShipOrderResult


def _validate_consistent_shipped_outcome(order: Order, payment: Payment, items: list) -> ShipOrderResult:
    if payment.expected_amount != order.grand_total:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số tiền thanh toán ({payment.expected_amount:,.0f} đ) "
            f"lệch tổng tiền đơn ({order.grand_total:,.0f} đ).",
            code='inconsistent_replay'
        )
    if payment.method == PaymentMethod.COD and payment.status != PaymentStatus.UNPAID:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: đơn COD đã giao phải có thanh toán UNPAID, hiện tại: {payment.status}.",
            code='inconsistent_replay'
        )

    if not items:
        raise ValidationError("Dữ liệu xuất kho không nhất quán: đơn hàng không có dòng sản phẩm.", code='inconsistent_replay')

    variant_aggs = {}
    for item in items:
        res_list = list(InventoryReservation.objects.select_for_update().filter(order_item=item))
        if len(res_list) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng sản phẩm '{item.product_name_snapshot}' "
                f"có {len(res_list)} bản ghi giữ kho (yêu cầu đúng 1 bản ghi SHIPPED).",
                code='inconsistent_replay'
            )
        res = res_list[0]
        if res.status != InventoryReservationStatus.SHIPPED or res.finished_at is None:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng giữ hàng '{item.product_name_snapshot}' "
                f"chưa hoàn tất trạng thái SHIPPED (status={res.status}, finished_at={res.finished_at}).",
                code='inconsistent_replay'
            )
        if res.variant_id != item.variant_id or res.quantity != item.quantity:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: dòng giữ kho không khớp biến thể hoặc số lượng đã đặt.",
                code='inconsistent_replay'
            )

        vid = item.variant_id
        variant_aggs[vid] = variant_aggs.get(vid, 0) + item.quantity

    ship_txs = list(
        InventoryTransaction.objects.select_for_update()
        .filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP)
        .select_related('inventory')
        .order_by('id')
    )

    if len(ship_txs) != len(variant_aggs):
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi sổ cái SHIP ({len(ship_txs)}) "
            f"không khớp số biến thể trong đơn ({len(variant_aggs)}).",
            code='inconsistent_replay'
        )

    for vid, total_qty in variant_aggs.items():
        matching = [tx for tx in ship_txs if tx.inventory.variant_id == vid]
        if len(matching) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: biến thể ID {vid} có {len(matching)} bản ghi sổ cái SHIP (yêu cầu đúng 1).",
                code='inconsistent_replay'
            )
        tx = matching[0]
        if total_qty <= 0 or tx.quantity_delta >= 0 or tx.quantity_delta != -total_qty:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: số lượng sổ cái SHIP ({tx.quantity_delta}) "
                f"không khớp số lượng đã đặt (-{total_qty}).",
                code='inconsistent_replay'
            )
        if tx.physical_before is None or tx.physical_after is None:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước hoặc sau trong sổ cái SHIP không được để trống.",
                code='inconsistent_replay'
            )
        if tx.physical_before < 0 or tx.physical_after < 0:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước ({tx.physical_before}) "
                f"hoặc sau ({tx.physical_after}) trong sổ cái SHIP không được âm.",
                code='inconsistent_replay'
            )
        if tx.physical_after != tx.physical_before + tx.quantity_delta:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: phép tính tồn kho sổ cái SHIP không khớp "
                f"(physical_after {tx.physical_after} != physical_before {tx.physical_before} + quantity_delta {tx.quantity_delta}).",
                code='inconsistent_replay'
            )
        if tx.order_id != order.id:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: bản ghi sổ cái SHIP không liên kết đúng đơn hàng.",
                code='inconsistent_replay'
            )

    shipping_history_count = OrderStatusHistory.objects.filter(
        order=order,
        old_status=OrderStatus.CONFIRMED,
        new_status=OrderStatus.SHIPPING,
    ).count()
    if shipping_history_count != 1:
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi lịch sử chuyển trạng thái CONFIRMED -> SHIPPING là {shipping_history_count} (yêu cầu đúng 1 bản ghi).",
            code='inconsistent_replay'
        )

    return ShipOrderResult(
        order=order,
        payment=payment,
        history_entry=None,
        transactions=ship_txs,
        already_shipped=True,
    )


def ship_order(
    order_id: int,
    user,
    shipping_note: Optional[str] = "",
    max_retries: int = 5,
    initial_retry_delay: float = 0.03,
) -> ShipOrderResult:
    if not (is_sales_staff(user) or is_admin(user)):
        raise PermissionDenied("Chỉ Nhân viên bán hàng hoặc Quản trị viên mới có thẩm quyền xuất kho và bắt đầu giao hàng.")

    cleaned_note = str(shipping_note or '').strip()
    if len(cleaned_note) > 500:
        raise ValidationError('Ghi chú vận chuyển không được vượt quá 500 ký tự.', code='invalid_note')

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

                if order.order_status == OrderStatus.SHIPPING:
                    return _validate_consistent_shipped_outcome(order, payment, items)

                if InventoryTransaction.objects.filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP).exists():
                    raise ValidationError(
                        "Phát hiện xung đột dữ liệu: Đơn hàng ở trạng thái CONFIRMED nhưng đã tồn tại bản ghi sổ cái SHIP. "
                        "Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                        code='conflicting_shipping_state'
                    )

                if order.order_status != OrderStatus.CONFIRMED:
                    raise ValidationError(
                        f"Đơn hàng đang ở trạng thái '{order.get_order_status_display()}', không thể xuất kho và bắt đầu giao hàng.",
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
                            f"Đơn hàng COD xuất kho phải có trạng thái thanh toán là Chưa thanh toán (UNPAID). Hiện tại: {payment.get_status_display()}.",
                            code='invalid_cod_payment_status'
                        )
                else:
                    raise ValidationError(
                        f"Phương thức thanh toán '{payment.method}' không được hỗ trợ trong quy trình xuất kho này.",
                        code='unsupported_payment_method'
                    )

                variant_aggs = {}
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

                    vid = item.variant_id
                    if vid not in variant_aggs:
                        variant_aggs[vid] = {
                            'variant': item.variant,
                            'total_quantity': 0,
                            'reservations': [],
                        }
                    variant_aggs[vid]['total_quantity'] += item.quantity
                    variant_aggs[vid]['reservations'].append(res)

                now = timezone.now()
                created_transactions = []

                for vid in sorted(variant_aggs.keys()):
                    agg = variant_aggs[vid]
                    total_qty = agg['total_quantity']

                    try:
                        inv = Inventory.objects.select_for_update().get(variant_id=vid)
                    except Inventory.DoesNotExist:
                        raise ValidationError(
                            f"Không tìm thấy bản ghi tồn kho cho biến thể SKU '{agg['variant'].sku}'.",
                            code='missing_inventory'
                        )

                    if inv.physical_sellable < total_qty:
                        raise ValidationError(
                            f"Tồn thực tế của biến thể SKU '{agg['variant'].sku}' ({inv.physical_sellable}) "
                            f"không đủ để xuất kho (yêu cầu {total_qty}).",
                            code='insufficient_inventory'
                        )

                    if inv.reserved < total_qty:
                        raise ValidationError(
                            f"Tồn đã giữ của biến thể SKU '{agg['variant'].sku}' ({inv.reserved}) "
                            f"không đủ để xuất kho (yêu cầu {total_qty}). Yêu cầu rà soát đối soát vận hành.",
                            code='inconsistent_inventory'
                        )

                    before_phys = inv.physical_sellable
                    after_phys = before_phys - total_qty

                    rows_updated = Inventory.objects.filter(
                        pk=inv.pk,
                        physical_sellable=before_phys,
                        reserved=inv.reserved,
                        physical_sellable__gte=total_qty,
                        reserved__gte=total_qty,
                    ).update(
                        physical_sellable=F('physical_sellable') - total_qty,
                        reserved=F('reserved') - total_qty,
                        updated_at=now,
                    )

                    if rows_updated != 1:
                        raise ConcurrencyConflict(f"Xung đột tương tranh khi cập nhật tồn kho biến thể SKU '{agg['variant'].sku}'.")

                    req_key = uuid.uuid5(uuid.NAMESPACE_URL, f"ship:{order.order_code}:{inv.id}")
                    tx = InventoryTransaction.objects.create(
                        inventory=inv,
                        order=order,
                        transaction_type=InventoryTransaction.TYPE_SHIP,
                        quantity_delta=-total_qty,
                        physical_before=before_phys,
                        physical_after=after_phys,
                        performed_by=user,
                        request_key=req_key,
                    )
                    created_transactions.append(tx)

                for vid in sorted(variant_aggs.keys()):
                    for res in variant_aggs[vid]['reservations']:
                        res.status = InventoryReservationStatus.SHIPPED
                        res.finished_at = now
                        res.save(update_fields=['status', 'finished_at'])

                rows_updated = Order.objects.filter(
                    pk=order.pk,
                    order_status=OrderStatus.CONFIRMED,
                ).update(order_status=OrderStatus.SHIPPING)

                if rows_updated != 1:
                    order.refresh_from_db()
                    if order.order_status == OrderStatus.SHIPPING:
                        return _validate_consistent_shipped_outcome(order, payment, items)
                    raise ValidationError("Xung đột tương tranh khi cập nhật trạng thái đơn hàng sang SHIPPING.", code='concurrent_conflict')

                order.order_status = OrderStatus.SHIPPING

                history_entry = OrderStatusHistory.objects.create(
                    order=order,
                    old_status=OrderStatus.CONFIRMED,
                    new_status=OrderStatus.SHIPPING,
                    changed_by=user,
                    reason=cleaned_note or "Bắt đầu giao hàng và xuất kho.",
                    changed_at=now,
                )

                return ShipOrderResult(
                    order=order,
                    payment=payment,
                    history_entry=history_entry,
                    transactions=created_transactions,
                    already_shipped=False,
                )

        except ConcurrencyConflict:
            if attempt >= max_retries - 1:
                raise ValidationError(
                    "Xung đột dữ liệu khi cập nhật tồn kho đồng thời. Vui lòng thử lại sau giây lát.",
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

        except IntegrityError as ie:
            try:
                order.refresh_from_db()
                if order.order_status == OrderStatus.SHIPPING:
                    return _validate_consistent_shipped_outcome(order, payment, items)
            except Exception:
                pass
            raise ie

    raise ValidationError(
        "Hệ thống cơ sở dữ liệu đang bận xử lý giao dịch đồng thời. Vui lòng thử lại sau giây lát.",
        code='database_busy'
    )


def _validate_ship_ledger_records(order: Order, items: list, error_code: str = 'inconsistent_ship_ledger') -> list[InventoryTransaction]:
    variant_aggs = {}
    for item in items:
        vid = item.variant_id
        variant_aggs[vid] = variant_aggs.get(vid, 0) + item.quantity

    ship_txs = list(
        InventoryTransaction.objects.select_for_update()
        .filter(order=order, transaction_type=InventoryTransaction.TYPE_SHIP)
        .select_related('inventory')
        .order_by('id')
    )

    if not ship_txs:
        raise ValidationError(
            "Đơn hàng thiếu bản ghi sổ cái xuất kho SHIP.",
            code='missing_ship_ledger' if error_code != 'inconsistent_replay' else 'inconsistent_replay'
        )

    if len(ship_txs) != len(variant_aggs):
        raise ValidationError(
            f"Dữ liệu xuất kho không nhất quán: số bản ghi sổ cái SHIP ({len(ship_txs)}) "
            f"không khớp số biến thể trong đơn ({len(variant_aggs)}). Yêu cầu rà soát đối soát vận hành.",
            code=error_code
        )

    for vid, total_qty in variant_aggs.items():
        matching = [tx for tx in ship_txs if tx.inventory.variant_id == vid]
        if len(matching) != 1:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: biến thể ID {vid} có {len(matching)} bản ghi sổ cái SHIP (yêu cầu đúng 1).",
                code=error_code
            )
        tx = matching[0]
        if total_qty <= 0 or tx.quantity_delta >= 0 or tx.quantity_delta != -total_qty:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: số lượng sổ cái SHIP ({tx.quantity_delta}) "
                f"không khớp số lượng đã đặt (-{total_qty}).",
                code=error_code
            )
        if tx.physical_before is None or tx.physical_after is None:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước hoặc sau trong sổ cái SHIP không được để trống.",
                code=error_code
            )
        if tx.physical_before < 0 or tx.physical_after < 0:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: giá trị tồn kho trước ({tx.physical_before}) "
                f"hoặc sau ({tx.physical_after}) trong sổ cái SHIP không được âm.",
                code=error_code
            )
        if tx.physical_after != tx.physical_before + tx.quantity_delta:
            raise ValidationError(
                f"Dữ liệu xuất kho không nhất quán: phép tính tồn kho sổ cái SHIP không khớp "
                f"(physical_after {tx.physical_after} != physical_before {tx.physical_before} + quantity_delta {tx.quantity_delta}). "
                f"Yêu cầu rà soát đối soát vận hành, không tự động sửa dữ liệu.",
                code=error_code
            )
        if tx.order_id != order.id:
            raise ValidationError(
                "Dữ liệu xuất kho không nhất quán: bản ghi sổ cái SHIP không liên kết đúng đơn hàng.",
                code=error_code
            )

    return ship_txs
