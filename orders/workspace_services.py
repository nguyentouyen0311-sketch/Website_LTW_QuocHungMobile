from datetime import timedelta
from decimal import Decimal
from pathlib import Path
import uuid

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction
from django.db.models import Sum
from django.utils import timezone

from accounts.permissions import is_admin, is_customer, is_sales_staff
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderStatus,
    ReturnAttachment,
    ReturnReason,
    ReturnRequest,
    ReturnRequestItem,
    ReturnStatus,
    ReturnType,
    ShippingMethod,
)
from orders.services import (
    cancel_customer_cod_order,
    create_cod_order_from_cart,
    create_cod_order_with_coupon_from_cart,
)
from orders.uploads import validate_return_upload
from payments.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundReasonType,
    RefundStatus,
)

STORE_ADDRESS = '156 Nguyễn Văn Linh, Đà Nẵng'


@transaction.atomic
def place_cod_order(customer, cart, data, order_code):
    if data['payment_method'] != PaymentMethod.COD:
        raise ValidationError({'payment_method': 'Chỉ hỗ trợ thanh toán khi nhận hàng (COD).'})
    pickup = data['shipping_method'] == ShippingMethod.PICKUP
    address = (
        STORE_ADDRESS
        if pickup
        else ', '.join(
            data[field] for field in ('shipping_address', 'ward', 'district', 'province')
        )
    )
    arguments = dict(
        customer=customer,
        cart=cart,
        recipient_name=data['recipient_name'],
        recipient_phone=data['recipient_phone'],
        shipping_address=address,
        order_code=order_code,
    )
    if data.get('coupon_code'):
        result = create_cod_order_with_coupon_from_cart(
            **arguments, coupon_code=data['coupon_code']
        )
    else:
        result = create_cod_order_from_cart(**arguments)
    if not result.is_replay:
        order = result.order
        order.address_province = data.get('province', '')
        order.address_district = data.get('district', '')
        order.address_ward = data.get('ward', '')
        order.shipping_method = data['shipping_method']
        order.shipping_note = data.get('shipping_note', '')
        order.shipping_fee = Decimal('0.00')
        order.save(
            update_fields=[
                'address_province',
                'address_district',
                'address_ward',
                'shipping_method',
                'shipping_note',
                'shipping_fee',
            ]
        )
    return result


def return_eligibility(order):
    payment = getattr(order, 'payment', None)
    if not payment or payment.method != PaymentMethod.COD:
        return False, 'Yêu cầu này chỉ áp dụng cho đơn hàng COD.'
    if order.return_requests.filter(
        status__in=[ReturnStatus.PENDING, ReturnStatus.APPROVED]
    ).exists():
        return False, 'Đơn hàng đã có yêu cầu đang được xử lý.'
    if order.order_status in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED):
        return (
            payment.status == PaymentStatus.UNPAID,
            'Có thể hủy miễn phí trước khi bàn giao vận chuyển.',
        )
    if order.order_status != OrderStatus.DELIVERED or not order.delivered_at:
        return (
            False,
            'Đổi/trả được tiếp nhận sau khi giao hàng thành công; hủy đơn chỉ trước bàn giao vận chuyển.',
        )
    if timezone.now() > order.delivered_at + timedelta(days=30):
        return False, 'Đơn hàng đã quá thời hạn đổi/trả 30 ngày kể từ lúc nhận hàng.'
    completed_ids = (
        ReturnRequestItem.objects.filter(
            order_item__order=order, request__status=ReturnStatus.COMPLETED
        )
        .exclude(request__request_type=ReturnType.CANCEL)
        .values_list('order_item_id', flat=True)
    )
    if not order.items.exclude(pk__in=completed_ids).exists():
        return False, 'Tất cả sản phẩm trong đơn đã được xử lý đổi/trả.'
    return True, 'Đơn hàng còn trong thời hạn đổi/trả 30 ngày.'


