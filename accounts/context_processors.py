from accounts.permissions import (
    is_admin,
    is_sales_staff,
    is_inventory_staff,
    is_customer,
    get_user_roles,
)


def auth_roles(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not user.is_active:
        return {
            'is_customer': False,
            'is_sales_staff': False,
            'is_inventory_staff': False,
            'is_admin': False,
            'can_access_django_admin': False,
            'user_roles': set(),
        }

    return {
        'is_customer': is_customer(user),
        'is_sales_staff': is_sales_staff(user),
        'is_inventory_staff': is_inventory_staff(user),
        'is_admin': is_admin(user),
        'can_access_django_admin': bool(user.is_staff),
        'user_roles': get_user_roles(user),
    }


def management_notifications(request):
    """Số việc cần xử lý hiển thị trên sidebar quản trị.

    Chỉ truy vấn cho nhân sự có quyền xử lý để khách hàng không phát sinh
    truy vấn không cần thiết. Mỗi badge phản ánh đúng trang mà nhân sự có thể
    mở để xử lý nghiệp vụ.
    """
    user = getattr(request, 'user', None)
    empty = {
        'pending_orders_count': 0,
        'pending_return_requests_count': 0,
        'return_notification_target': '',
    }
    if not user or not user.is_authenticated or not user.is_active:
        return empty

    can_process_orders = is_sales_staff(user)
    can_review_returns = is_sales_staff(user)
    can_complete_returns = is_inventory_staff(user)
    if not (can_process_orders or can_review_returns or can_complete_returns):
        return empty

    from orders.models import Order, OrderStatus, ReturnRequest, ReturnRequestStatus

    pending_orders_count = (
        Order.objects.filter(order_status=OrderStatus.PENDING_CONFIRMATION).count()
        if can_process_orders else 0
    )

    return_statuses = []
    if can_review_returns:
        return_statuses.append(ReturnRequestStatus.PENDING)
    if can_complete_returns:
        return_statuses.append(ReturnRequestStatus.APPROVED)
    pending_return_requests_count = ReturnRequest.objects.filter(status__in=return_statuses).count()

    return {
        'pending_orders_count': pending_orders_count,
        'pending_return_requests_count': pending_return_requests_count,
        # Nhân viên kho đi thẳng tới danh sách xác nhận hoàn tất; sales/admin
        # đi tới danh sách duyệt yêu cầu.
        'return_notification_target': (
            'inventory:returned_stock_list'
            if can_complete_returns and not can_review_returns
            else 'orders:return_request_management'
        ),
    }
