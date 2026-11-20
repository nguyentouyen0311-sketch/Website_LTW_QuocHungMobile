from datetime import timedelta
from math import ceil

from django.db.models import Q
from django.utils import timezone

from orders.models import Order, OrderStatus, ReturnStatus
from payments.models import PaymentMethod, PaymentStatus

STATUS_TABS = [
    ('all', 'Tất cả'),
    ('pending', 'Chờ xác nhận'),
    ('preparing', 'Đang chuẩn bị hàng'),
    ('shipping', 'Đang giao hàng'),
    ('completed', 'Hoàn thành'),
    ('cancelled', 'Hủy / Đổi trả'),
]
STATUS_FILTERS = {
    'pending': Q(order_status=OrderStatus.PENDING_CONFIRMATION),
    'preparing': Q(order_status=OrderStatus.CONFIRMED),
    'shipping': Q(order_status=OrderStatus.SHIPPING),
    'completed': Q(order_status=OrderStatus.DELIVERED)
    & ~Q(
        return_requests__status__in=[
            ReturnStatus.PENDING,
            ReturnStatus.APPROVED,
            ReturnStatus.COMPLETED,
        ]
    ),
    'cancelled': Q(order_status=OrderStatus.CANCELLED)
    | Q(
        return_requests__status__in=[
            ReturnStatus.PENDING,
            ReturnStatus.APPROVED,
            ReturnStatus.COMPLETED,
        ]
    ),
}
STATUS_LABELS = {
    OrderStatus.PENDING_CONFIRMATION: ('Chờ xác nhận', 'pending'),
    OrderStatus.CONFIRMED: ('Đang chuẩn bị hàng', 'preparing'),
    OrderStatus.SHIPPING: ('Đang giao hàng', 'shipping'),
    OrderStatus.DELIVERED: ('Giao thành công', 'completed'),
    OrderStatus.CANCELLED: ('Đã hủy', 'cancelled'),
}


def workspace_orders():
    return (
        Order.objects.filter(payment__method=PaymentMethod.COD)
        .select_related('customer', 'payment__cod_collection__collected_by', 'coupon_usage__coupon')
        .prefetch_related(
            'items__variant__product__images',
            'items__variant__inventory',
            'items__reservations',
            'status_history',
            'return_requests__items__order_item',
            'return_requests__attachments',
        )
    )


def decorate_order(order):
    usage = getattr(order, 'coupon_usage', None)
    order.coupon_code = usage.coupon.code if usage else ''
    order.ui_status, order.ui_status_class = STATUS_LABELS.get(
        order.order_status, (order.get_order_status_display(), 'neutral')
    )
    order.ui_items = list(order.items.all())
    order.ui_history = sorted(
        order.status_history.all(), key=lambda event: (event.changed_at, event.pk), reverse=True
    )
    order.ui_requests = sorted(
        order.return_requests.all(), key=lambda request: request.pk, reverse=True
    )
    order.latest_request = order.ui_requests[0] if order.ui_requests else None
    handled = next(
        (
            record
            for record in order.ui_requests
            if record.request_type != 'CANCEL' and record.status != ReturnStatus.REJECTED
        ),
        None,
    )
    if handled:
        order.ui_status = 'Đã hoàn tiền' if handled.refund_id else 'Yêu cầu đổi / trả'
        order.ui_status_class = 'cancelled'
    completed_item_ids = {
        entry.order_item_id
        for record in order.ui_requests
        if record.status == ReturnStatus.COMPLETED and record.request_type != 'CANCEL'
        for entry in record.items.all()
    }
    for item in order.ui_items:
        item.ui_returnable = item.pk not in completed_item_ids
        item.ui_line_gross = item.unit_price * item.quantity
        item.ui_original_gross = (
            item.original_unit_price * item.quantity if item.original_unit_price else None
        )
        images = list(item.variant.product.images.all())
        image = next(
            (image for image in images if image.variant_id == item.variant_id), None
        ) or next((image for image in images if image.variant_id is None), None)
        item.ui_image = image.image.url if image else ''
        inventory = getattr(item.variant, 'inventory', None)
        item.ui_available = (
            max(0, inventory.physical_sellable - inventory.reserved) if inventory else 0
        )
    payment = getattr(order, 'payment', None)
    order.can_cancel = bool(
        payment
        and payment.status == PaymentStatus.UNPAID
        and order.order_status in (OrderStatus.PENDING_CONFIRMATION, OrderStatus.CONFIRMED)
    )
    deadline = order.delivered_at + timedelta(days=30) if order.delivered_at else None
    order.return_days_remaining = (
        max(0, ceil((deadline - timezone.now()).total_seconds() / 86400)) if deadline else 0
    )
    order.return_deadline = deadline
    has_open = any(
        request.status in (ReturnStatus.PENDING, ReturnStatus.APPROVED)
        for request in order.ui_requests
    )
    order.can_return = (
        order.order_status == OrderStatus.DELIVERED
        and bool(deadline and timezone.now() <= deadline)
        and not has_open
        and any(item.ui_returnable for item in order.ui_items)
    )
    stages = [
        OrderStatus.PENDING_CONFIRMATION,
        OrderStatus.CONFIRMED,
        OrderStatus.SHIPPING,
        OrderStatus.DELIVERED,
    ]
    titles = [
        'Đặt hàng thành công',
        'Đã xác nhận & đóng gói',
        'Đang vận chuyển',
        'Giao hàng thành công',
    ]
    current = stages.index(order.order_status) if order.order_status in stages else -1
    order.ui_steps = []
    for index, (status, title) in enumerate(zip(stages, titles)):
        event = next(
            (entry for entry in reversed(order.ui_history) if entry.new_status == status), None
        )
        order.ui_steps.append(
            {
                'number': index + 1,
                'title': title,
                'time': event.changed_at if event else (order.created_at if index == 0 else None),
                'done': current >= index,
                'current': current == index,
            }
        )
    return order


def status_tabs(queryset, selected, customer=False):
    result = []
    for key, label in STATUS_TABS:
        if customer and key == 'completed':
            label = 'Đã giao'
        elif customer and key == 'cancelled':
            label = 'Đã hủy / Đổi trả'
        query = queryset if key == 'all' else queryset.filter(STATUS_FILTERS[key])
        result.append(
            {
                'key': key,
                'label': label,
                'count': query.distinct().count(),
                'active': key == selected,
            }
        )
    return result