def submit_return_request(customer, order_id, data, uploads):
    if not is_customer(customer):
        raise PermissionDenied('Yêu cầu tài khoản khách hàng.')
    if len(uploads) > 5:
        raise ValidationError({'attachments': 'Mỗi yêu cầu tối đa 5 tệp.'})
    verified_uploads = [(upload, validate_return_upload(upload)) for upload in uploads]
    saved_files = []
    try:
        with transaction.atomic():
            order = (
                Order.objects.select_for_update()
                .select_related('payment')
                .get(pk=order_id, customer=customer)
            )
            existing = ReturnRequest.objects.filter(request_key=data['request_key']).first()
            item_ids = {int(value) for value in data['items']}
            if existing:
                previous_ids = set(existing.items.values_list('order_item_id', flat=True))
                if (
                    existing.order_id != order.pk
                    or existing.customer_id != customer.pk
                    or existing.request_type != data['request_type']
                    or existing.reason != data['reason']
                    or existing.description != data['description'].strip()
                    or previous_ids != item_ids
                ):
                    raise ValidationError(
                        'Mã yêu cầu đã được dùng với nội dung khác. Vui lòng tải lại trang.'
                    )
                return existing, False
            eligible, reason = return_eligibility(order)
            if not eligible:
                raise ValidationError(reason)
            if (
                data['request_type'] not in ReturnType.values
                or data['reason'] not in ReturnReason.values
            ):
                raise ValidationError('Hình thức hoặc lý do yêu cầu không hợp lệ.')
            if len(data['description'].strip()) < 10 or len(data['description']) > 3000:
                raise ValidationError({'description': 'Mô tả cần từ 10 đến 3000 ký tự.'})
            items = list(order.items.filter(pk__in=item_ids))
            if not items or len(items) != len(item_ids):
                raise ValidationError({'items': 'Chỉ được chọn sản phẩm thuộc đơn hàng của bạn.'})
            completed_items = ReturnRequestItem.objects.filter(
                order_item_id__in=item_ids, request__status=ReturnStatus.COMPLETED
            ).exclude(request__request_type=ReturnType.CANCEL)
            if completed_items.exists():
                raise ValidationError({'items': 'Sản phẩm đã có yêu cầu đổi/trả hoàn tất.'})
            cancellation = data['request_type'] == ReturnType.CANCEL
            if cancellation:
                if order.order_status not in (
                    OrderStatus.PENDING_CONFIRMATION,
                    OrderStatus.CONFIRMED,
                ):
                    raise ValidationError('Không thể hủy sau khi đơn đã bàn giao vận chuyển.')
                if item_ids != set(order.items.values_list('pk', flat=True)):
                    raise ValidationError(
                        {'items': 'Hủy đơn áp dụng cho toàn bộ sản phẩm trong đơn.'}
                    )
                cancel_customer_cod_order(
                    order.pk, customer, CancellationReasonCode.OTHER, data['description'][:500]
                )
            else:
                if order.order_status != OrderStatus.DELIVERED or not order.delivered_at:
                    raise ValidationError('Đổi/trả chỉ áp dụng cho đơn đã giao thành công.')
                if (
                    data['request_type'] == ReturnType.EXCHANGE
                    and data['reason'] != ReturnReason.HARDWARE
                ):
                    raise ValidationError(
                        {
                            'reason': 'Đổi mới 1 đổi 1 áp dụng với lỗi kỹ thuật / phần cứng từ nhà sản xuất.'
                        }
                    )
                if data['request_type'] == ReturnType.REFUND and order.payment.status not in (
                    PaymentStatus.PAID,
                    PaymentStatus.PARTIALLY_REFUNDED,
                ):
                    raise ValidationError(
                        'Chưa có khoản tiền đã thu để tiếp nhận yêu cầu hoàn tiền.'
                    )
            now = timezone.now()
            amount = (
                Decimal('0.00')
                if cancellation
                else sum((item.line_total for item in items), Decimal('0.00'))
            )
            if not cancellation and amount <= 0:
                raise ValidationError('Không có giá trị thanh toán để xử lý đổi/trả.')
            request = ReturnRequest.objects.create(
                order=order,
                customer=customer,
                request_key=data['request_key'],
                request_type=data['request_type'],
                reason=data['reason'],
                description=data['description'].strip(),
                expected_amount=amount,
                response_due_at=now + timedelta(minutes=30),
                status=ReturnStatus.COMPLETED if cancellation else ReturnStatus.PENDING,
            )
            ReturnRequestItem.objects.bulk_create(
                [
                    ReturnRequestItem(request=request, order_item=item, amount=item.line_total)
                    for item in items
                ]
            )
            for upload, content_type in verified_uploads:
                attachment = ReturnAttachment(
                    request=request,
                    original_name=Path(upload.name).name[:200],
                    content_type=content_type,
                )
                attachment.file.save(
                    uuid.uuid4().hex + Path(upload.name).suffix.lower(), upload, save=False
                )
                saved_files.append((attachment.file.storage, attachment.file.name))
                attachment.save()
            return request, True
    except Exception:
        for storage, name in saved_files:
            storage.delete(name)
        raise


