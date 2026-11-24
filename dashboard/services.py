"""
Dịch vụ tính toán và tổng hợp số liệu kinh doanh cho Dashboard — Quốc Hùng Mobile.
Tất cả số liệu được truy vấn trực tiếp từ cơ sở dữ liệu gốc (orders, payments, catalog, inventory).
Không gán cứng (hardcode) số liệu và tuân thủ các quy tắc kế toán & báo cáo kinh doanh.
"""

import csv
from datetime import date, datetime, timedelta
from decimal import Decimal, ROUND_HALF_UP
import io
from typing import Any, Dict, List, Optional, Tuple

from django.db.models import Count, F, Q, Sum
from django.utils import timezone

from catalog.models import Category, Product, ProductVariant
from inventory.models import Inventory
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderItem,
    OrderStatus,
    ReturnRequest,
    ReturnRequestStatus,
    ReturnRequestType,
    ReturnReasonCode,
)
from payments.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundStatus,
)

# 7 nhóm lý do đổi trả chuẩn theo đặc tả báo cáo kinh doanh (SRS & Section 11)
RETURN_REASON_TECHNICAL_DEFECT = ReturnReasonCode.TECHNICAL_DEFECT
RETURN_REASON_WRONG_ITEM = ReturnReasonCode.WRONG_ITEM
RETURN_REASON_NOT_AS_DESCRIBED = ReturnReasonCode.NOT_AS_DESCRIBED
RETURN_REASON_DAMAGED_ON_ARRIVAL = ReturnReasonCode.DAMAGED_ON_ARRIVAL
RETURN_REASON_NOT_SUITABLE = ReturnReasonCode.NOT_SUITABLE
RETURN_REASON_CHANGE_COLOR_CAPACITY = ReturnReasonCode.CHANGE_COLOR_CAPACITY
RETURN_REASON_OTHER = ReturnReasonCode.OTHER

RETURN_REASON_LABELS = {
    ReturnReasonCode.TECHNICAL_DEFECT: 'Sản phẩm lỗi kỹ thuật',
    ReturnReasonCode.WRONG_ITEM: 'Giao sai sản phẩm/biến thể',
    ReturnReasonCode.NOT_AS_DESCRIBED: 'Sản phẩm không đúng mô tả',
    ReturnReasonCode.DAMAGED_ON_ARRIVAL: 'Sản phẩm hư hỏng khi nhận',
    ReturnReasonCode.NOT_SUITABLE: 'Không phù hợp với nhu cầu',
    ReturnReasonCode.CHANGE_COLOR_CAPACITY: 'Muốn đổi màu sắc/dung lượng',
    ReturnReasonCode.OTHER: 'Khác',
}


def calculate_growth(current: Any, previous: Any) -> float:
    """
    Tính phần trăm tăng trưởng giữa kỳ hiện tại và kỳ trước:
    Growth = ((Current - Previous) / Previous) * 100%
    Xử lý an toàn khi mẫu số bằng 0.
    """
    curr = float(current or 0)
    prev = float(previous or 0)
    if prev == 0.0:
        if curr == 0.0:
            return 0.0
        return 100.0  # Tăng trưởng 100% khi kỳ trước bằng 0 và kỳ này phát sinh
    return round(((curr - prev) / prev) * 100.0, 1)


