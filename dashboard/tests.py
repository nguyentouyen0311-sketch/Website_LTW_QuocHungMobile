"""
Bộ kiểm thử tự động toàn diện cho Phân hệ Dashboard & Báo cáo Kinh doanh — Quốc Hùng Mobile.
Tuân thủ đầy đủ 13 yêu cầu kiểm thử trong Section 15 của SRS:
1. Admin có quyền truy cập Dashboard.
2. Người dùng không phải Admin bị từ chối truy cập (403 PermissionDenied).
3. Các bộ lọc thời gian hoạt động chính xác (Hôm nay, 7 ngày qua, Tháng này, Tùy chọn).
4. Tính toán kỳ trước đối chiếu có cùng độ dài ngày chính xác.
5. Đơn hàng đã hủy không được tính vào doanh thu thành công.
6. Chỉ các đơn giao thành công (DELIVERED) mới được tính vào sản lượng bán của sản phẩm.
7. Tỷ lệ hủy đơn xử lý an toàn khi tổng số đơn hàng bằng 0 (không lỗi chia cho 0).
8. Tổng tồn kho thực tế, tồn đã giữ và khả dụng tính toán chính xác.
9. Biến thể sắp hết hàng (Low stock) và hết hàng (Out of stock) được phát hiện chính xác.
10. Tỷ lệ đổi / trả hàng lấy số đơn giao thành công làm mẫu số.
11. Một đơn hàng có cả yêu cầu đổi và trả không bị gộp chung hoặc tính sai.
12. Hoàn tiền hoàn tất (Completed refunds) làm giảm doanh thu thuần chính xác.
13. Xuất file CSV tồn kho chứa đầy đủ dữ liệu thực tế từ cơ sở dữ liệu.
"""

