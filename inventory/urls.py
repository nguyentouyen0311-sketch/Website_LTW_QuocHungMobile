"""
Định tuyến URL cho phân hệ Quản lý Kho hàng (Inventory URLs) — Quốc Hùng Mobile.
"""
from django.urls import path
from inventory.views import (
    inventory_list_view,
    inventory_detail_view,
    inventory_update_threshold_view,
    inventory_receive_view,
    inventory_adjust_view,
    inventory_transaction_list_view,
    returned_stock_list_view,
    confirm_receipt_action_view,
    inspect_condition_view,
    restock_returned_product_action_view,
)

app_name = 'inventory'

urlpatterns = [
    # UI-29: Danh sách tồn kho và cảnh báo
    path('', inventory_list_view, name='inventory_list'),
    # UI-30: Sổ cái lịch sử biến động kho toàn hệ thống
    path('history/', inventory_transaction_list_view, name='inventory_history'),
    # Hàng đổi trả trong kho (Requirement 1, 2, 5, 6)
    path('returns/', returned_stock_list_view, name='returned_stock_list'),
    path('returns/requests/<int:pk>/confirm-receipt/', confirm_receipt_action_view, name='confirm_receipt'),
    path('returns/<int:pk>/inspect/', inspect_condition_view, name='inspect_condition'),
    path('returns/<int:pk>/restock/', restock_returned_product_action_view, name='restock_returned_product'),
    # UI-30: Chi tiết tồn kho biến thể và thiết lập ngưỡng cảnh báo
    path('<int:inventory_id>/', inventory_detail_view, name='inventory_detail'),
    # UI-31: Nhập hàng vào kho
    path('<int:inventory_id>/receive/', inventory_receive_view, name='inventory_receive'),
    # UI-32: Kiểm kê và điều chỉnh tồn kho
    path('<int:inventory_id>/adjust/', inventory_adjust_view, name='inventory_adjust'),
    # Đột biến ngưỡng cảnh báo (POST Only)
    path('<int:inventory_id>/threshold/', inventory_update_threshold_view, name='inventory_update_threshold'),
]