@transaction.atomic
def review_return_request(
    request_id, actor, decision, note, refund_reference='', received_return=False
):
    if not is_sales_staff(actor):
        raise PermissionDenied('Chỉ nhân viên được xử lý yêu cầu.')
    identity = ReturnRequest.objects.only('order_id').get(pk=request_id)
    order = Order.objects.select_for_update().get(pk=identity.order_id)
    request = ReturnRequest.objects.select_for_update().get(pk=request_id)
    if len(note.strip()) < 5:
        raise ValidationError({'note': 'Cần ghi chú xử lý tối thiểu 5 ký tự.'})
    if decision not in (ReturnStatus.APPROVED, ReturnStatus.REJECTED, ReturnStatus.COMPLETED):
        raise ValidationError('Quyết định không hợp lệ.')
    if request.status == decision:
        return request
    if request.status in (ReturnStatus.REJECTED, ReturnStatus.COMPLETED):
        raise ValidationError('Yêu cầu đã kết thúc, không thể thay đổi.')
    if decision == ReturnStatus.COMPLETED:
        if (
            not is_admin(actor)
            or request.request_type != ReturnType.REFUND
            or request.status != ReturnStatus.APPROVED
        ):
            raise PermissionDenied(
                'Chỉ quản trị viên được ghi nhận hoàn tiền thực tế cho yêu cầu trả hàng đã tiếp nhận.'
            )
        if not received_return:
            raise ValidationError('Cần xác nhận đã nhận và kiểm tra đầy đủ hàng trả lại.')
        reference = refund_reference.strip().upper()
        if (
            not reference
            or len(reference) > 100
            or Refund.objects.filter(bank_reference__iexact=reference).exists()
        ):
            raise ValidationError(
                {'refund_reference': 'Cần mã chứng từ hoàn tiền thực tế chưa được sử dụng.'}
            )
        payment = Payment.objects.select_for_update().get(order=order)
        refunded = order.refunds.filter(status=RefundStatus.COMPLETED).aggregate(
            total=Sum('amount')
        )['total'] or Decimal('0')
        if (
            payment.method != PaymentMethod.COD
            or payment.status not in (PaymentStatus.PAID, PaymentStatus.PARTIALLY_REFUNDED)
            or refunded + request.expected_amount > payment.expected_amount
        ):
            raise ValidationError('Số tiền hoàn vượt quá số tiền đã thu còn có thể hoàn.')
        refund = Refund.objects.create(
            order=order,
            reason_type=RefundReasonType.RETURN,
            amount=request.expected_amount,
            status=RefundStatus.COMPLETED,
            bank_reference=reference,
            confirmed_by=actor,
            completed_at=timezone.now(),
        )
        request.refund = refund
        payment.status = (
            PaymentStatus.REFUNDED
            if refunded + request.expected_amount == payment.expected_amount
            else PaymentStatus.PARTIALLY_REFUNDED
        )
        payment.save(update_fields=['status'])
    request.status = decision
    request.reviewed_by = actor
    request.reviewed_at = timezone.now()
    request.review_note = note.strip()
    request.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'review_note', 'refund'])
    return request
