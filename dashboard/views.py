"""
Views cho Phân hệ Dashboard Thống kê & Báo cáo Kinh doanh — Quốc Hùng Mobile.
Chỉ cho phép tài khoản có vai trò Quản trị viên (Admin - ACT-04) truy cập.
Kiểm tra quyền chặt chẽ tại tầng Backend thông qua decorator @role_required(ROLE_ADMIN).
"""

from datetime import datetime
import json
from django.contrib import messages
from django.core.serializers.json import DjangoJSONEncoder
from django.http import HttpResponse
from django.shortcuts import render
from django.utils import timezone
from django.views.decorators.http import require_GET

from accounts.permissions import ROLE_ADMIN, role_required
from dashboard.services import (
    export_inventory_csv,
    get_cancellation_metrics,
    get_category_revenue,
    get_date_range,
    get_inventory_metrics,
    get_order_metrics,
    get_overview_metrics,
    get_product_metrics,
    get_return_exchange_metrics,
    get_revenue_by_day,
)


@role_required(ROLE_ADMIN)
@require_GET
def dashboard_overview_view(request):
    """
    Trang chính Dashboard Thống kê & Báo cáo Kinh doanh (Section 4–11).
    Hỗ trợ lọc theo thời gian: Hôm nay, 7 ngày qua, Tháng này, Tùy chỉnh (custom).
    So sánh đối chiếu tự động với kỳ trước có cùng số ngày.
    """
    # 1. Đọc và làm sạch tham số lọc thời gian từ GET
    period = request.GET.get('period', 'this_month').strip()
    start_date_str = request.GET.get('start_date', '').strip()
    end_date_str = request.GET.get('end_date', '').strip()
    compare_to = request.GET.get('compare_to', 'previous_period').strip()
    product_view = request.GET.get('product_view', 'best_selling').strip()  # 'best_selling' or 'slowest_selling'
    quantity_type = request.GET.get('quantity_type', 'net').strip()  # 'net' or 'gross'

    # 2. Xác định phạm vi ngày và kỳ so sánh
    date_range = get_date_range(
        period=period,
        start_date_str=start_date_str,
        end_date_str=end_date_str,
        compare_to=compare_to,
    )

    # 3. Gọi các dịch vụ tổng hợp số liệu kinh doanh từ CSDL
    overview_metrics = get_overview_metrics(date_range)
    revenue_by_day = get_revenue_by_day(date_range)
    category_revenue = get_category_revenue(date_range)
    product_metrics = get_product_metrics(date_range, limit=10)
    order_metrics = get_order_metrics(date_range)
    inventory_metrics = get_inventory_metrics()
    cancellation_metrics = get_cancellation_metrics(date_range)
    return_exchange_metrics = get_return_exchange_metrics(date_range)

    # 4. Đóng gói dữ liệu biểu đồ sang chuỗi JSON an toàn
    revenue_by_day_json = json.dumps(revenue_by_day, cls=DjangoJSONEncoder)
    category_revenue_json = json.dumps(category_revenue, cls=DjangoJSONEncoder)

    context = {
        'page_title': 'Dashboard Thống kê & Báo cáo Kinh doanh',
        'active_nav': 'dashboard',
        'module_badge': 'Báo cáo & Thống kê',
        'date_range': date_range,
        'current_period': date_range['period'],
        'start_date_val': date_range['start_date_str'],
        'end_date_val': date_range['end_date_str'],
        'compare_to': compare_to,
        'product_view': product_view,
        'quantity_type': quantity_type,
        'overview': overview_metrics,
        'revenue_by_day': revenue_by_day,
        'revenue_by_day_json': revenue_by_day_json,
        'category_revenue': category_revenue,
        'category_revenue_json': category_revenue_json,
        'product_metrics': product_metrics,
        'order_metrics': order_metrics,
        'inventory_metrics': inventory_metrics,
        'cancellation_metrics': cancellation_metrics,
        'return_exchange_metrics': return_exchange_metrics,
    }

    return render(request, 'dashboard/dashboard.html', context)


@role_required(ROLE_ADMIN)
@require_GET
def inventory_export_csv_view(request):
    """
    Xuất danh sách tồn kho toàn hệ thống ra file CSV (Section 8).
    Chỉ cho phép Quản trị viên (Admin) tải file.
    """
    csv_content = export_inventory_csv()
    filename = f"qhm_ton_kho_{timezone.localdate().strftime('%Y%m%d')}.csv"

    # Thêm BOM UTF-8 (\ufeff) để Excel hiển thị tiếng Việt chính xác
    response = HttpResponse(
        '\ufeff' + csv_content,
        content_type='text/csv; charset=utf-8-sig',
    )
    response['Content-Disposition'] = f'attachment; filename="{filename}"'
    return response