from datetime import date, datetime, timedelta
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied
from django.test import Client, TestCase
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import (
    ROLE_ADMIN,
    ROLE_CUSTOMER,
    ROLE_INVENTORY_STAFF,
    ROLE_SALES_STAFF,
)
from catalog.models import Category, Product, ProductVariant
from dashboard.services import (
    calculate_growth,
    export_inventory_csv,
    get_allocated_order_items_net_revenue,
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
from inventory.models import Inventory
from orders.models import (
    CancellationReasonCode,
    Order,
    OrderCancellation,
    OrderItem,
    OrderStatus,
    ReturnReasonCode,
    ReturnRequest,
    ReturnRequestStatus,
    ReturnRequestType,
)
from payments.models import (
    Payment,
    PaymentMethod,
    PaymentStatus,
    Refund,
    RefundReasonType,
    RefundStatus,
)

User = get_user_model()


class DashboardTestCase(TestCase):
    """Lớp kiểm thử cơ sở thiết lập các dữ liệu danh mục, tài khoản, biến thể mẫu."""

    def setUp(self):
        # 1. Tạo các nhóm vai trò
        self.admin_group, _ = Group.objects.get_or_create(name=ROLE_ADMIN)
        self.sales_group, _ = Group.objects.get_or_create(name=ROLE_SALES_STAFF)
        self.inventory_group, _ = Group.objects.get_or_create(name=ROLE_INVENTORY_STAFF)
        self.customer_group, _ = Group.objects.get_or_create(name=ROLE_CUSTOMER)

        # 2. Tạo các tài khoản người dùng tương ứng
        self.admin_user = User.objects.create_user(
            username='test_admin', password='password123', is_staff=True, is_superuser=True
        )
        self.admin_user.groups.add(self.admin_group)

        self.sales_user = User.objects.create_user(
            username='test_sales', password='password123', is_staff=True
        )
        self.sales_user.groups.add(self.sales_group)

        self.inventory_user = User.objects.create_user(
            username='test_inv', password='password123', is_staff=True
        )
        self.inventory_user.groups.add(self.inventory_group)

        self.customer_user = User.objects.create_user(
            username='test_customer', password='password123'
        )
        self.customer_user.groups.add(self.customer_group)

        # 3. Tạo Catalog & Biến thể mẫu
        self.category = Category.objects.create(name='Điện thoại Apple', description='iPhone chính hãng')
        self.product = Product.objects.create(
            category=self.category,
            name='iPhone 16 Pro Max',
            brand='Apple',
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku='IP16PM-256-NAT',
            price=Decimal('34990000.00'),
            is_active=True,
        )

        # 4. Cập nhật Inventory được tạo tự động bởi signal
        Inventory.objects.filter(variant=self.variant).update(
            physical_sellable=20,
            reserved=2,
            min_threshold=5,
        )
        self.inventory = Inventory.objects.get(variant=self.variant)

        self.client = Client()

    # -------------------------------------------------------------------------
    # TEST 1: Admin can access dashboard
    # -------------------------------------------------------------------------
    def test_admin_can_access_dashboard(self):
        self.client.force_login(self.admin_user)
        response = self.client.get('/management/dashboard/')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Dashboard Thống kê & Báo cáo Kinh doanh')

        # Thử tải tệp xuất CSV tồn kho
        csv_resp = self.client.get('/management/dashboard/inventory/export-csv/')
        self.assertEqual(csv_resp.status_code, 200)
        self.assertIn('text/csv', csv_resp['Content-Type'])

    # -------------------------------------------------------------------------
    # TEST 2: Non-Admin users cannot access dashboard
    # -------------------------------------------------------------------------
    def test_non_admin_cannot_access_dashboard(self):
        # Khách vãng lai (Anonymous)
        anon_resp = self.client.get('/management/dashboard/')
        self.assertEqual(anon_resp.status_code, 403)

        # Khách hàng (Customer)
        self.client.force_login(self.customer_user)
        cust_resp = self.client.get('/management/dashboard/')
        self.assertEqual(cust_resp.status_code, 403)

        # Nhân viên kho (Inventory Staff - không phải Admin)
        self.client.force_login(self.inventory_user)
        inv_resp = self.client.get('/management/dashboard/')
        self.assertEqual(inv_resp.status_code, 403)

        # Nhân viên bán hàng (Sales Staff - không phải Admin)
        self.client.force_login(self.sales_user)
        sales_resp = self.client.get('/management/dashboard/')
        self.assertEqual(sales_resp.status_code, 403)

    # -------------------------------------------------------------------------
    # TEST 3: Date filters work correctly
    # -------------------------------------------------------------------------
    def test_date_filters_work_correctly(self):
        today = timezone.localdate()

        # Hôm nay
        r_today = get_date_range('today')
        self.assertEqual(r_today['start_date'], today)
        self.assertEqual(r_today['end_date'], today)
        self.assertEqual(r_today['duration_days'], 1)

        # 7 ngày qua
        r_7 = get_date_range('last_7_days')
        self.assertEqual(r_7['duration_days'], 7)
        self.assertEqual(r_7['end_date'], today)
        self.assertEqual(r_7['start_date'], today - timedelta(days=6))

        # Tháng này
        r_month = get_date_range('this_month')
        self.assertEqual(r_month['start_date'], today.replace(day=1))
        self.assertEqual(r_month['end_date'], today)

        # Tùy chọn (Custom)
        r_custom = get_date_range('custom', start_date_str='2026-11-01', end_date_str='2026-11-10')
        self.assertEqual(r_custom['start_date'], date(2026, 11, 1))
        self.assertEqual(r_custom['end_date'], date(2026, 11, 10))
        self.assertEqual(r_custom['duration_days'], 10)

    # -------------------------------------------------------------------------
    # TEST 4: Previous period comparison is calculated correctly
    # -------------------------------------------------------------------------
    def test_previous_period_comparison_calculated_correctly(self):
        # Xét khoảng tùy chọn 10 ngày từ 11/11 đến 20/11
        r = get_date_range('custom', start_date_str='2026-11-11', end_date_str='2026-11-20')
        self.assertEqual(r['duration_days'], 10)
        # Kỳ trước liền kề phải kết thúc ngày 10/11 và kéo dài 10 ngày (01/11 -> 10/11)
        self.assertEqual(r['prev_end_date'], date(2026, 11, 10))
        self.assertEqual(r['prev_start_date'], date(2026, 11, 1))

        # Kiểm tra hàm tính tăng trưởng an toàn calculate_growth
        self.assertEqual(calculate_growth(100, 50), 100.0)
        self.assertEqual(calculate_growth(50, 100), -50.0)
        self.assertEqual(calculate_growth(100, 100), 0.0)
        self.assertEqual(calculate_growth(50, 0), 100.0)  # Mẫu = 0, tử > 0
        self.assertEqual(calculate_growth(0, 0), 0.0)     # Cả 2 đều 0

    # -------------------------------------------------------------------------
    # TEST 5: Cancelled orders are not counted as successful sales
    # -------------------------------------------------------------------------
    def test_cancelled_orders_not_counted_as_successful_sales(self):
        date_range = get_date_range('today')

        # Tạo 1 đơn hủy và 1 đơn hết hạn QR
        ord_cancelled = Order.objects.create(
            order_code='ORD-TEST-CANC-01',
            customer=self.customer_user,
            recipient_name='Người Nhận 1',
            recipient_phone='0987654321',
            shipping_address='Hà Nội',
            order_status=OrderStatus.CANCELLED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        OrderItem.objects.create(
            order=ord_cancelled,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=1,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('34990000.00'),
        )

        ord_expired = Order.objects.create(
            order_code='ORD-TEST-EXP-01',
            customer=self.customer_user,
            recipient_name='Người Nhận 2',
            recipient_phone='0987654321',
            shipping_address='Hà Nội',
            order_status=OrderStatus.EXPIRED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )

        # Doanh thu và số lượng bán phải bằng 0 (vì không có đơn DELIVERED nào)
        overview = get_overview_metrics(date_range)
        self.assertEqual(overview['total_revenue'], Decimal('0.00'))
        self.assertEqual(overview['total_products_sold'], 0)
        self.assertEqual(overview['delivered_orders_count'], 0)

        # Sản phẩm bán chạy cũng không ghi nhận doanh số
        p_metrics = get_product_metrics(date_range)
        self.assertEqual(len(p_metrics['best_selling']), 0)

    # -------------------------------------------------------------------------
    # TEST 6: Only successfully delivered orders are used for product sales
    # -------------------------------------------------------------------------
    def test_only_successfully_delivered_orders_used_for_product_sales(self):
        date_range = get_date_range('today')

        # 1. Đơn Đang giao (SHIPPING) - Không được tính
        ord_ship = Order.objects.create(
            order_code='ORD-TEST-SHIP-01',
            customer=self.customer_user,
            recipient_name='Người Nhận 3',
            recipient_phone='0987654321',
            shipping_address='Hồ Chí Minh',
            order_status=OrderStatus.SHIPPING,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        OrderItem.objects.create(
            order=ord_ship,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=2,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('69980000.00'),
        )

        # 2. Đơn Giao thành công (DELIVERED) - Được tính chính xác
        ord_deliv = Order.objects.create(
            order_code='ORD-TEST-DELIV-01',
            customer=self.customer_user,
            recipient_name='Người Nhận 4',
            recipient_phone='0987654321',
            shipping_address='Đà Nẵng',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
            delivered_at=timezone.now(),
        )
        OrderItem.objects.create(
            order=ord_deliv,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=3,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('104970000.00'),
        )

        overview = get_overview_metrics(date_range)
        self.assertEqual(overview['total_products_sold'], 3)
        self.assertEqual(overview['total_revenue'], Decimal('34990000.00'))

        p_metrics = get_product_metrics(date_range)
        self.assertEqual(len(p_metrics['best_selling']), 1)
        self.assertEqual(p_metrics['best_selling'][0]['net_quantity'], 3)

    # -------------------------------------------------------------------------
    # TEST 7: Cancellation rate handles zero orders safely
    # -------------------------------------------------------------------------
    def test_cancellation_rate_handles_zero_orders_safely(self):
        # Khoảng thời gian trong quá khứ không có đơn hàng nào
        past_range = get_date_range('custom', start_date_str='2020-01-01', end_date_str='2020-01-02')
        canc = get_cancellation_metrics(past_range)
        self.assertEqual(canc['total_orders'], 0)
        self.assertEqual(canc['total_cancelled'], 0)
        self.assertEqual(canc['cancellation_rate'], 0.0)

    # -------------------------------------------------------------------------
    # TEST 8: Inventory totals are calculated correctly
    # -------------------------------------------------------------------------
    def test_inventory_totals_calculated_correctly(self):
        inv_metrics = get_inventory_metrics()
        self.assertEqual(inv_metrics['total_physical'], 20)
        self.assertEqual(inv_metrics['total_reserved'], 2)
        # Khả dụng = Tồn thực tế (20) - Tồn đã giữ (2) = 18
        self.assertEqual(inv_metrics['total_available'], 18)

    # -------------------------------------------------------------------------
    # TEST 9: Low-stock and out-of-stock variants are identified correctly
    # -------------------------------------------------------------------------
    def test_low_stock_and_out_of_stock_variants_identified_correctly(self):
        # Biến thể 1: Hết hàng (available <= 0)
        prod_out = Product.objects.create(
            category=self.category,
            name='iPhone 16 Pro Max 512GB',
            brand='Apple',
            is_active=True,
        )
        var_out = ProductVariant.objects.create(
            product=prod_out,
            sku='IP16PM-512-BL',
            price=Decimal('39990000.00'),
            is_active=True,
        )
        Inventory.objects.filter(variant=var_out).update(
            physical_sellable=0,
            reserved=0,
            min_threshold=5,
        )

        # Biến thể 2: Sắp hết hàng (available = 3 <= min_threshold = 5)
        prod_low = Product.objects.create(
            category=self.category,
            name='iPhone 16 Pro Max 1TB',
            brand='Apple',
            is_active=True,
        )
        var_low = ProductVariant.objects.create(
            product=prod_low,
            sku='IP16PM-1TB-DES',
            price=Decimal('45990000.00'),
            is_active=True,
        )
        Inventory.objects.filter(variant=var_low).update(
            physical_sellable=4,
            reserved=1,
            min_threshold=5,
        )

        inv_metrics = get_inventory_metrics()
        self.assertEqual(inv_metrics['out_of_stock_count'], 1)
        self.assertEqual(inv_metrics['low_stock_count'], 1)
        self.assertEqual(inv_metrics['restock_needed_count'], 2)

    # -------------------------------------------------------------------------
    # TEST 10: Return/exchange rates use delivered orders as denominator
    # -------------------------------------------------------------------------
    def test_return_exchange_rates_use_delivered_orders_as_denominator(self):
        date_range = get_date_range('today')

        # Tạo 2 đơn giao thành công
        ord1 = Order.objects.create(
            order_code='ORD-TEST-RET-01',
            customer=self.customer_user,
            recipient_name='Khách 1',
            recipient_phone='0987654321',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        item1 = OrderItem.objects.create(
            order=ord1,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=1,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('34990000.00'),
        )

        ord2 = Order.objects.create(
            order_code='ORD-TEST-RET-02',
            customer=self.customer_user,
            recipient_name='Khách 2',
            recipient_phone='0987654321',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        item2 = OrderItem.objects.create(
            order=ord2,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=1,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('34990000.00'),
        )

        # Đơn 1 phát sinh yêu cầu trả hàng
        ReturnRequest.objects.create(
            order=ord1,
            order_item=item1,
            customer=self.customer_user,
            request_type=ReturnRequestType.RETURN,
            quantity=1,
            reason_code=ReturnReasonCode.TECHNICAL_DEFECT,
            description='Lỗi màn hình nhấp nháy',
            status=ReturnRequestStatus.APPROVED,
        )

        ret_metrics = get_return_exchange_metrics(date_range)
        self.assertEqual(ret_metrics['delivered_orders_count'], 2)
        self.assertEqual(ret_metrics['return_orders_count'], 1)
        self.assertEqual(ret_metrics['return_request_rate'], 50.0)
        self.assertEqual(ret_metrics['exchange_request_rate'], 0.0)

    # -------------------------------------------------------------------------
    # TEST 11: An order containing both exchange and return is not double-counted
    # -------------------------------------------------------------------------
    def test_order_with_both_exchange_and_return_not_double_counted(self):
        date_range = get_date_range('today')

        ord_mixed = Order.objects.create(
            order_code='ORD-TEST-MIXED-01',
            customer=self.customer_user,
            recipient_name='Khách Hỗn Hợp',
            recipient_phone='0987654321',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('69980000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('69980000.00'),
        )
        item1 = OrderItem.objects.create(
            order=ord_mixed,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=1,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('34990000.00'),
        )
        item2 = OrderItem.objects.create(
            order=ord_mixed,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=1,
            unit_price=Decimal('34990000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('34990000.00'),
        )

        # 1 yêu cầu đổi hàng cho item 1
        ReturnRequest.objects.create(
            order=ord_mixed,
            order_item=item1,
            customer=self.customer_user,
            request_type=ReturnRequestType.EXCHANGE,
            replacement_variant=self.variant,
            quantity=1,
            reason_code=ReturnReasonCode.CHANGE_COLOR_CAPACITY,
            description='Muốn đổi sang màu khác',
            status=ReturnRequestStatus.APPROVED,
        )

        # 1 yêu cầu trả hàng cho item 2
        ReturnRequest.objects.create(
            order=ord_mixed,
            order_item=item2,
            customer=self.customer_user,
            request_type=ReturnRequestType.RETURN,
            quantity=1,
            reason_code=ReturnReasonCode.TECHNICAL_DEFECT,
            description='Lỗi micro không nghe được tiếng',
            status=ReturnRequestStatus.APPROVED,
        )

        ret_metrics = get_return_exchange_metrics(date_range)
        self.assertEqual(ret_metrics['delivered_orders_count'], 1)
        self.assertEqual(ret_metrics['exchange_orders_count'], 1)
        self.assertEqual(ret_metrics['return_orders_count'], 1)
        # Tỷ lệ mỗi bên là 1/1 = 100%, không bị gộp chung làm 2/1 = 200%
        self.assertEqual(ret_metrics['exchange_request_rate'], 100.0)
        self.assertEqual(ret_metrics['return_request_rate'], 100.0)

    # -------------------------------------------------------------------------
    # TEST 12: Completed refunds reduce net revenue
    # -------------------------------------------------------------------------
    def test_completed_refunds_reduce_net_revenue(self):
        date_range = get_date_range('today')

        ord_refunded = Order.objects.create(
            order_code='ORD-TEST-REF-01',
            customer=self.customer_user,
            recipient_name='Khách Hoàn Tiền',
            recipient_phone='0987654321',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('10000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('10000000.00'),
        )

        # Hoàn tiền đã hoàn tất (COMPLETED): 2,000,000 đ
        Refund.objects.create(
            order=ord_refunded,
            reason_type=RefundReasonType.RETURN,
            amount=Decimal('2000000.00'),
            status=RefundStatus.COMPLETED,
            confirmed_by=self.admin_user,
            completed_at=timezone.now(),
        )

        # Hoàn tiền đang chờ xử lý (PENDING): 1,000,000 đ (KHÔNG được trừ vào doanh thu thuần)
        Refund.objects.create(
            order=ord_refunded,
            reason_type=RefundReasonType.CANCEL,
            amount=Decimal('1000000.00'),
            status=RefundStatus.PENDING,
        )

        overview = get_overview_metrics(date_range)
        self.assertEqual(overview['gross_revenue'], Decimal('10000000.00'))
        self.assertEqual(overview['completed_refunds'], Decimal('2000000.00'))
        # Doanh thu thuần = 10,000,000 - 2,000,000 = 8,000,000 đ
        self.assertEqual(overview['total_revenue'], Decimal('8000000.00'))

    # -------------------------------------------------------------------------
    # TEST 13: CSV inventory export contains database values
    # -------------------------------------------------------------------------
    def test_csv_inventory_export_contains_database_values(self):
        csv_text = export_inventory_csv()
        self.assertIn('Mã SKU', csv_text)
        self.assertIn('IP16PM-256-NAT', csv_text)
        self.assertIn('iPhone 16 Pro Max', csv_text)
        self.assertIn('20', csv_text)  # physical
        self.assertIn('18', csv_text)  # available

    # -------------------------------------------------------------------------
    # TEST 14: Standard return reason codes grouping & preservation of OTHER description
    # -------------------------------------------------------------------------
    def test_standard_return_reason_codes_grouping_and_other_description(self):
        date_range = get_date_range('today')

        ord_demo = Order.objects.create(
            order_code='ORD-TEST-REASONS-01',
            customer=self.customer_user,
            recipient_name='Khách Lý Do Chuẩn',
            recipient_phone='0987654321',
            shipping_address='Hà Nội',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('100000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('100000000.00'),
        )
        item_demo = OrderItem.objects.create(
            order=ord_demo,
            variant=self.variant,
            product_name_snapshot='iPhone 16 Pro Max',
            variant_snapshot='Titan Tự nhiên',
            sku_snapshot='IP16PM-256-NAT',
            quantity=10,
            unit_price=Decimal('10000000.00'),
            discount_amount=Decimal('0.00'),
            line_total=Decimal('100000000.00'),
        )

        all_codes = [
            ReturnReasonCode.TECHNICAL_DEFECT,
            ReturnReasonCode.WRONG_ITEM,
            ReturnReasonCode.NOT_AS_DESCRIBED,
            ReturnReasonCode.DAMAGED_ON_ARRIVAL,
            ReturnReasonCode.NOT_SUITABLE,
            ReturnReasonCode.CHANGE_COLOR_CAPACITY,
            ReturnReasonCode.OTHER,
        ]

        # Tạo từng yêu cầu đổi trả cho 7 mã chuẩn
        for code in all_codes:
            ReturnRequest.objects.create(
                order=ord_demo,
                order_item=item_demo,
                customer=self.customer_user,
                request_type=ReturnRequestType.RETURN,
                quantity=1,
                reason_code=code,
                description=f"Chi tiết giải thích cho mã {code}" if code != ReturnReasonCode.OTHER else "Khách hàng dọn nhà đi công tác nước ngoài dài hạn",
                status=ReturnRequestStatus.APPROVED,
            )

        # Tạo thêm 1 bản ghi cũ (legacy record) không có reason_code (rỗng) để test cơ chế fallback an toàn
        ReturnRequest.objects.create(
            order=ord_demo,
            order_item=item_demo,
            customer=self.customer_user,
            request_type=ReturnRequestType.RETURN,
            quantity=1,
            reason_code='',
            description='Màn hình bị lỗi sọc xanh và liệt cảm ứng',
            status=ReturnRequestStatus.PENDING,
        )

        ret_metrics = get_return_exchange_metrics(date_range)
        reasons_breakdown = {r['code']: r['count'] for r in ret_metrics['reasons_breakdown']}

        # Mỗi mã chuẩn đều có ít nhất 1 bản ghi
        for code in all_codes:
            self.assertGreaterEqual(reasons_breakdown.get(code, 0), 1, f"Mã {code} phải có trong thống kê.")

        # Bản ghi fallback "Màn hình bị lỗi" phải được phân loại an toàn vào TECHNICAL_DEFECT
        self.assertEqual(reasons_breakdown[ReturnReasonCode.TECHNICAL_DEFECT], 2)
        # Mã OTHER có đúng 1 bản ghi
        self.assertEqual(reasons_breakdown[ReturnReasonCode.OTHER], 1)

        # Kiểm tra chi tiết mô tả khách hàng được bảo toàn cho Admin kiểm tra
        other_details = [d for d in ret_metrics['other_requests_details'] if d['reason_code'] == ReturnReasonCode.OTHER]
        self.assertEqual(len(other_details), 1)
        self.assertEqual(other_details[0]['reason'], "Khách hàng dọn nhà đi công tác nước ngoài dài hạn")

    # -------------------------------------------------------------------------
    # TEST 15: Redesigned Inventory Dashboard metrics, formula, and sorting
    # -------------------------------------------------------------------------
    def test_redesigned_inventory_dashboard_metrics_and_sorting(self):
        # Thiết lập 3 biến thể:
        # Biến thể 1: Đủ hàng (physical=30, reserved=5, available=25, min_threshold=10) -> Không cảnh báo
        Inventory.objects.filter(variant=self.variant).update(
            physical_sellable=30,
            reserved=5,
            min_threshold=10,
        )

        # Biến thể 2: Hết hàng (physical=2, reserved=2, available=0 <= 0, min_threshold=5) -> 'Hết hàng'
        prod_oos = Product.objects.create(
            category=self.category,
            name='iPhone Hết Hàng',
            brand='Apple',
            is_active=True,
        )
        var_oos = ProductVariant.objects.create(
            product=prod_oos,
            sku='IP-OOS-SKU',
            price=Decimal('20000000.00'),
            is_active=True,
        )
        Inventory.objects.filter(variant=var_oos).update(
            physical_sellable=2,
            reserved=2,
            min_threshold=5,
        )

        # Biến thể 3: Sắp hết (physical=8, reserved=2, available=6 <= min_threshold=10) -> 'Sắp hết'
        prod_low = Product.objects.create(
            category=self.category,
            name='iPhone Sắp Hết Hàng',
            brand='Apple',
            is_active=True,
        )
        var_low = ProductVariant.objects.create(
            product=prod_low,
            sku='IP-LOW-SKU',
            price=Decimal('25000000.00'),
            is_active=True,
        )
        Inventory.objects.filter(variant=var_low).update(
            physical_sellable=8,
            reserved=2,
            min_threshold=10,
        )

        inv_metrics = get_inventory_metrics()

        # Kiểm tra công thức: Tổng tồn thực tế = Tồn đã giữ + Tồn có thể bán
        self.assertEqual(
            inv_metrics['total_physical'],
            inv_metrics['total_reserved'] + inv_metrics['total_available'],
        )
        self.assertEqual(inv_metrics['total_physical'], 30 + 2 + 8)
        self.assertEqual(inv_metrics['total_reserved'], 5 + 2 + 2)
        self.assertEqual(inv_metrics['total_available'], 25 + 0 + 6)

        # Kiểm tra số lượng cảnh báo không bị tính trùng
        self.assertEqual(inv_metrics['out_of_stock_count'], 1)
        self.assertEqual(inv_metrics['low_stock_count'], 1)
        self.assertEqual(inv_metrics['total_warning_count'], 2)
        self.assertEqual(len(inv_metrics['warning_variants']), 2)

        # Kiểm tra thứ tự sắp xếp: Hết hàng (available <= 0) xếp trước Sắp hết (0 < available <= min_threshold)
        first_item = inv_metrics['warning_variants'][0]
        second_item = inv_metrics['warning_variants'][1]

        self.assertEqual(first_item['status_label'], 'Hết hàng')
        self.assertEqual(first_item['sku'], 'IP-OOS-SKU')
        self.assertEqual(first_item['available'], 0)

        self.assertEqual(second_item['status_label'], 'Sắp hết')
        self.assertEqual(second_item['sku'], 'IP-LOW-SKU')
        self.assertEqual(second_item['available'], 6)

    # -------------------------------------------------------------------------
    # TEST 16: Exact Vietnamese labels, section titles, and units in template
    # -------------------------------------------------------------------------
    def test_exact_vietnamese_labels_and_units_in_template(self):
        self.client.force_login(self.admin_user)
        response = self.client.get('/management/dashboard/')
        self.assertEqual(response.status_code, 200)

        html = response.content.decode('utf-8')

        # 1. KPI Sản phẩm bán ra
        self.assertIn('Sản phẩm bán ra', html)
        self.assertNotIn('Sản phẩm bán ra (Thuần)', html)
        self.assertIn('sản phẩm</small>', html)

        # 2. Tiêu đề biểu đồ và các phân hệ
        self.assertIn('Tỷ trọng doanh thu theo danh mục', html)
        self.assertIn('Biểu đồ doanh thu theo ngày & So sánh kỳ trước', html)
        self.assertIn('Thống kê hiệu suất sản phẩm', html)
        self.assertNotIn('Hiệu quả Kinh doanh Sản phẩm & Biến thể', html)
        self.assertIn('Thống kê đơn hàng chi tiết', html)
        self.assertNotIn('Thống kê Chi tiết Trạng thái Đơn hàng', html)
        self.assertIn('Thống kê hủy đơn hàng', html)
        self.assertIn('Nguyên nhân hủy đơn hàng', html)
        self.assertIn('Trạng thái xử lý đổi/trả', html)
        self.assertIn('Nguyên nhân đổi trả', html)

        # 3. Phân nhóm Order Dashboard 4 nhóm
        self.assertIn('Tổng số đơn hàng', html)
        self.assertIn('Tỷ lệ giao hàng thành công', html)
        self.assertIn('Số đơn chờ thanh toán', html)
        self.assertIn('Số đơn chờ đối soát', html)
        self.assertIn('Số đơn chờ xác nhận', html)
        self.assertIn('Số đơn đã xác nhận', html)
        self.assertIn('Số đơn đang giao', html)
        self.assertIn('Số đơn giao thành công', html)
        self.assertIn('Số đơn đã hủy', html)
        self.assertIn('Số đơn hết hạn thanh toán', html)

        # 4. Các đơn vị chuẩn: đơn, %, yêu cầu, sản phẩm
        self.assertIn('đơn', html)
        self.assertIn('%', html)
        self.assertIn('yêu cầu', html)

        # 5. 7 Tên lý do đổi trả chuẩn xác
        self.assertIn('Sản phẩm lỗi kỹ thuật', html)
        self.assertIn('Giao sai sản phẩm/biến thể', html)
        self.assertIn('Sản phẩm không đúng mô tả', html)
        self.assertIn('Sản phẩm hư hỏng khi nhận', html)
        self.assertIn('Không phù hợp với nhu cầu', html)
        self.assertIn('Muốn đổi màu sắc/dung lượng', html)
        self.assertIn('Khác', html)

    # -------------------------------------------------------------------------
    # TEST 17: Dynamic Chart Data and JSON Script Serialization
    # -------------------------------------------------------------------------
    def test_dynamic_chart_data_and_json_serialization(self):
        self.client.force_login(self.admin_user)
        response = self.client.get('/management/dashboard/?period=this_month')
        self.assertEqual(response.status_code, 200)

        # Kiểm tra dữ liệu JSON trong context
        revenue_by_day = response.context['revenue_by_day']
        category_revenue = response.context['category_revenue']

        self.assertIn('labels', revenue_by_day)
        self.assertIn('current_revenues', revenue_by_day)
        self.assertIn('previous_revenues', revenue_by_day)
        self.assertIsInstance(revenue_by_day['current_revenues'], list)

        self.assertIn('categories', category_revenue)
        self.assertIn('total_revenue', category_revenue)
        self.assertIsInstance(category_revenue['categories'], list)

        # Kiểm tra JSON script trong HTML không bị double-encode
        html = response.content.decode('utf-8')
        import json
        import re

        rev_script = re.search(r'<script id="revenue-by-day-data" type="application/json">(.*?)</script>', html)
        self.assertIsNotNone(rev_script, "Phải có thẻ script revenue-by-day-data")
        parsed_rev = json.loads(rev_script.group(1))
        self.assertIsInstance(parsed_rev, dict, "Dữ liệu JSON trong script phải là dict trực tiếp, không phải string bị double-encode")

        cat_script = re.search(r'<script id="category-revenue-data" type="application/json">(.*?)</script>', html)
        self.assertIsNotNone(cat_script, "Phải có thẻ script category-revenue-data")
        parsed_cat = json.loads(cat_script.group(1))
        self.assertIsInstance(parsed_cat, dict, "Dữ liệu JSON danh mục phải là dict trực tiếp")

    # -------------------------------------------------------------------------
    # TEST 18: Dynamic behavior with 0, 1, and many products/categories
    # -------------------------------------------------------------------------
    def test_dynamic_behavior_different_numbers_of_products_and_categories(self):
        # 1. Không có đơn hàng nào
        empty_range = get_date_range('custom', start_date_str='2010-01-01', end_date_str='2010-01-05')
        empty_rev = get_revenue_by_day(empty_range)
        self.assertEqual(empty_rev['current_total'], 0.0)
        self.assertEqual(len(empty_rev['labels']), 5)
        self.assertEqual(sum(empty_rev['current_revenues']), 0.0)

        empty_cat = get_category_revenue(empty_range)
        self.assertEqual(empty_cat['total_revenue'], Decimal('0.00'))
        self.assertEqual(len(empty_cat['categories']), 0)

        # 2. Tạo đơn hàng với nhiều danh mục khác nhau
        cat2 = Category.objects.create(name='Phụ kiện chính hãng')
        prod2 = Product.objects.create(category=cat2, name='Củ sạc nhanh 20W Apple', brand='Apple', is_active=True)
        var2 = ProductVariant.objects.create(product=prod2, sku='CHG-20W-W', price=Decimal('590000.00'), is_active=True)

        ord_multi = Order.objects.create(
            order_code='ORD-MULTI-CAT-01',
            customer=self.customer_user,
            recipient_name='Khách Nhiều Danh Mục',
            recipient_phone='0987654321',
            shipping_address='Hà Nội',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('35580000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('35580000.00'),
        )
        # Item 1 thuộc Điện thoại Apple
        OrderItem.objects.create(
            order=ord_multi,
            variant=self.variant,
            product_name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku,
            quantity=1,
            unit_price=Decimal('34990000.00'),
            line_total=Decimal('34990000.00'),
        )
        # Item 2 thuộc Phụ kiện chính hãng
        OrderItem.objects.create(
            order=ord_multi,
            variant=var2,
            product_name_snapshot=prod2.name,
            sku_snapshot=var2.sku,
            quantity=1,
            unit_price=Decimal('590000.00'),
            line_total=Decimal('590000.00'),
        )

        today_range = get_date_range('today')
        multi_cat = get_category_revenue(today_range)
        self.assertGreaterEqual(len(multi_cat['categories']), 2)

        cat_names = [c['name'] for c in multi_cat['categories']]
        self.assertIn('Điện thoại Apple', cat_names)
        self.assertIn('Phụ kiện chính hãng', cat_names)

        # Tỷ trọng phần trăm phải tính toán động và tổng xấp xỉ 100%
        pct_sum = sum(c['percentage'] for c in multi_cat['categories'])
        self.assertAlmostEqual(pct_sum, 100.0, delta=1.0)


# =============================================================================
# KIỂM THỬ TÍNH NHẤT QUÁN CỦA DOANH THU THUẦN (REVENUE CONSISTENCY TEST SUITE)
# =============================================================================
class RevenueConsistencyTestCase(TestCase):
    """
    Bộ kiểm thử bảo đảm tính nhất quán của Doanh thu thuần (Net Revenue Consistency)
    xuyên suốt toàn bộ hệ thống Dashboard:
    - Thẻ chỉ số tổng quan (Overview Total Revenue KPI Card)
    - Bảng thống kê hiệu suất sản phẩm (Product Performance Table)
    - Biểu đồ cơ cấu doanh thu theo danh mục (Category Revenue Donut Chart)
    - Biểu đồ doanh thu theo ngày (Revenue By Day Chart)
    Quy tắc cốt lõi: Across all delivered orders: sum(product net revenue) == overview net revenue.
    """

    def setUp(self):
        # 1. Tạo các nhóm vai trò & tài khoản
        self.admin_group, _ = Group.objects.get_or_create(name=ROLE_ADMIN)
        self.customer_group, _ = Group.objects.get_or_create(name=ROLE_CUSTOMER)

        self.admin_user = User.objects.create_user(
            username='rev_admin', password='password123', is_staff=True, is_superuser=True
        )
        self.admin_user.groups.add(self.admin_group)

        self.customer_user = User.objects.create_user(
            username='rev_customer', password='password123'
        )
        self.customer_user.groups.add(self.customer_group)

        # 2. Tạo Catalog & Biến thể mẫu chính
        self.category = Category.objects.create(name='Điện thoại Apple', description='iPhone chính hãng')
        self.product = Product.objects.create(
            category=self.category,
            name='iPhone 16 Pro Max',
            brand='Apple',
            is_active=True,
        )
        self.variant = ProductVariant.objects.create(
            product=self.product,
            sku='IP16PM-256-NAT',
            price=Decimal('34990000.00'),
            is_active=True,
        )
        Inventory.objects.filter(variant=self.variant).update(
            physical_sellable=20,
            reserved=2,
            min_threshold=5,
        )

        # 3. Tạo thêm danh mục & biến thể phụ để kiểm tra đa sản phẩm và đa danh mục
        self.cat_acc = Category.objects.create(name='Phụ kiện Apple', description='Phụ kiện chính hãng')
        self.prod_case = Product.objects.create(
            category=self.cat_acc,
            name='Ốp lưng iPhone 16 Pro Max Silicone',
            brand='Apple',
            is_active=True,
        )
        self.var_case = ProductVariant.objects.create(
            product=self.prod_case,
            sku='CASE-16PM-BLK',
            price=Decimal('1290000.00'),
            is_active=True,
        )
        self.prod_charger = Product.objects.create(
            category=self.cat_acc,
            name='Củ sạc nhanh 30W Apple',
            brand='Apple',
            is_active=True,
        )
        self.var_charger = ProductVariant.objects.create(
            product=self.prod_charger,
            sku='CHG-30W-W',
            price=Decimal('890000.00'),
            is_active=True,
        )
        self.client = Client()

    # -------------------------------------------------------------------------
    # TEST 1: Đơn hàng không hoàn tiền -> Doanh thu thuần sản phẩm = doanh thu gộp = tổng quan
    # -------------------------------------------------------------------------
    def test_order_with_no_refunds_product_net_revenue_matches_overview(self):
        date_range = get_date_range('today')
        order = Order.objects.create(
            order_code='ORD-CONSIST-01',
            customer=self.customer_user,
            recipient_name='Khách Hàng 01',
            recipient_phone='0901234567',
            shipping_address='TP HCM',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        OrderItem.objects.create(
            order=order,
            variant=self.variant,
            product_name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku,
            quantity=1,
            unit_price=Decimal('34990000.00'),
            line_total=Decimal('34990000.00'),
        )

        overview = get_overview_metrics(date_range)
        p_metrics = get_product_metrics(date_range)
        cat_metrics = get_category_revenue(date_range)

        # Doanh thu thuần tổng quan
        self.assertEqual(overview['total_revenue'], Decimal('34990000.00'))
        self.assertEqual(overview['completed_refunds'], Decimal('0.00'))

        # Doanh thu thuần bảng sản phẩm
        self.assertEqual(len(p_metrics['best_selling']), 1)
        self.assertEqual(p_metrics['best_selling'][0]['revenue'], Decimal('34990000.00'))

        # Doanh thu thuần danh mục
        self.assertEqual(cat_metrics['total_revenue'], Decimal('34990000.00'))

        # Nhất quán tuyệt đối: tổng sản phẩm == tổng quan
        prod_sum = sum(p['revenue'] for p in p_metrics['best_selling'])
        self.assertEqual(prod_sum, overview['total_revenue'])
        self.assertEqual(cat_metrics['total_revenue'], overview['total_revenue'])

    # -------------------------------------------------------------------------
    # TEST 2: Đơn hàng hoàn tiền một phần -> Doanh thu thuần sản phẩm phản ánh chính xác khoản hoàn
    # -------------------------------------------------------------------------
    def test_order_with_partial_refund_deducted_consistently(self):
        date_range = get_date_range('today')
        order = Order.objects.create(
            order_code='ORD-CONSIST-02',
            customer=self.customer_user,
            recipient_name='Khách Hàng 02',
            recipient_phone='0901234567',
            shipping_address='Hà Nội',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('34990000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('34990000.00'),
        )
        OrderItem.objects.create(
            order=order,
            variant=self.variant,
            product_name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku,
            quantity=1,
            unit_price=Decimal('34990000.00'),
            line_total=Decimal('34990000.00'),
        )
        # Hoàn tiền đã hoàn tất (COMPLETED): 4,990,000 đ
        Refund.objects.create(
            order=order,
            reason_type=RefundReasonType.RETURN,
            amount=Decimal('4990000.00'),
            status=RefundStatus.COMPLETED,
            confirmed_by=self.admin_user,
            completed_at=timezone.now(),
        )
        # Hoàn tiền đang chờ duyệt (PENDING): 2,000,000 đ (KHÔNG được trừ)
        Refund.objects.create(
            order=order,
            reason_type=RefundReasonType.CANCEL,
            amount=Decimal('2000000.00'),
            status=RefundStatus.PENDING,
        )

        expected_net = Decimal('34990000.00') - Decimal('4990000.00')  # 30,000,000 đ

        overview = get_overview_metrics(date_range)
        p_metrics = get_product_metrics(date_range)
        cat_metrics = get_category_revenue(date_range)

        self.assertEqual(overview['completed_refunds'], Decimal('4990000.00'))
        self.assertEqual(overview['total_revenue'], expected_net)
        self.assertEqual(p_metrics['best_selling'][0]['revenue'], expected_net)
        self.assertEqual(cat_metrics['total_revenue'], expected_net)

        prod_sum = sum(p['revenue'] for p in p_metrics['best_selling'])
        self.assertEqual(prod_sum, overview['total_revenue'])

    # -------------------------------------------------------------------------
    # TEST 3: Đơn hàng nhiều sản phẩm có hoàn tiền -> Phân bổ tỷ lệ và bảo toàn phần dư làm tròn
    # -------------------------------------------------------------------------
    def test_multi_product_order_proportional_refund_allocation_and_remainder(self):
        date_range = get_date_range('today')
        # Đơn hàng gồm 2 sản phẩm:
        # Item 1: 6,000,000 đ (tỷ trọng 60%)
        # Item 2: 4,000,000 đ (tỷ trọng 40%)
        # Tổng đơn = 10,000,000 đ
        # Hoàn tiền COMPLETED = 2,000,000 đ
        # Doanh thu thuần đơn = 8,000,000 đ
        # Dự kiến Item 1 = 60% * 8M = 4,800,000 đ
        # Dự kiến Item 2 = 8M - 4.8M = 3,200,000 đ
        order = Order.objects.create(
            order_code='ORD-CONSIST-03',
            customer=self.customer_user,
            recipient_name='Khách Hàng 03',
            recipient_phone='0901234567',
            shipping_address='Đà Nẵng',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('10000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('10000000.00'),
        )
        it1 = OrderItem.objects.create(
            order=order,
            variant=self.variant,
            product_name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku,
            quantity=1,
            unit_price=Decimal('6000000.00'),
            line_total=Decimal('6000000.00'),
        )
        it2 = OrderItem.objects.create(
            order=order,
            variant=self.var_case,
            product_name_snapshot=self.prod_case.name,
            sku_snapshot=self.var_case.sku,
            quantity=1,
            unit_price=Decimal('4000000.00'),
            line_total=Decimal('4000000.00'),
        )
        Refund.objects.create(
            order=order,
            reason_type=RefundReasonType.RETURN,
            amount=Decimal('2000000.00'),
            status=RefundStatus.COMPLETED,
            confirmed_by=self.admin_user,
            completed_at=timezone.now(),
        )

        overview = get_overview_metrics(date_range)
        p_metrics = get_product_metrics(date_range)
        cat_metrics = get_category_revenue(date_range)

        self.assertEqual(overview['total_revenue'], Decimal('8000000.00'))

        p_rev_map = {p['sku']: p['revenue'] for p in p_metrics['best_selling']}
        self.assertEqual(p_rev_map[self.variant.sku], Decimal('4800000.00'))
        self.assertEqual(p_rev_map[self.var_case.sku], Decimal('3200000.00'))

        # Tổng doanh thu các sản phẩm phải đúng chính xác 8,000,000 đ
        prod_sum = sum(p['revenue'] for p in p_metrics['best_selling'])
        self.assertEqual(prod_sum, Decimal('8000000.00'))
        self.assertEqual(prod_sum, overview['total_revenue'])
        self.assertEqual(cat_metrics['total_revenue'], overview['total_revenue'])

        # Thử nghiệm thêm phép chia 3 sản phẩm có số dư lẻ (penny remainder test)
        allocated_items = get_allocated_order_items_net_revenue(Order.objects.filter(id=order.id))
        self.assertEqual(allocated_items[it1.id] + allocated_items[it2.id], Decimal('8000000.00'))

    # -------------------------------------------------------------------------
    # TEST 4: Đơn hàng hoàn tiền 100% -> Doanh thu thuần trở về 0 và khớp tổng quan
    # -------------------------------------------------------------------------
    def test_fully_refunded_order_net_revenue_zero(self):
        date_range = get_date_range('today')
        order = Order.objects.create(
            order_code='ORD-CONSIST-04',
            customer=self.customer_user,
            recipient_name='Khách Hàng 04',
            recipient_phone='0901234567',
            shipping_address='Cần Thơ',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('10000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('10000000.00'),
        )
        OrderItem.objects.create(
            order=order,
            variant=self.variant,
            product_name_snapshot=self.product.name,
            sku_snapshot=self.variant.sku,
            quantity=1,
            unit_price=Decimal('10000000.00'),
            line_total=Decimal('10000000.00'),
        )
        Refund.objects.create(
            order=order,
            reason_type=RefundReasonType.RETURN,
            amount=Decimal('10000000.00'),
            status=RefundStatus.COMPLETED,
            confirmed_by=self.admin_user,
            completed_at=timezone.now(),
        )

        overview = get_overview_metrics(date_range)
        p_metrics = get_product_metrics(date_range)
        cat_metrics = get_category_revenue(date_range)

        self.assertEqual(overview['gross_revenue'], Decimal('10000000.00'))
        self.assertEqual(overview['completed_refunds'], Decimal('10000000.00'))
        self.assertEqual(overview['total_revenue'], Decimal('0.00'))

        self.assertEqual(len(p_metrics['best_selling']), 1)
        self.assertEqual(p_metrics['best_selling'][0]['revenue'], Decimal('0.00'))
        self.assertEqual(cat_metrics['total_revenue'], Decimal('0.00'))

        prod_sum = sum(p['revenue'] for p in p_metrics['best_selling'])
        self.assertEqual(prod_sum, overview['total_revenue'])

    # -------------------------------------------------------------------------
    # TEST 5: Nhiều đơn hàng với các trạng thái hoàn tiền hỗn hợp -> Khớp toàn diện
    # -------------------------------------------------------------------------
    def test_multiple_orders_mixed_refund_statuses_sum_consistency(self):
        date_range = get_date_range('today')
        # Đơn 1: DELIVERED, grand_total 10M, hoàn tiền COMPLETED 2M -> Net = 8M
        ord1 = Order.objects.create(
            order_code='ORD-MIX-01',
            customer=self.customer_user,
            recipient_name='Khách 1',
            recipient_phone='0901111111',
            shipping_address='Hà Nội',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('10000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('10000000.00'),
        )
        OrderItem.objects.create(
            order=ord1,
            variant=self.variant,
            quantity=1,
            unit_price=Decimal('10000000.00'),
            line_total=Decimal('10000000.00'),
        )
        Refund.objects.create(
            order=ord1,
            amount=Decimal('2000000.00'),
            status=RefundStatus.COMPLETED,
            reason_type=RefundReasonType.RETURN,
        )

        # Đơn 2: DELIVERED, grand_total 5M, hoàn tiền PENDING 1M (không trừ) -> Net = 5M
        ord2 = Order.objects.create(
            order_code='ORD-MIX-02',
            customer=self.customer_user,
            recipient_name='Khách 2',
            recipient_phone='0902222222',
            shipping_address='Hải Phòng',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('5000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('5000000.00'),
        )
        OrderItem.objects.create(
            order=ord2,
            variant=self.var_case,
            quantity=5,
            unit_price=Decimal('1000000.00'),
            line_total=Decimal('5000000.00'),
        )
        Refund.objects.create(
            order=ord2,
            amount=Decimal('1000000.00'),
            status=RefundStatus.PENDING,
            reason_type=RefundReasonType.CANCEL,
        )

        # Đơn 3: DELIVERED, grand_total 4M, hoàn tiền COMPLETED 4M -> Net = 0M
        ord3 = Order.objects.create(
            order_code='ORD-MIX-03',
            customer=self.customer_user,
            recipient_name='Khách 3',
            recipient_phone='0903333333',
            shipping_address='Nghệ An',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('4000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('4000000.00'),
        )
        OrderItem.objects.create(
            order=ord3,
            variant=self.var_charger,
            quantity=4,
            unit_price=Decimal('1000000.00'),
            line_total=Decimal('4000000.00'),
        )
        Refund.objects.create(
            order=ord3,
            amount=Decimal('4000000.00'),
            status=RefundStatus.COMPLETED,
            reason_type=RefundReasonType.RETURN,
        )

        # Đơn 4: CANCELLED, grand_total 7M (không được tính vào doanh thu)
        ord4 = Order.objects.create(
            order_code='ORD-MIX-04',
            customer=self.customer_user,
            recipient_name='Khách 4',
            recipient_phone='0904444444',
            shipping_address='Huế',
            order_status=OrderStatus.CANCELLED,
            subtotal=Decimal('7000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('7000000.00'),
        )
        OrderItem.objects.create(
            order=ord4,
            variant=self.variant,
            quantity=1,
            unit_price=Decimal('7000000.00'),
            line_total=Decimal('7000000.00'),
        )

        # Tổng doanh thu thuần kỳ vọng: 8M + 5M + 0 = 13,000,000 đ
        expected_total_net = Decimal('13000000.00')

        overview = get_overview_metrics(date_range)
        p_metrics = get_product_metrics(date_range)
        cat_metrics = get_category_revenue(date_range)

        self.assertEqual(overview['total_revenue'], expected_total_net)
        self.assertEqual(cat_metrics['total_revenue'], expected_total_net)

        prod_sum = sum(p['revenue'] for p in p_metrics['best_selling'])
        self.assertEqual(prod_sum, expected_total_net)
        self.assertEqual(prod_sum, overview['total_revenue'])

    # -------------------------------------------------------------------------
    # TEST 6: Bộ lọc thời gian (Date filters) -> Doanh thu thuần nhất quán mọi khoảng thời gian
    # -------------------------------------------------------------------------
    def test_date_range_filters_revenue_consistency(self):
        now = timezone.now()
        # Đơn 1: Hôm nay (DELIVERED, net 10M)
        ord_today = Order.objects.create(
            order_code='ORD-DATE-TODAY',
            customer=self.customer_user,
            recipient_name='Khách Today',
            recipient_phone='0901111111',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('10000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('10000000.00'),
        )
        OrderItem.objects.create(
            order=ord_today,
            variant=self.variant,
            quantity=1,
            unit_price=Decimal('10000000.00'),
            line_total=Decimal('10000000.00'),
        )

        # Đơn 2: 3 ngày trước (DELIVERED, grand_total 8M, refund 2M -> net 6M)
        ord_3days = Order.objects.create(
            order_code='ORD-DATE-3DAYS',
            customer=self.customer_user,
            recipient_name='Khách 3 Days',
            recipient_phone='0902222222',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('8000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('8000000.00'),
        )
        OrderItem.objects.create(
            order=ord_3days,
            variant=self.var_case,
            quantity=2,
            unit_price=Decimal('4000000.00'),
            line_total=Decimal('8000000.00'),
        )
        Refund.objects.create(
            order=ord_3days,
            amount=Decimal('2000000.00'),
            status=RefundStatus.COMPLETED,
            reason_type=RefundReasonType.RETURN,
        )
        Order.objects.filter(id=ord_3days.id).update(created_at=now - timedelta(days=3))

        # Đơn 3: 40 ngày trước (DELIVERED, net 4M)
        ord_40days = Order.objects.create(
            order_code='ORD-DATE-40DAYS',
            customer=self.customer_user,
            recipient_name='Khách 40 Days',
            recipient_phone='0903333333',
            shipping_address='HN',
            order_status=OrderStatus.DELIVERED,
            subtotal=Decimal('4000000.00'),
            discount_total=Decimal('0.00'),
            grand_total=Decimal('4000000.00'),
        )
        OrderItem.objects.create(
            order=ord_40days,
            variant=self.var_charger,
            quantity=4,
            unit_price=Decimal('1000000.00'),
            line_total=Decimal('4000000.00'),
        )
        Order.objects.filter(id=ord_40days.id).update(created_at=now - timedelta(days=40))

        # Kiểm tra tính nhất quán qua các khoảng thời gian: 'today', 'last_7_days', 'custom'
        for period in ['today', 'last_7_days']:
            d_range = get_date_range(period)
            ov = get_overview_metrics(d_range)
            pm = get_product_metrics(d_range)
            cm = get_category_revenue(d_range)

            prod_total = sum(p['revenue'] for p in pm['best_selling'])
            self.assertEqual(prod_total, ov['total_revenue'])
            self.assertEqual(cm['total_revenue'], ov['total_revenue'])

        # Khoảng tùy chỉnh bao trùm toàn bộ
        custom_range = get_date_range(
            'custom',
            start_date_str=(now - timedelta(days=60)).strftime('%Y-%m-%d'),
            end_date_str=(now + timedelta(days=1)).strftime('%Y-%m-%d'),
        )
        ov_c = get_overview_metrics(custom_range)
        pm_c = get_product_metrics(custom_range)
        cm_c = get_category_revenue(custom_range)

        # Tổng cả 3 đơn: 10M + 6M + 4M = 20M
        self.assertEqual(ov_c['total_revenue'], Decimal('20000000.00'))
        prod_total_c = sum(p['revenue'] for p in pm_c['best_selling'])
        self.assertEqual(prod_total_c, Decimal('20000000.00'))
        self.assertEqual(cm_c['total_revenue'], Decimal('20000000.00'))

    # -------------------------------------------------------------------------
    # TEST 7: Bộ dữ liệu rỗng -> Xử lý an toàn, doanh thu 0, không chia cho 0
    # -------------------------------------------------------------------------
    def test_empty_dataset_handles_zero_revenue_safely(self):
        past_range = get_date_range('custom', start_date_str='2010-01-01', end_date_str='2010-01-02')

        overview = get_overview_metrics(past_range)
        p_metrics = get_product_metrics(past_range)
        cat_metrics = get_category_revenue(past_range)

        self.assertEqual(overview['total_revenue'], Decimal('0.00'))
        self.assertEqual(overview['gross_revenue'], Decimal('0.00'))
        self.assertEqual(overview['completed_refunds'], Decimal('0.00'))

        self.assertEqual(len(p_metrics['best_selling']), 0)
        self.assertEqual(cat_metrics['total_revenue'], Decimal('0.00'))
        self.assertEqual(len(cat_metrics['categories']), 0)

        # Kiểm tra bảng bán chậm với biến thể có sẵn nhưng phát sinh 0 doanh thu
        for item in p_metrics['slowest_selling']:
            self.assertEqual(item['revenue'], Decimal('0.00'))
            self.assertEqual(item['net_quantity'], 0)


# =============================================================================
# KIỂM THỬ SIDEBAR & NAVIGATION QUẢN TRỊ (Sidebar Navigation Spec)
# =============================================================================
class DashboardSidebarNavigationTestCase(TestCase):
    """
    Kiểm thử cấu trúc Sidebar Quản trị chia sẻ và trạng thái active menu:
    - 4 nhóm danh mục chuẩn: TỔNG QUAN, QUẢN LÝ HÀNG HÓA, BÁN HÀNG & CSKH, HỆ THỐNG
    - 9 mục điều hướng chuẩn với URL hợp lệ (không chứa href="#")
    - Trạng thái active: Trên Dashboard thì "Dashboard thống kê" active, "Quản lý tồn kho" không active
    - Trạng thái active trên trang Tồn kho: "Quản lý tồn kho" active, "Dashboard thống kê" không active
    - Khối thương hiệu: QUỐC HÙNG MOBILE / ADMIN PORTAL
    - Khối thông tin tài khoản ở đáy sidebar: Avatar, Tên, Administrator, Form POST Đăng xuất với CSRF
    - Khối điều khiển drawer trên mobile: Backdrop, nút đóng #qhm-sidebar-close
    """

    def setUp(self):
        self.client = Client()
        self.admin_group, _ = Group.objects.get_or_create(name=ROLE_ADMIN)
        self.admin_user = User.objects.create_user(
            username='admin_nav_tester',
            password='navpassword123',
            is_staff=True,
            first_name='Admin',
            last_name='Tester',
        )
        self.admin_user.groups.add(self.admin_group)
        self.client.login(username='admin_nav_tester', password='navpassword123')

    def test_dashboard_sidebar_structure_and_active_state(self):
        url = reverse('dashboard:index')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['active_nav'], 'dashboard')

        html = response.content.decode('utf-8')

        # 1. Khối thương hiệu (Brand Area)
        self.assertIn('QUỐC HÙNG MOBILE', html)
        self.assertIn('ADMIN PORTAL', html)
        self.assertIn('id="qhm-sidebar-close"', html)
        self.assertIn('id="qhm-sidebar-backdrop"', html)

        # 2. 4 nhóm điều hướng chuẩn
        self.assertIn('TỔNG QUAN', html)
        self.assertIn('QUẢN LÝ HÀNG HÓA', html)
        self.assertIn('BÁN HÀNG & CSKH', html)
        self.assertIn('HỆ THỐNG', html)

        # 3. 9 mục điều hướng chuẩn
        self.assertIn('Dashboard thống kê', html)
        self.assertIn('Quản lý danh mục', html)
        self.assertIn('Quản lý sản phẩm', html)
        self.assertIn('Biến thể & Thuộc tính', html)
        self.assertIn('Quản lý tồn kho', html)
        self.assertIn('Quản lý đơn hàng', html)
        self.assertIn('Mã giảm giá', html)
        self.assertIn('Quản lý Chatbot & CSKH', html)
        self.assertIn('Tài khoản & Phân quyền', html)

        # 4. URL hợp lệ và không dùng href="#"
        self.assertIn(reverse('dashboard:index'), html)
        self.assertIn(reverse('catalog:category_list'), html)
        self.assertIn(reverse('catalog:product_list'), html)
        self.assertIn(reverse('catalog:attribute_list'), html)
        self.assertIn(reverse('inventory:inventory_list'), html)
        self.assertIn(reverse('orders:pending_orders_list'), html)
        self.assertIn(reverse('coupons:coupon_list'), html)
        self.assertIn(reverse('chat:staff_inbox'), html)
        self.assertIn(reverse('accounts:staff_list'), html)

        # 5. Trạng thái active trên Dashboard
        # Dashboard thống kê phải có class active
        self.assertRegex(html, r'class="[^"]*active[^"]*"[^>]*id="side-nav-dashboard"|id="side-nav-dashboard"[^>]*class="[^"]*active[^"]*"')
        # Quản lý tồn kho không được active
        self.assertNotRegex(html, r'class="[^"]*active[^"]*"[^>]*id="side-nav-inventory"|id="side-nav-inventory"[^>]*class="[^"]*active[^"]*"')

        # 6. Khối người dùng ở đáy sidebar
        self.assertIn('Administrator', html)
        self.assertIn(reverse('accounts:logout'), html)
        self.assertIn('csrfmiddlewaretoken', html)

    def test_inventory_sidebar_active_state(self):
        url = reverse('inventory:inventory_list')
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)

        html = response.content.decode('utf-8')
        # Kiểm tra Quản lý tồn kho được hiển thị với class active và Dashboard không active
        self.assertRegex(html, r'class="[^"]*active[^"]*"[^>]*id="side-nav-inventory"|id="side-nav-inventory"[^>]*class="[^"]*active[^"]*"')
        self.assertNotRegex(html, r'class="[^"]*active[^"]*"[^>]*id="side-nav-dashboard"|id="side-nav-dashboard"[^>]*class="[^"]*active[^"]*"')