def get_date_range(
    period: Optional[str] = None,
    start_date_str: Optional[str] = None,
    end_date_str: Optional[str] = None,
    compare_to: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Xác định khoảng thời gian báo cáo và khoảng thời gian kỳ trước để so sánh đối chiếu.
    Hỗ trợ các bộ lọc: 'today', 'last_7_days', 'this_month', 'custom'.
    Mặc định: 'this_month'.
    """
    now = timezone.now()
    today = timezone.localdate()

    period = (period or '').strip().lower()
    if period not in ['today', 'last_7_days', 'this_month', 'custom']:
        period = 'this_month'

    if period == 'today':
        start_date = today
        end_date = today
        period_label = f"Hôm nay ({today.strftime('%d/%m/%Y')})"
    elif period == 'last_7_days':
        start_date = today - timedelta(days=6)
        end_date = today
        period_label = f"7 ngày qua ({start_date.strftime('%d/%m')} - {end_date.strftime('%d/%m/%Y')})"
    elif period == 'custom':
        try:
            start_date = datetime.strptime(start_date_str or '', '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date_str or '', '%Y-%m-%d').date()
            if end_date < start_date:
                start_date, end_date = end_date, start_date
            period_label = f"Tùy chọn ({start_date.strftime('%d/%m/%Y')} - {end_date.strftime('%d/%m/%Y')})"
        except (ValueError, TypeError):
            # Nếu chuỗi ngày không hợp lệ, chuyển về mặc định tháng này
            period = 'this_month'
            start_date = today.replace(day=1)
            end_date = today
            period_label = f"Tháng này ({start_date.strftime('%d/%m')} - {end_date.strftime('%d/%m/%Y')})"
    else:  # this_month
        start_date = today.replace(day=1)
        end_date = today
        period_label = f"Tháng này ({start_date.strftime('%d/%m')} - {end_date.strftime('%d/%m/%Y')})"

    duration_days = max(1, (end_date - start_date).days + 1)

    # Tính toán kỳ trước liền kề (Previous Period) có cùng số ngày N
    prev_end_date = start_date - timedelta(days=1)
    prev_start_date = prev_end_date - timedelta(days=duration_days - 1)
    comparison_label = f"Kỳ trước ({prev_start_date.strftime('%d/%m')} - {prev_end_date.strftime('%d/%m/%Y')})"

    # Chuyển đổi sang datetime nhận biết múi giờ (Timezone-Aware Datetime)
    current_tz = timezone.get_current_timezone()
    start_dt = timezone.make_aware(datetime.combine(start_date, datetime.min.time()), current_tz)
    end_dt = timezone.make_aware(datetime.combine(end_date, datetime.max.time()), current_tz)

    prev_start_dt = timezone.make_aware(datetime.combine(prev_start_date, datetime.min.time()), current_tz)
    prev_end_dt = timezone.make_aware(datetime.combine(prev_end_date, datetime.max.time()), current_tz)

    return {
        'period': period,
        'start_date': start_date,
        'end_date': end_date,
        'start_date_str': start_date.strftime('%Y-%m-%d'),
        'end_date_str': end_date.strftime('%Y-%m-%d'),
        'start_dt': start_dt,
        'end_dt': end_dt,
        'prev_start_date': prev_start_date,
        'prev_end_date': prev_end_date,
        'prev_start_date_str': prev_start_date.strftime('%Y-%m-%d'),
        'prev_end_date_str': prev_end_date.strftime('%Y-%m-%d'),
        'prev_start_dt': prev_start_dt,
        'prev_end_dt': prev_end_dt,
        'duration_days': duration_days,
        'period_label': period_label,
        'comparison_label': comparison_label,
    }


def classify_return_reason(reason_text: str) -> str:
    """
    Phân loại lý do đổi trả từ văn bản tự do của khách hàng vào 7 nhóm tiêu chuẩn.
    Tuân thủ quy chuẩn Section 11 của SRS.
    """
    if not reason_text:
        return RETURN_REASON_OTHER

    text = reason_text.strip().lower()

    # 1. Lỗi kỹ thuật (Technical defect)
    tech_keywords = ['lỗi', 'hỏng', 'rè', 'màn hình', 'camera', 'micro', 'pin', 'không lên', 'sập nguồn', 'nhấp nháy', 'liệt', 'loa']
    if any(k in text for k in tech_keywords):
        return RETURN_REASON_TECHNICAL_DEFECT

    # 2. Giao sai sản phẩm / sai biến thể (Wrong product / variant)
    wrong_keywords = ['sai sản phẩm', 'sai mẫu', 'sai máy', 'giao nhầm', 'nhầm hàng', 'không đúng hàng']
    if any(k in text for k in wrong_keywords):
        return RETURN_REASON_WRONG_ITEM

    # 3. Sản phẩm không đúng mô tả (Not as described)
    desc_keywords = ['không đúng mô tả', 'sai mô tả', 'khác mô tả', 'không giống quảng cáo']
    if any(k in text for k in desc_keywords):
        return RETURN_REASON_NOT_AS_DESCRIBED

    # 4. Hư hỏng khi nhận hàng (Damaged on arrival)
    damage_keywords = ['bị móp', 'bị vỡ', 'bể', 'trầy xước khi nhận', 'hộp móp', 'hư hỏng lúc giao', 'rách seal']
    if any(k in text for k in damage_keywords):
        return RETURN_REASON_DAMAGED_ON_ARRIVAL

    # 6. Muốn đổi màu sắc hoặc dung lượng (Change color or capacity)
    change_keywords = ['đổi màu', 'đổi dung lượng', 'đổi sang', 'muốn đổi máy', 'đổi bản', '256gb', '128gb', '512gb', '1tb', 'trắng', 'đen', 'titan']
    if any(k in text for k in change_keywords):
        return RETURN_REASON_CHANGE_COLOR_CAPACITY

    # 5. Không phù hợp với nhu cầu (Not suitable)
    not_suitable_keywords = ['không thích', 'không hợp', 'không vừa ý', 'không còn nhu cầu', 'đổi ý']
    if any(k in text for k in not_suitable_keywords):
        return RETURN_REASON_NOT_SUITABLE

    return RETURN_REASON_OTHER


def get_overview_metrics(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tính toán 4 thẻ chỉ số tổng quan (Overview KPI Cards - Section 4):
    1. Tổng doanh thu thuần (Total net revenue)
    2. Tổng sản phẩm bán ra (Total products sold)
    3. Tổng đơn hàng phát sinh (Total orders)
    4. Số biến thể đang quản lý (Number of managed variants)
    Kèm theo tỷ lệ tăng trưởng so với kỳ trước, doanh thu trung bình/ngày,
    tỷ lệ giao hàng thành công và cảnh báo tồn kho thấp.
    """
    # Tập đơn hàng tạo trong kỳ hiện tại
    curr_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
    )
    total_orders = curr_orders.count()

    # Đơn hàng giao thành công trong nhóm này
    curr_delivered_orders = curr_orders.filter(order_status=OrderStatus.DELIVERED)
    delivered_orders_count = curr_delivered_orders.count()

    # Doanh thu gộp từ các đơn giao thành công
    curr_gross_revenue = curr_delivered_orders.aggregate(total=Sum('grand_total'))['total'] or Decimal('0.00')

    # Hoàn tiền đã hoàn tất (Completed refunds) của các đơn giao thành công
    curr_completed_refunds = Refund.objects.filter(
        order__in=curr_delivered_orders,
        status=RefundStatus.COMPLETED,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')

    # Doanh thu thuần = Doanh thu gộp - Hoàn tiền hoàn tất
    curr_net_revenue = max(Decimal('0.00'), curr_gross_revenue - curr_completed_refunds)

    # Số lượng sản phẩm bán ra từ các đơn giao thành công
    curr_delivered_items = OrderItem.objects.filter(order__in=curr_delivered_orders)
    curr_gross_sold = curr_delivered_items.aggregate(total=Sum('quantity'))['total'] or 0

    # Trừ số lượng hàng đã trả hoàn tất (Return Completed)
    curr_returned_qty = ReturnRequest.objects.filter(
        order__in=curr_delivered_orders,
        request_type=ReturnRequestType.RETURN,
        status=ReturnRequestStatus.COMPLETED,
    ).aggregate(total=Sum('quantity'))['total'] or 0
    curr_net_sold = max(0, curr_gross_sold - curr_returned_qty)

    # --- KỲ TRƯỚC (PREVIOUS PERIOD) ---
    prev_orders = Order.objects.filter(
        created_at__gte=date_range['prev_start_dt'],
        created_at__lte=date_range['prev_end_dt'],
    )
    prev_total_orders = prev_orders.count()
    prev_delivered_orders = prev_orders.filter(order_status=OrderStatus.DELIVERED)

    prev_gross_revenue = prev_delivered_orders.aggregate(total=Sum('grand_total'))['total'] or Decimal('0.00')
    prev_completed_refunds = Refund.objects.filter(
        order__in=prev_delivered_orders,
        status=RefundStatus.COMPLETED,
    ).aggregate(total=Sum('amount'))['total'] or Decimal('0.00')
    prev_net_revenue = max(Decimal('0.00'), prev_gross_revenue - prev_completed_refunds)

    prev_delivered_items = OrderItem.objects.filter(order__in=prev_delivered_orders)
    prev_gross_sold = prev_delivered_items.aggregate(total=Sum('quantity'))['total'] or 0
    prev_returned_qty = ReturnRequest.objects.filter(
        order__in=prev_delivered_orders,
        request_type=ReturnRequestType.RETURN,
        status=ReturnRequestStatus.COMPLETED,
    ).aggregate(total=Sum('quantity'))['total'] or 0
    prev_net_sold = max(0, prev_gross_sold - prev_returned_qty)

    # Tỷ lệ giao hàng thành công
    successful_delivery_rate = round(
        (delivered_orders_count / total_orders * 100.0) if total_orders > 0 else 0.0,
        1,
    )

    # Doanh thu trung bình mỗi ngày
    duration = date_range['duration_days']
    avg_daily_revenue = round(float(curr_net_revenue) / duration, 0) if duration > 0 else 0.0

    # Số lượng biến thể quản lý và cảnh báo tồn kho thấp
    managed_variants_count = ProductVariant.objects.filter(
        is_active=True,
        product__is_deleted=False,
    ).count()

    low_stock_variants_count = 0
    for inv in Inventory.objects.select_related('variant', 'variant__product').filter(
        variant__is_active=True,
        variant__product__is_deleted=False,
    ):
        if inv.is_low_stock:
            low_stock_variants_count += 1

    return {
        'total_revenue': curr_net_revenue,
        'gross_revenue': curr_gross_revenue,
        'completed_refunds': curr_completed_refunds,
        'prev_revenue': prev_net_revenue,
        'revenue_growth': calculate_growth(curr_net_revenue, prev_net_revenue),
        'avg_daily_revenue': avg_daily_revenue,
        'total_products_sold': curr_net_sold,
        'gross_products_sold': curr_gross_sold,
        'returned_products_qty': curr_returned_qty,
        'prev_products_sold': prev_net_sold,
        'products_sold_growth': calculate_growth(curr_net_sold, prev_net_sold),
        'total_orders': total_orders,
        'prev_total_orders': prev_total_orders,
        'orders_growth': calculate_growth(total_orders, prev_total_orders),
        'delivered_orders_count': delivered_orders_count,
        'successful_delivery_rate': successful_delivery_rate,
        'managed_variants_count': managed_variants_count,
        'low_stock_variants_count': low_stock_variants_count,
    }


def get_revenue_by_day(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tổng hợp dữ liệu biểu đồ doanh thu theo từng ngày (Section 5 - Left).
    So sánh doanh thu theo từng mốc ngày giữa kỳ hiện tại và kỳ trước.
    Dữ liệu được chuẩn bị sẵn sàng để chuyển thành JSON an toàn cho Chart.js.
    """
    start_date = date_range['start_date']
    end_date = date_range['end_date']
    prev_start_date = date_range['prev_start_date']
    duration_days = date_range['duration_days']

    current_tz = timezone.get_current_timezone()

    labels: List[str] = []
    curr_data: List[float] = []
    prev_data: List[float] = []

    curr_total = Decimal('0.00')
    prev_total = Decimal('0.00')

    # Lấy trước danh sách đơn hàng giao thành công trong cả 2 kỳ để tối ưu truy vấn
    curr_delivered_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
        order_status=OrderStatus.DELIVERED,
    ).prefetch_related('refunds')

    prev_delivered_orders = Order.objects.filter(
        created_at__gte=date_range['prev_start_dt'],
        created_at__lte=date_range['prev_end_dt'],
        order_status=OrderStatus.DELIVERED,
    ).prefetch_related('refunds')

    # Tạo bảng tra cứu theo ngày (localdate)
    curr_daily_map: Dict[date, Decimal] = {}
    for o in curr_delivered_orders:
        d = timezone.localdate(o.created_at)
        ref_amt = sum(
            r.amount for r in o.refunds.all() if r.status == RefundStatus.COMPLETED
        )
        net_amt = max(Decimal('0.00'), o.grand_total - ref_amt)
        curr_daily_map[d] = curr_daily_map.get(d, Decimal('0.00')) + net_amt

    prev_daily_map: Dict[date, Decimal] = {}
    for o in prev_delivered_orders:
        d = timezone.localdate(o.created_at)
        ref_amt = sum(
            r.amount for r in o.refunds.all() if r.status == RefundStatus.COMPLETED
        )
        net_amt = max(Decimal('0.00'), o.grand_total - ref_amt)
        prev_daily_map[d] = prev_daily_map.get(d, Decimal('0.00')) + net_amt

    for i in range(duration_days):
        day_curr = start_date + timedelta(days=i)
        day_prev = prev_start_date + timedelta(days=i)

        rev_curr = curr_daily_map.get(day_curr, Decimal('0.00'))
        rev_prev = prev_daily_map.get(day_prev, Decimal('0.00'))

        curr_total += rev_curr
        prev_total += rev_prev

        if duration_days <= 14:
            label = day_curr.strftime('%d/%m')
        else:
            label = f"N{i+1} ({day_curr.strftime('%d/%m')})"

        labels.append(label)
        curr_data.append(float(rev_curr))
        prev_data.append(float(rev_prev))

    diff_total = curr_total - prev_total
    diff_percent = calculate_growth(curr_total, prev_total)

    return {
        'labels': labels,
        'current_revenues': curr_data,
        'previous_revenues': prev_data,
        'current_total': float(curr_total),
        'previous_total': float(prev_total),
        'difference_total': float(diff_total),
        'difference_percent': diff_percent,
        'period_label': date_range['period_label'],
        'comparison_label': date_range['comparison_label'],
    }


def get_allocated_order_items_net_revenue(delivered_orders_queryset) -> Dict[int, Decimal]:
    """
    Phân bổ doanh thu thuần của từng đơn hàng giao thành công (DELIVERED) cho các OrderItem tương ứng.
    Tuân thủ quy chuẩn kế toán và định nghĩa doanh thu chính thức của Dashboard:
    Net revenue = Doanh thu từ đơn giao thành công - Hoàn tiền hoàn tất (Completed refunds).

    Quy trình phân bổ:
    1. Với từng đơn hàng giao thành công:
       net_order_revenue = max(Decimal("0.00"), order.grand_total - completed_refunds)
    2. Nếu đơn hàng có các OrderItem với tổng line_total > 0:
       - Phân bổ theo tỷ trọng line_total của từng OrderItem.
       - Sử dụng số dư lũy tiến (running balance allocation) và Decimal arithmetic để tránh sai số dấu phẩy động.
       - Mặt hàng cuối cùng nhận toàn bộ giá trị còn lại (remaining balance) để bảo đảm tổng phân bổ
         của các mặt hàng chính xác tuyệt đối bằng net_order_revenue của đơn:
         sum(item_net_revenue) == net_order_revenue.
    3. Nếu order_items_total == 0 hoặc không có line_total:
       - Chia đều an toàn net_order_revenue cho các mặt hàng (mặt hàng cuối nhận phần dư).
    4. Trả về dict ánh xạ {order_item_id: allocated_net_revenue}.
    """
    allocated_map: Dict[int, Decimal] = {}

    if hasattr(delivered_orders_queryset, 'prefetch_related'):
        orders = delivered_orders_queryset.prefetch_related('refunds', 'items')
    else:
        orders = delivered_orders_queryset

    for order in orders:
        completed_refunds = sum(
            (r.amount or Decimal('0.00')) for r in order.refunds.all() if r.status == RefundStatus.COMPLETED
        )
        order_gt = order.grand_total or Decimal('0.00')
        net_order_revenue = max(Decimal('0.00'), order_gt - completed_refunds)

        items = list(order.items.all())
        if not items:
            continue

        order_items_total = sum((it.line_total or Decimal('0.00')) for it in items)
        rem_rev = net_order_revenue
        rem_weight = order_items_total

        for idx, it in enumerate(items):
            if idx == len(items) - 1:
                item_net = rem_rev
            else:
                w = it.line_total or Decimal('0.00')
                if rem_weight > Decimal('0.00'):
                    item_net = ((w / rem_weight) * rem_rev).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                else:
                    rem_items = len(items) - idx
                    item_net = (rem_rev / Decimal(rem_items)).quantize(Decimal('0.01'), rounding=ROUND_HALF_UP)
                rem_rev -= item_net
                rem_weight -= w
            allocated_map[it.id] = item_net

    return allocated_map


def get_category_revenue(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Tổng hợp tỷ trọng doanh thu thuần theo danh mục sản phẩm (Section 5 - Right).
    Tính trên các đơn hàng giao thành công, phản ánh doanh thu thuần sau khi trừ hoàn tiền hoàn tất.
    """
    curr_delivered_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
        order_status=OrderStatus.DELIVERED,
    )

    allocated_net_rev_map = get_allocated_order_items_net_revenue(curr_delivered_orders)

    items = OrderItem.objects.filter(
        order__in=curr_delivered_orders,
    ).select_related('variant__product__category')

    category_map: Dict[str, Dict[str, Any]] = {}
    total_net_revenue = Decimal('0.00')

    for it in items:
        cat_name = 'Chưa phân loại'
        try:
            if it.variant and it.variant.product and it.variant.product.category:
                cat_name = it.variant.product.category.name
        except Exception:
            pass

        if cat_name not in category_map:
            category_map[cat_name] = {
                'name': cat_name,
                'revenue': Decimal('0.00'),
                'quantity': 0,
            }

        line_net_revenue = allocated_net_rev_map.get(it.id, Decimal('0.00'))
        category_map[cat_name]['revenue'] += line_net_revenue
        category_map[cat_name]['quantity'] += (it.quantity or 0)
        total_net_revenue += line_net_revenue

    # Tính tỷ lệ phần trăm
    categories_list = []
    for cat_name, info in sorted(category_map.items(), key=lambda x: x[1]['revenue'], reverse=True):
        rev = info['revenue']
        pct = round((float(rev) / float(total_net_revenue) * 100.0) if total_net_revenue > 0 else 0.0, 1)
        categories_list.append({
            'name': cat_name,
            'revenue': rev,
            'revenue_float': float(rev),
            'quantity': info['quantity'],
            'percentage': pct,
        })

    return {
        'total_revenue': total_net_revenue,
        'total_revenue_float': float(total_net_revenue),
        'categories': categories_list,
        'labels': [c['name'] for c in categories_list],
        'revenues': [c['revenue_float'] for c in categories_list],
        'percentages': [c['percentage'] for c in categories_list],
    }


def get_product_metrics(date_range: Dict[str, Any], limit: int = 10) -> Dict[str, Any]:
    """
    Thống kê hiệu quả kinh doanh của sản phẩm và biến thể (Section 6).
    Số lượng bán chỉ tính trên các đơn hàng giao thành công (DELIVERED).
    Cung cấp số lượng gộp (Gross) và số lượng thuần (Net = Gross - Hoàn trả).
    """
    total_active_products = Product.objects.filter(is_active=True, is_deleted=False).count()
    total_managed_variants = ProductVariant.objects.filter(is_active=True, product__is_deleted=False).count()

    # Nhóm đơn hàng giao thành công kỳ hiện tại
    curr_delivered_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
        order_status=OrderStatus.DELIVERED,
    )

    # Đơn hàng giao thành công kỳ trước (để so sánh tăng trưởng)
    prev_delivered_orders = Order.objects.filter(
        created_at__gte=date_range['prev_start_dt'],
        created_at__lte=date_range['prev_end_dt'],
        order_status=OrderStatus.DELIVERED,
    )

    # Tổng hợp bán kỳ trước theo variant_id
    prev_variant_sales: Dict[int, int] = {}
    for row in (
        OrderItem.objects.filter(order__in=prev_delivered_orders)
        .values('variant_id')
        .annotate(total_qty=Sum('quantity'))
    ):
        prev_variant_sales[row['variant_id']] = row['total_qty']

    # Tổng hợp hoàn trả đã hoàn tất kỳ hiện tại theo variant_id
    returned_qty_map: Dict[int, int] = {}
    for rr in (
        ReturnRequest.objects.filter(
            order__in=curr_delivered_orders,
            request_type=ReturnRequestType.RETURN,
            status=ReturnRequestStatus.COMPLETED,
        )
        .values('order_item__variant_id')
        .annotate(total_ret=Sum('quantity'))
    ):
        vid = rr['order_item__variant_id']
        if vid:
            returned_qty_map[vid] = rr['total_ret']

    # Lấy phân bổ doanh thu thuần cho từng OrderItem của các đơn giao thành công
    allocated_net_rev_map = get_allocated_order_items_net_revenue(curr_delivered_orders)

    # Tổng hợp bán kỳ hiện tại theo variant
    variant_sales_map: Dict[int, Dict[str, Any]] = {}
    for it in (
        OrderItem.objects.filter(order__in=curr_delivered_orders)
        .select_related('variant', 'variant__product')
    ):
        vid = it.variant_id
        if vid not in variant_sales_map:
            v_obj = it.variant
            sku = it.sku_snapshot or (v_obj.sku if v_obj else 'N/A')
            p_name = it.product_name_snapshot or (v_obj.product.name if v_obj and v_obj.product else 'Sản phẩm')
            v_attrs = it.variant_snapshot or (v_obj.attribute_summary if v_obj else 'Mặc định')

            variant_sales_map[vid] = {
                'variant_id': vid,
                'sku': sku,
                'product_name': p_name,
                'attributes': v_attrs,
                'gross_quantity': 0,
                'returned_quantity': returned_qty_map.get(vid, 0),
                'net_quantity': 0,
                'revenue': Decimal('0.00'),
            }

        variant_sales_map[vid]['gross_quantity'] += it.quantity
        variant_sales_map[vid]['revenue'] += allocated_net_rev_map.get(it.id, Decimal('0.00'))

    for vid, item in variant_sales_map.items():
        item['net_quantity'] = max(0, item['gross_quantity'] - item['returned_quantity'])
        prev_qty = prev_variant_sales.get(vid, 0)
        item['prev_quantity'] = prev_qty
        item['growth_percent'] = calculate_growth(item['net_quantity'], prev_qty)

    all_sold_variants = list(variant_sales_map.values())

    # Top bán chạy (Best-selling): Xếp theo số lượng bán thuần giảm dần
    best_selling = sorted(all_sold_variants, key=lambda x: (x['net_quantity'], x['revenue']), reverse=True)[:limit]
    for idx, item in enumerate(best_selling, start=1):
        item['rank'] = idx

    # Top bán chậm (Slowest-selling):
    # Bao gồm cả các biến thể đang kinh doanh nhưng phát sinh 0 đơn hoặc ít đơn nhất
    managed_variants = ProductVariant.objects.filter(
        is_active=True,
        product__is_deleted=False,
    ).select_related('product')

    full_variant_list = []
    for var in managed_variants:
        if var.id in variant_sales_map:
            # Dùng bản sao độc lập: hạng của bảng bán chậm không được ghi đè
            # lên hạng đã tính cho bảng bán chạy cùng biến thể.
            full_variant_list.append(dict(variant_sales_map[var.id]))
        else:
            full_variant_list.append({
                'variant_id': var.id,
                'sku': var.sku,
                'product_name': var.product.name,
                'attributes': var.attribute_summary,
                'gross_quantity': 0,
                'returned_quantity': 0,
                'net_quantity': 0,
                'prev_quantity': prev_variant_sales.get(var.id, 0),
                'growth_percent': 0.0,
                'revenue': Decimal('0.00'),
            })

    slowest_selling = sorted(full_variant_list, key=lambda x: (x['net_quantity'], x['revenue']))[:limit]
    for idx, item in enumerate(slowest_selling, start=1):
        item['rank'] = idx

    return {
        'total_active_products': total_active_products,
        'total_managed_variants': total_managed_variants,
        'best_selling': best_selling,
        'slowest_selling': slowest_selling,
    }


