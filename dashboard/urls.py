"""
Định tuyến URL cho Phân hệ Dashboard Thống kê & Báo cáo Kinh doanh — Quốc Hùng Mobile.
"""

from django.urls import path
from dashboard.views import dashboard_overview_view, inventory_export_csv_view

app_name = 'dashboard'

urlpatterns = [
    # Tuyến đường chính: /management/dashboard/
    path('', dashboard_overview_view, name='index'),
    # Tuyến đường xuất CSV tồn kho: /management/dashboard/inventory/export-csv/
    path('inventory/export-csv/', inventory_export_csv_view, name='inventory_export_csv'),
]
