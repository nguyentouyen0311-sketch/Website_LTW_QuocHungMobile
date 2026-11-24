from decimal import Decimal, ROUND_HALF_UP

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.permissions import is_admin
from orders.models import ReturnRequest, ReturnRequestStatus, ReturnRequestType
from payments.models import PaymentStatus, Refund, RefundReasonType, RefundStatus


@transaction.atomic
def create_return_refund_request(return_request_id, actor):
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


@transaction.atomic
def confirm_return_refund(refund_id, actor, bank_reference, completed_at, audit_note):
    if not is_admin(actor):
        raise PermissionDenied('Chỉ Quản trị viên mới được xác nhận hoàn tiền.')
    refund = Refund.objects.select_for_update().select_related('order__payment').get(pk=refund_id)
    if refund.reason_type != RefundReasonType.RETURN or not refund.return_request_id:
        raise ValidationError('Hồ sơ này không phải hoàn tiền từ yêu cầu trả hàng.')
    if refund.status == RefundStatus.COMPLETED:
        return refund, False
    if refund.status != RefundStatus.PENDING:
        raise ValidationError('Chỉ hồ sơ hoàn tiền đang chờ xử lý mới được xác nhận.')
    reference = str(bank_reference or '').strip().upper()
    note = str(audit_note or '').strip()
    if not reference or len(reference) > 100:
        raise ValidationError('Cần nhập mã giao dịch ngân hàng hợp lệ.')
    if len(note) < 5:
        raise ValidationError('Ghi chú kiểm toán cần tối thiểu 5 ký tự.')
    if not completed_at:
        raise ValidationError('Cần nhập thời điểm hoàn tiền.')
    if timezone.is_naive(completed_at):
        completed_at = timezone.make_aware(completed_at, timezone.get_current_timezone())
    if completed_at > timezone.now():
        raise ValidationError('Thời điểm hoàn tiền không được ở tương lai.')
    if Refund.objects.filter(bank_reference__iexact=reference).exclude(pk=refund.pk).exists():
        raise ValidationError('Mã giao dịch ngân hàng đã được dùng cho hồ sơ hoàn tiền khác.')
    payment = getattr(refund.order, 'payment', None)
    if not payment or payment.status not in (PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED):
        raise ValidationError('Trạng thái thanh toán không còn phù hợp để xác nhận hoàn tiền.')
    prior = Refund.objects.filter(order=refund.order, status=RefundStatus.COMPLETED).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    if prior + refund.amount > payment.expected_amount:
        raise ValidationError('Tổng hoàn tiền vượt quá số tiền khách đã thanh toán.')
    refund.bank_reference = reference
    refund.confirmed_by = actor
    refund.completed_at = completed_at
    refund.audit_note = note
    refund.status = RefundStatus.COMPLETED
    refund.full_clean()
    refund.save(update_fields=['bank_reference', 'confirmed_by', 'completed_at', 'audit_note', 'status'])
    payment.status = PaymentStatus.REFUNDED if prior + refund.amount == payment.expected_amount else PaymentStatus.PARTIALLY_REFUNDED
    payment.save(update_fields=['status'])
    return refund, True