def get_order_metrics(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thống kê toàn diện trạng thái đơn hàng (Section 7 - Order Dashboard).
    Tuân thủ phân biệt giữa trạng thái đơn hàng (order_status),
    và trạng thái thanh toán (payment.status).
    """
    orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
    )
    total_orders = orders.count()

    status_counts = orders.values('order_status').annotate(cnt=Count('id'))
    status_map = {row['order_status']: row['cnt'] for row in status_counts}

    pending_confirmation = status_map.get(OrderStatus.PENDING_CONFIRMATION, 0)
    confirmed = status_map.get(OrderStatus.CONFIRMED, 0)
    shipping = status_map.get(OrderStatus.SHIPPING, 0)
    delivered = status_map.get(OrderStatus.DELIVERED, 0)
    cancelled = status_map.get(OrderStatus.CANCELLED, 0)

    # Đơn hàng thất bại sau xuất kho
    delivery_failed = (
        status_map.get(OrderStatus.DELIVERY_FAILED, 0)
        + status_map.get(OrderStatus.RETURNING_TO_WAREHOUSE, 0)
        + status_map.get(OrderStatus.FAILED_CLOSED, 0)
    )

    delivery_success_rate = round(
        (delivered / total_orders * 100.0) if total_orders > 0 else 0.0,
        1,
    )

    return {
        'total_orders': total_orders,
        'pending_confirmation': pending_confirmation,
        'confirmed': confirmed,
        'shipping': shipping,
        'delivered': delivered,
        'cancelled': cancelled,
        'delivery_failed': delivery_failed,
        'delivery_success_rate': delivery_success_rate,
    }


def get_inventory_metrics() -> Dict[str, Any]:
    """
    Thống kê tình hình kho hàng hiện tại (Section 8 - Inventory Dashboard).
    Sử dụng trực tiếp Inventory model gốc, không chỉnh sửa số lượng tồn kho.
    Công thức: Tổng tồn thực tế = Tồn đã giữ + Tồn có thể bán (Available).
    Cảnh báo:
    - Hết hàng (Out of stock): available <= 0
    - Sắp hết (Low stock): 0 < available <= min_threshold
    - Không tính trùng biến thể hết hàng vào nhóm sắp hết hàng.
    - Sắp xếp biến thể hết hàng lên trước, sau đó đến sắp hết hàng.
    """
    inventories = Inventory.objects.select_related('variant', 'variant__product').all()

    total_physical = 0
    total_reserved = 0
    low_stock_count = 0
    out_of_stock_count = 0
    warning_variants: List[Dict[str, Any]] = []

    for inv in inventories:
        p = inv.physical_sellable if inv.physical_sellable is not None else 0
        r = inv.reserved if inv.reserved is not None else 0
        avail = inv.available
        thresh = inv.min_threshold if inv.min_threshold is not None else 0

        total_physical += p
        total_reserved += r

        is_oos = (avail <= 0)
        is_low = (0 < avail <= thresh)

        if is_oos:
            out_of_stock_count += 1
            status_label = 'Hết hàng'
            status_badge_class = 'badge-danger'
        elif is_low:
            low_stock_count += 1
            status_label = 'Sắp hết'
            status_badge_class = 'badge-warning'
        else:
            status_label = ''
            status_badge_class = ''

        if is_oos or is_low:
            var_name = inv.variant.product.name if inv.variant and inv.variant.product else 'Sản phẩm'
            sku = inv.variant.sku if inv.variant else 'N/A'
            attrs = inv.variant.attribute_summary if inv.variant else ''
            suggested_qty = max(10, (thresh * 2) - avail) if avail <= 0 else max(5, thresh - avail + 5)

            warning_variants.append({
                'inventory_id': inv.id,
                'product_name': var_name,
                'sku': sku,
                'attributes': attrs,
                'physical_sellable': p,
                'reserved': r,
                'available': avail,
                'min_threshold': thresh,
                'suggested_quantity': suggested_qty,
                'status_label': status_label,
                'status_text': status_label,
                'status_badge_class': status_badge_class,
                'status_class': status_badge_class,
                'is_out_of_stock': is_oos,
            })

    # Sắp xếp danh sách cảnh báo: Hết hàng (is_out_of_stock=True) lên trước, sau đó đến sắp hết
    warning_variants.sort(key=lambda x: (0 if x['is_out_of_stock'] else 1, x['available'], -x['min_threshold']))

    # Tổng tồn thực tế = Tồn đã giữ + Tồn có thể bán (Available)
    total_available = total_physical - total_reserved
    total_warning_count = out_of_stock_count + low_stock_count

    return {
        'total_physical': total_physical,
        'total_reserved': total_reserved,
        'total_available': total_available,
        'low_stock_count': low_stock_count,
        'out_of_stock_count': out_of_stock_count,
        'total_warning_count': total_warning_count,
        'restock_needed_count': total_warning_count,
        'warning_variants': warning_variants,
        'restock_items': warning_variants,
    }


def get_cancellation_metrics(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thống kê chi tiết tình trạng hủy đơn (Section 9 - Cancellation Dashboard).
    Công thức:
    Tỷ lệ hủy = Tổng đơn hủy / Tổng đơn tạo trong kỳ × 100%
    """
    cohort_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
    )
    total_orders = cohort_orders.count()

    # Đơn hủy thuộc nhóm cohort này
    cancelled_orders = cohort_orders.filter(order_status=OrderStatus.CANCELLED)
    customer_cancelled_count = cancelled_orders.count()

    total_cancelled = customer_cancelled_count

    cancellation_rate = round(
        (total_cancelled / total_orders * 100.0) if total_orders > 0 else 0.0,
        1,
    )

    # Thống kê lý do hủy
    reason_counts: Dict[str, int] = {}
    reason_labels: Dict[str, str] = {
        CancellationReasonCode.WRONG_ORDER: 'Đặt nhầm sản phẩm',
        CancellationReasonCode.CHANGE_VARIANT: 'Muốn đổi sản phẩm hoặc biến thể khác',
        CancellationReasonCode.FOUND_BETTER: 'Tìm thấy sản phẩm / giá phù hợp hơn',
        CancellationReasonCode.NO_LONGER_NEEDED: 'Không còn nhu cầu mua hàng',
        CancellationReasonCode.SLOW_PROCESSING: 'Thời gian xử lý / xác nhận lâu',
        CancellationReasonCode.OTHER: 'Lý do khác',
    }

    # Các lý do từ OrderCancellation
    for canc in (
        cancelled_orders.filter(cancellation__isnull=False)
        .values('cancellation__reason_code')
        .annotate(cnt=Count('id'))
    ):
        code = canc['cancellation__reason_code']
        reason_counts[code] = reason_counts.get(code, 0) + canc['cnt']

    # Nếu có đơn hủy mà chưa có bản ghi cancellation chi tiết, đưa vào Khác
    recorded_customer_cancellations = sum(reason_counts.values())
    unrecorded_diff = customer_cancelled_count - recorded_customer_cancellations
    if unrecorded_diff > 0:
        reason_counts[CancellationReasonCode.OTHER] = reason_counts.get(CancellationReasonCode.OTHER, 0) + unrecorded_diff

    # Xếp hạng lý do hủy giảm dần
    cancellation_colors = ['#E62E2D', '#1F2A24', '#F59E0B', '#6B7280', '#9CA3AF']
    reasons_breakdown = []
    for idx, (code, cnt) in enumerate(sorted(reason_counts.items(), key=lambda x: x[1], reverse=True)):
        share = round((cnt / total_cancelled * 100.0) if total_cancelled > 0 else 0.0, 1)
        clamped_share = max(0.0, min(100.0, share))
        color = cancellation_colors[idx] if idx < len(cancellation_colors) else '#9CA3AF'
        reasons_breakdown.append({
            'code': code,
            'label': reason_labels.get(code, code),
            'name': reason_labels.get(code, code),
            'count': cnt,
            'percentage': clamped_share,
            'color': color,
            'color_index': idx,
        })

    return {
        'total_orders': total_orders,
        'total_cancelled': total_cancelled,
        'cancellation_rate': cancellation_rate,
        'customer_cancelled_count': customer_cancelled_count,
        'reasons_breakdown': reasons_breakdown,
    }


