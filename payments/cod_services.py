from decimal import Decimal, InvalidOperation

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.utils import timezone

from accounts.permissions import is_sales_staff
from orders.models import Order, OrderStatus
from payments.models import CODCollection, Payment, PaymentMethod, PaymentStatus


@transaction.atomic
def record_cod_collection(payment_id, actor, amount, reference, note=''):
    if not is_sales_staff(actor):
        raise PermissionDenied(
            'Chỉ nhân viên bán hàng và quản trị viên được ghi nhận thu tiền COD.'
        )
    payment_order = Payment.objects.only('order_id').get(pk=payment_id)
    order = Order.objects.select_for_update().get(pk=payment_order.order_id)
    payment = Payment.objects.select_for_update().get(pk=payment_id)
    try:
        if isinstance(amount, (bool, float)):
            raise InvalidOperation
        amount = Decimal(str(amount))
        if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
            raise InvalidOperation
    except (InvalidOperation, ValueError, TypeError):
        raise ValidationError(
            {'amount': 'Số tiền thu phải là số dương hợp lệ, tối đa 2 chữ số thập phân.'}
        )
    reference = str(reference or '').strip().upper()
    if not reference or len(reference) > 100:
        raise ValidationError({'reference': 'Cần mã chứng từ thu tiền, tối đa 100 ký tự.'})
    if payment.method != PaymentMethod.COD or order.order_status not in (
        OrderStatus.SHIPPING,
        OrderStatus.DELIVERED,
    ):
        raise ValidationError('Chỉ ghi nhận tiền COD cho đơn đang giao hoặc đã giao thành công.')
    if amount != payment.expected_amount or amount != order.grand_total:
        raise ValidationError(
            {'amount': 'Số tiền thu phải khớp chính xác tổng thanh toán của đơn.'}
        )
    existing = CODCollection.objects.filter(payment=payment).first()
    if existing:
        if (
            existing.amount == amount
            and existing.reference == reference
            and payment.status == PaymentStatus.PAID
        ):
            return existing
        raise ValidationError('Đơn hàng đã có chứng từ thu tiền khác. Không thể ghi nhận lặp.')
    if payment.status != PaymentStatus.UNPAID:
        raise ValidationError('Chỉ được ghi nhận thu tiền cho thanh toán chưa thanh toán.')
    if CODCollection.objects.filter(reference=reference).exists():
        raise ValidationError({'reference': 'Mã chứng từ đã được sử dụng cho một khoản thu khác.'})
    collected_at = timezone.now()
    receipt = CODCollection.objects.create(
        payment=payment,
        amount=amount,
        reference=reference,
        collected_by=actor,
        collected_at=collected_at,
        note=str(note).strip()[:500],
    )
    if (
        Payment.objects.filter(pk=payment.pk, status=PaymentStatus.UNPAID).update(
            status=PaymentStatus.PAID
        )
        != 1
    ):
        raise ValidationError('Trạng thái thanh toán vừa thay đổi. Vui lòng tải lại đơn.')
    return receipt
