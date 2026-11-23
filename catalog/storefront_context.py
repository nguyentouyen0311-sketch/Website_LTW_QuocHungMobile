from accounts.permissions import is_customer
from catalog.models import Category
from orders.models import Order, OrderStatus


def storefront_account(request):
    user = getattr(request, 'user', None)
    if not user or not user.is_authenticated or not is_customer(user):
        return {'customer_order_count': 0, 'storefront_customer': None}
    closed = [OrderStatus.DELIVERED, OrderStatus.CANCELLED, OrderStatus.EXPIRED, OrderStatus.DELIVERY_FAILED, OrderStatus.FAILED_CLOSED]
    display_name = user.get_full_name().strip() or user.username
    initials = ''.join(part[0] for part in display_name.split()[:2]).upper() or 'KH'
    return {
        'customer_order_count': Order.objects.filter(customer=user).exclude(order_status__in=closed).count(),
        'storefront_customer': {'name': display_name, 'initials': initials},
    }


def storefront_navigation(request):
    """Danh mục hiển thị trong thanh điều hướng khách hàng.

    Chỉ trả về danh mục đang được quản trị viên bật hiển thị để storefront
    luôn phản ánh dữ liệu quản trị, không phụ thuộc một danh sách hard-code.
    """
    return {
        'storefront_nav_categories': (
            Category.objects.filter(is_visible=True)
            .exclude(name='QA - Đổi trả')
            .order_by('name')
        ),
    }