def get_return_exchange_metrics(date_range: Dict[str, Any]) -> Dict[str, Any]:
    """
    Thống kê Đổi hàng và Trả hàng (Section 10 & 11).
    Công thức:
    Tỷ lệ yêu cầu đổi hàng = Đơn giao thành công có >= 1 yêu cầu đổi / Tổng đơn giao thành công × 100%
    Tỷ lệ yêu cầu trả hàng = Đơn giao thành công có >= 1 yêu cầu trả / Tổng đơn giao thành công × 100%
    Phân loại theo 7 nhóm lý do chuẩn và hỗ trợ Admin xem chi tiết mô tả 'Lý do khác'.
    """
    # Tập đơn hàng giao thành công trong kỳ (Mẫu số chung)
    delivered_orders = Order.objects.filter(
        created_at__gte=date_range['start_dt'],
        created_at__lte=date_range['end_dt'],
        order_status=OrderStatus.DELIVERED,
    )
    delivered_orders_count = delivered_orders.count()

    # Yêu cầu đổi trả gắn với tập đơn hàng giao thành công trong kỳ
    requests_on_cohort = ReturnRequest.objects.filter(order__in=delivered_orders).select_related(
        'order', 'order_item', 'customer', 'replacement_variant'
    )

    exchange_order_ids = set(
        requests_on_cohort.filter(request_type=ReturnRequestType.EXCHANGE)
        .values_list('order_id', flat=True)
    )
    return_order_ids = set(
        requests_on_cohort.filter(request_type=ReturnRequestType.RETURN)
        .values_list('order_id', flat=True)
    )

    exchange_request_rate = round(
        (len(exchange_order_ids) / delivered_orders_count * 100.0) if delivered_orders_count > 0 else 0.0,
        1,
    )
    return_request_rate = round(
        (len(return_order_ids) / delivered_orders_count * 100.0) if delivered_orders_count > 0 else 0.0,
        1,
    )

    total_exchange_requests = requests_on_cohort.filter(request_type=ReturnRequestType.EXCHANGE).count()
    total_return_requests = requests_on_cohort.filter(request_type=ReturnRequestType.RETURN).count()

    # Trạng thái xử lý yêu cầu Đổi hàng
    exchange_status_counts = (
        requests_on_cohort.filter(request_type=ReturnRequestType.EXCHANGE)
        .values('status')
        .annotate(cnt=Count('id'))
    )
    ex_status_map = {r['status']: r['cnt'] for r in exchange_status_counts}

    exchange_pending = ex_status_map.get(ReturnRequestStatus.PENDING, 0)
    exchange_approved = ex_status_map.get(ReturnRequestStatus.APPROVED, 0)
    exchange_rejected = ex_status_map.get(ReturnRequestStatus.REJECTED, 0)
    exchange_completed = ex_status_map.get(ReturnRequestStatus.COMPLETED, 0) + ex_status_map.get(ReturnRequestStatus.RECEIVED, 0)

    exchange_pending_pct = max(0.0, min(100.0, round((exchange_pending / total_exchange_requests * 100.0) if total_exchange_requests > 0 else 0.0, 1)))
    exchange_approved_pct = max(0.0, min(100.0, round((exchange_approved / total_exchange_requests * 100.0) if total_exchange_requests > 0 else 0.0, 1)))
    exchange_rejected_pct = max(0.0, min(100.0, round((exchange_rejected / total_exchange_requests * 100.0) if total_exchange_requests > 0 else 0.0, 1)))
    exchange_completed_pct = max(0.0, min(100.0, round((exchange_completed / total_exchange_requests * 100.0) if total_exchange_requests > 0 else 0.0, 1)))

    # Trạng thái xử lý yêu cầu Trả hàng
    return_status_counts = (
        requests_on_cohort.filter(request_type=ReturnRequestType.RETURN)
        .values('status')
        .annotate(cnt=Count('id'))
    )
    ret_status_map = {r['status']: r['cnt'] for r in return_status_counts}

    return_pending = ret_status_map.get(ReturnRequestStatus.PENDING, 0)
    return_approved = ret_status_map.get(ReturnRequestStatus.APPROVED, 0)
    return_rejected = ret_status_map.get(ReturnRequestStatus.REJECTED, 0)
    return_completed = ret_status_map.get(ReturnRequestStatus.COMPLETED, 0) + ret_status_map.get(ReturnRequestStatus.RECEIVED, 0)

    return_pending_pct = max(0.0, min(100.0, round((return_pending / total_return_requests * 100.0) if total_return_requests > 0 else 0.0, 1)))
    return_approved_pct = max(0.0, min(100.0, round((return_approved / total_return_requests * 100.0) if total_return_requests > 0 else 0.0, 1)))
    return_rejected_pct = max(0.0, min(100.0, round((return_rejected / total_return_requests * 100.0) if total_return_requests > 0 else 0.0, 1)))
    return_completed_pct = max(0.0, min(100.0, round((return_completed / total_return_requests * 100.0) if total_return_requests > 0 else 0.0, 1)))

    # 4 Giai đoạn xử lý Đổi / Trả chuẩn (Section 12)
    processing_stages = [
        {
            'step': 1,
            'name': 'Chờ duyệt tiếp nhận',
            'label': 'Chờ duyệt tiếp nhận',
            'stage_name': 'Chờ duyệt tiếp nhận',
            'badge_label': 'Chờ duyệt',
            'color': '#F59E0B',
            'bg_color': '#F3F4F6',
            'exchange_color': '#F59E0B',
            'return_color': '#F59E0B',
            'exchange_count': exchange_pending,
            'exchange_pct': exchange_pending_pct,
            'exchange_percentage': exchange_pending_pct,
            'return_count': return_pending,
            'return_pct': return_pending_pct,
            'return_percentage': return_pending_pct,
        },
        {
            'step': 2,
            'name': 'Đã được duyệt - Chờ nhận hàng',
            'label': 'Đã được duyệt - Chờ nhận hàng',
            'stage_name': 'Đã được duyệt - Chờ nhận hàng',
            'badge_label': 'Được duyệt',
            'color': '#2563EB',
            'bg_color': '#F3F4F6',
            'exchange_color': '#2563EB',
            'return_color': '#2563EB',
            'exchange_count': exchange_approved,
            'exchange_pct': exchange_approved_pct,
            'exchange_percentage': exchange_approved_pct,
            'return_count': return_approved,
            'return_pct': return_approved_pct,
            'return_percentage': return_approved_pct,
        },
        {
            'step': 3,
            'name': 'Bị từ chối',
            'label': 'Bị từ chối',
            'stage_name': 'Bị từ chối',
            'badge_label': 'Bị từ chối',
            'color': '#E62E2D',
            'bg_color': '#FEF2F2',
            'exchange_color': '#E62E2D',
            'return_color': '#E62E2D',
            'exchange_count': exchange_rejected,
            'exchange_pct': exchange_rejected_pct,
            'exchange_percentage': exchange_rejected_pct,
            'return_count': return_rejected,
            'return_pct': return_rejected_pct,
            'return_percentage': return_rejected_pct,
        },
        {
            'step': 4,
            'name': 'Đã hoàn tất xử lý',
            'label': 'Đã hoàn tất xử lý',
            'stage_name': 'Đã hoàn tất xử lý',
            'badge_label': 'Hoàn tất',
            'color': '#059669',
            'bg_color': '#ECFDF5',
            'exchange_color': '#059669',
            'return_color': '#059669',
            'exchange_count': exchange_completed,
            'exchange_pct': exchange_completed_pct,
            'exchange_percentage': exchange_completed_pct,
            'return_count': return_completed,
            'return_pct': return_completed_pct,
            'return_percentage': return_completed_pct,
        },
    ]

    # Phân loại theo 7 nhóm lý do đổi trả chuẩn (Section 11)
    # Nguồn dữ liệu chuẩn: reason_code trên model ReturnRequest (không phụ thuộc keyword text)
    # Hỗ trợ fallback phân loại nếu bản ghi cũ chưa có reason_code.
    reasons_stat: Dict[str, int] = {k: 0 for k in RETURN_REASON_LABELS.keys()}
    other_requests_details: List[Dict[str, Any]] = []

    total_requests_count = requests_on_cohort.count()

    for req in requests_on_cohort:
        group_key = req.reason_code if (req.reason_code and req.reason_code in RETURN_REASON_LABELS) else None
        if not group_key:
            group_key = classify_return_reason(req.description) if req.description else ReturnReasonCode.OTHER
        if group_key not in reasons_stat:
            group_key = ReturnReasonCode.OTHER

        reasons_stat[group_key] += 1

        # Ghi nhận chi tiết phục vụ Admin xem diễn giải cho nhóm 'Khác' và các trường hợp cụ thể
        other_requests_details.append({
            'id': req.id,
            'order_code': req.order.order_code,
            'customer_username': req.customer.username if req.customer else 'Khách',
            'request_type': req.get_request_type_display(),
            'product_name': req.order_item.product_name_snapshot if req.order_item else 'N/A',
            'reason_code': group_key,
            'category_group': RETURN_REASON_LABELS.get(group_key, 'Lý do khác'),
            # Giữ khóa `reason` để template/báo cáo cũ tương thích, dữ liệu lấy từ
            # trường mô tả chuẩn của ReturnRequest.
            'reason': req.description,
            'status': req.get_status_display(),
            'status_raw': req.status,
            'created_at': req.created_at,
        })

    RETURN_REASON_COLORS = ['#E62E2D', '#1F2A24', '#F59E0B', '#2563EB', '#6B7280', '#9CA3AF', '#9CA3AF']
    reasons_breakdown = []
    for code, label in RETURN_REASON_LABELS.items():
        cnt = reasons_stat.get(code, 0)
        pct = max(0.0, min(100.0, round((cnt / total_requests_count * 100.0) if total_requests_count > 0 else 0.0, 1)))
        reasons_breakdown.append({
            'code': code,
            'label': label,
            'name': label,
            'count': cnt,
            'percentage': pct,
        })

    reasons_breakdown.sort(key=lambda x: x['count'], reverse=True)
    for idx, r in enumerate(reasons_breakdown):
        r['color'] = RETURN_REASON_COLORS[idx] if idx < len(RETURN_REASON_COLORS) else '#9CA3AF'
        r['color_index'] = idx

    return {
        'delivered_orders_count': delivered_orders_count,
        'exchange_orders_count': len(exchange_order_ids),
        'return_orders_count': len(return_order_ids),
        'exchange_request_rate': exchange_request_rate,
        'return_request_rate': return_request_rate,
        'total_exchange_requests': total_exchange_requests,
        'total_return_requests': total_return_requests,
        'exchange_pending': exchange_pending,
        'exchange_approved': exchange_approved,
        'exchange_rejected': exchange_rejected,
        'exchange_completed': exchange_completed,
        'return_pending': return_pending,
        'return_approved': return_approved,
        'return_rejected': return_rejected,
        'return_completed': return_completed,
        'processing_stages': processing_stages,
        'reasons_breakdown': reasons_breakdown,
        'other_requests_details': other_requests_details,
    }


