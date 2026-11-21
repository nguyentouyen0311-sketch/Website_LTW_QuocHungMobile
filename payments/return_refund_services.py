from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction

from accounts.permissions import is_admin
from orders.models import ReturnRequest, ReturnRequestStatus, ReturnRequestType
from payments.models import PaymentStatus, Refund, RefundReasonType, RefundStatus


@transaction.atomic
def create_return_refund_request(return_request_id, actor):
    """Create a pending internal refund record; never initiates a bank transfer."""
    if not is_admin(actor):
        raise PermissionDenied('Chỉ Quản trị viên mới có thể lập hồ sơ hoàn tiền.')
    request_obj = ReturnRequest.objects.select_for_update().select_related(
        'order', 'order_item', 'order__payment'
    ).get(pk=return_request_id)
    if request_obj.request_type != ReturnRequestType.RETURN:
        raise ValidationError('Chỉ yêu cầu trả hàng và hoàn tiền mới tạo được hồ sơ hoàn tiền.')
    if request_obj.status != ReturnRequestStatus.COMPLETED:
        raise ValidationError('Kho cần hoàn tất tiếp nhận và xử lý hàng trả trước khi lập hoàn tiền.')
    payment = getattr(request_obj.order, 'payment', None)
    if not payment or payment.status not in (PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED):
        raise ValidationError('Đơn hàng chưa có khoản thanh toán hợp lệ để hoàn tiền.')
    existing = Refund.objects.filter(return_request=request_obj).first()
    if existing:
        return existing, False
    unit_amount = request_obj.order_item.line_total / Decimal(request_obj.order_item.quantity)
    amount = (unit_amount * Decimal(request_obj.quantity)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
    if amount <= Decimal('0.00'):
        raise ValidationError('Không xác định được số tiền hoàn hợp lệ từ dòng đơn hàng.')
    try:
        refund = Refund.objects.create(
            return_request=request_obj,
            order=request_obj.order,
            reason_type=RefundReasonType.RETURN,
            amount=amount,
            status=RefundStatus.PENDING,
        )
    except IntegrityError:
        refund = Refund.objects.get(return_request=request_obj)
        return refund, False
    return refund, True