def export_inventory_csv() -> str:
    """
    Xuất danh sách tồn kho toàn hệ thống ra chuỗi định dạng CSV chuẩn UTF-8-sig.
    Dữ liệu được trích xuất hoàn toàn từ cơ sở dữ liệu thật, không gán cứng.
    """
    output = io.StringIO()
    writer = csv.writer(output, delimiter=',', quoting=csv.QUOTE_MINIMAL)

    # Tiêu đề cột
    writer.writerow([
        'STT',
        'Mã SKU',
        'Tên sản phẩm',
        'Biến thể / Thuộc tính',
        'Tồn thực tế',
        'Tồn đã giữ',
        'Tồn khả dụng',
        'Ngưỡng tối thiểu',
        'Trạng thái tồn kho',
        'Gợi ý nhập thêm',
        'Cập nhật gần nhất',
    ])

    inventories = Inventory.objects.select_related('variant', 'variant__product').order_by('id')

    for idx, inv in enumerate(inventories, start=1):
        sku = inv.variant.sku if inv.variant else 'N/A'
        p_name = inv.variant.product.name if inv.variant and inv.variant.product else 'Sản phẩm'
        attrs = inv.variant.attribute_summary if inv.variant else 'Mặc định'
        p = inv.physical_sellable or 0
        r = inv.reserved or 0
        avail = inv.available
        thresh = inv.min_threshold or 0

        status = 'Hết hàng' if inv.is_out_of_stock else ('Cần nhập hàng' if inv.is_low_stock else 'Đủ hàng')
        suggested = max(10, (thresh * 2) - avail) if avail <= 0 else (max(5, thresh - avail + 5) if inv.is_low_stock else 0)

        updated_str = inv.updated_at.strftime('%d/%m/%Y %H:%M') if inv.updated_at else ''

        writer.writerow([
            idx,
            sku,
            p_name,
            attrs,
            p,
            r,
            avail,
            thresh,
            status,
            suggested,
            updated_str,
        ])

    return output.getvalue()
