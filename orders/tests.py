import tempfile
import uuid
from datetime import timedelta
from decimal import Decimal
from io import BytesIO

from PIL import Image
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse, resolve, Resolver404
from django.utils import timezone

from cart.models import Cart, CartItem
from catalog.models import Category, Product, ProductVariant
from coupons.models import Coupon, CouponUsageStatus
from inventory.models import InventoryReservationStatus
from orders.models import (
    Order,
    OrderStatus,
    ReturnRequest,
    ReturnType,
    ReturnReason,
    ReturnStatus,
    OrderCancellation,
)
from orders.services import (
    create_cod_order_from_cart,
    confirm_order,
    ship_order,
    deliver_order,
    cancel_customer_cod_order,
)
from orders.workspace_services import place_cod_order, submit_return_request, review_return_request
from orders.forms import ReturnRequestForm
from payments.services import record_cod_collection
from payments.models import PaymentStatus


class WorkspaceFixture:
    @classmethod
    def setUpTestData(cls):
        user = get_user_model()
        cls.customer = user.objects.create_user(
            username='owner', first_name='Nguyễn', last_name='Văn An'
        )
        cls.other = user.objects.create_user(username='other')
        cls.staff = user.objects.create_user(username='sales')
        cls.admin = user.objects.create_superuser(
            username='admin', email='admin@example.test', password=None
        )
        group, _ = Group.objects.get_or_create(name='Customer')
        cls.customer.groups.add(group)
        cls.other.groups.add(group)
        sales, _ = Group.objects.get_or_create(name='Sales Staff')
        cls.staff.groups.add(sales)
        category = Category.objects.create(name='Điện thoại', is_visible=True)
        product = Product.objects.create(
            name='iPhone 16 Pro Max Chính Hãng', category=category, is_active=True
        )
        cls.variant = ProductVariant.objects.create(
            product=product,
            sku='IP16-256',
            price=Decimal('32490000'),
            original_price=Decimal('32990000'),
            is_active=True,
        )
        inventory = cls.variant.inventory
        inventory.physical_sellable = 50
        inventory.save()
        cls.cart, _ = Cart.objects.get_or_create(user=cls.customer)

    def make_order(self, *, customer=None, stage='pending', coupon=''):
        customer = customer or self.customer
        cart, _ = Cart.objects.get_or_create(user=customer)
        CartItem.objects.update_or_create(
            cart=cart,
            variant=self.variant,
            defaults={'quantity': 1, 'price_at_addition': self.variant.price},
        )
        data = {
            'recipient_name': 'Nguyễn Văn An',
            'recipient_phone': '0905123456',
            'shipping_address': '71 Ngũ Hành Sơn',
            'province': 'Đà Nẵng',
            'district': 'Ngũ Hành Sơn',
            'ward': 'Mỹ An',
            'shipping_method': 'DELIVERY',
            'shipping_note': 'Giao giờ hành chính',
            'payment_method': 'COD',
            'coupon_code': coupon,
        }
        order = place_cod_order(customer, cart, data, 'QHM' + uuid.uuid4().hex[:14].upper()).order
        if stage in ('confirmed', 'shipping', 'delivered'):
            confirm_order(order.pk, self.staff)
        if stage in ('shipping', 'delivered'):
            ship_order(order.pk, self.staff)
        if stage == 'delivered':
            deliver_order(order.pk, self.staff)
            record_cod_collection(
                order.payment.pk, self.staff, order.grand_total, 'COD-' + order.order_code
            )
        order.refresh_from_db()
        return order

    def request_data(self, order, request_type=ReturnType.EXCHANGE):
        return {
            'request_key': uuid.uuid4(),
            'request_type': request_type,
            'items': [str(i.pk) for i in order.items.all()],
            'reason': ReturnReason.HARDWARE,
            'description': 'Màn hình bị lỗi cảm ứng khi sử dụng.',
        }

    @staticmethod
    def image_upload():
        buffer = BytesIO()
        Image.new('RGB', (8, 8), 'red').save(buffer, format='PNG')
        return SimpleUploadedFile('minh-chung.png', buffer.getvalue(), content_type='image/png')


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class OrderWorkspaceTests(WorkspaceFixture, TestCase):
    def test_five_screens_render_and_are_cod_only(self):
        pending = self.make_order()
        delivered = self.make_order(stage='delivered')
        self.client.force_login(self.customer)
        for name, args, text in [
            ('checkout', [], 'Thông tin người nhận hàng'),
            ('my_orders', [], 'Đơn hàng của tôi'),
            ('order_detail', [pending.pk], 'Tiến trình xử lý'),
            ('return_request', [delivered.pk], 'BƯỚC 1'),
        ]:
            response = self.client.get(reverse('orders:' + name, args=args))
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, text)
            for retired in ('VietQR', 'notify-transfer', 'verification_list', 'Mã QR'):
                self.assertNotContains(response, retired)
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('orders:management_orders_list'), {'selected': pending.pk}
        )
        self.assertContains(response, 'Quản lý đơn hàng &amp; Xử lý vận đơn')
        self.assertContains(response, 'Danh sách đơn đặt hàng')
        self.assertContains(response, 'Xác nhận đơn & Chuyển kho đóng gói')
        self.assertNotContains(response, 'Đơn hàng chờ xác nhận</h1>')
        self.assertEqual(len(response.context['tabs']), 6)

    def test_ownership_and_roles(self):
        order = self.make_order()
        self.client.force_login(self.other)
        for name in ('order_detail', 'return_request', 'print_order'):
            self.assertEqual(
                self.client.get(reverse('orders:' + name, args=[order.pk])).status_code, 404
            )
        self.assertEqual(self.client.get(reverse('orders:management_orders_list')).status_code, 403)
        self.assertEqual(self.client.get(reverse('orders:pending_orders_list')).status_code, 403)
        response = self.client.get(reverse('orders:my_orders'))
        self.assertNotContains(response, order.order_code)
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get(reverse('orders:checkout')).status_code, 403)

    def test_anonymous_needs_login(self):
        for name in ('checkout', 'my_orders', 'management_orders_list'):
            self.assertEqual(self.client.get(reverse('orders:' + name)).status_code, 302)

    def checkout_payload(self):
        CartItem.objects.update_or_create(
            cart=self.cart,
            variant=self.variant,
            defaults={'quantity': 1, 'price_at_addition': self.variant.price},
        )
        self.client.force_login(self.customer)
        form = self.client.get(reverse('orders:checkout')).context['form']
        data = dict(form.initial)
        data.update(
            {
                'recipient_name': 'Nguyễn Văn An',
                'recipient_phone': '0905123456',
                'shipping_address': '71 Ngũ Hành Sơn',
                'province': 'Đà Nẵng',
                'district': 'Ngũ Hành Sơn',
                'ward': 'Mỹ An',
                'shipping_note': 'Gọi trước khi giao',
                'action': 'place',
            }
        )
        return data

    def test_checkout_snapshot_inventory_and_replay(self):
        data = self.checkout_payload()
        response = self.client.post(reverse('orders:checkout'), data)
        self.assertEqual(response.status_code, 302)
        order = Order.objects.get(customer=self.customer)
        self.assertEqual(order.shipping_note, 'Gọi trước khi giao')
        self.assertIn('Mỹ An', order.shipping_address)
        self.assertEqual(order.address_province, 'Đà Nẵng')
        self.assertEqual(order.items.get().original_unit_price, Decimal('32990000'))
        self.assertEqual(
            order.items.get().reservations.get().status, InventoryReservationStatus.ACTIVE
        )
        inventory = self.variant.inventory
        inventory.refresh_from_db()
        self.assertEqual(inventory.reserved, 1)
        self.assertEqual(self.cart.items.count(), 0)
        self.assertEqual(self.client.post(reverse('orders:checkout'), data).status_code, 302)
        self.assertEqual(Order.objects.count(), 1)

    def test_checkout_rejects_tampering_and_stale_quote(self):
        data = self.checkout_payload()
        bad = dict(data, payment_method='QR')
        response = self.client.post(reverse('orders:checkout'), bad)
        self.assertEqual(response.status_code, 200)
        self.assertIn('payment_method', response.context['form'].errors)
        self.assertEqual(Order.objects.count(), 0)
        self.variant.price = Decimal('32000000')
        self.variant.save()
        response = self.client.post(reverse('orders:checkout'), data)
        self.assertContains(response, 'vừa thay đổi')
        self.assertEqual(Order.objects.count(), 0)

    def test_pickup_does_not_require_delivery_address(self):
        data = self.checkout_payload()
        data.update(
            shipping_method='PICKUP', province='', district='', ward='', shipping_address=''
        )
        self.assertEqual(self.client.post(reverse('orders:checkout'), data).status_code, 302)
        self.assertIn('156 Nguyễn Văn Linh', Order.objects.get().shipping_address)

    def test_coupon_usage_preserved_and_inventory_released_once(self):
        coupon = Coupon.objects.create(
            code='QH500',
            discount_type='FIXED',
            value=Decimal('500000'),
            max_uses=20,
            starts_at=timezone.now() - timedelta(days=1),
            ends_at=timezone.now() + timedelta(days=1),
        )
        order = self.make_order(coupon=coupon.code)
        self.assertEqual(order.grand_total, Decimal('31990000'))
        self.assertEqual(order.items.get().line_total, order.grand_total)
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)
        cancel_customer_cod_order(order.pk, self.customer, 'OTHER', 'Đặt nhầm sản phẩm')
        cancel_customer_cod_order(order.pk, self.customer, 'OTHER', 'Đặt nhầm sản phẩm')
        coupon.refresh_from_db()
        self.assertEqual(coupon.used_count, 1)
        self.assertEqual(order.coupon_usage.status, CouponUsageStatus.USED)
        self.assertEqual(OrderCancellation.objects.filter(order=order).count(), 1)
        inventory = self.variant.inventory
        inventory.refresh_from_db()
        self.assertEqual(inventory.reserved, 0)

    def test_admin_filter_selection_and_legacy_redirect(self):
        order = self.make_order()
        self.make_order(stage='shipping')
        self.client.force_login(self.staff)
        url = reverse('orders:management_orders_list')
        response = self.client.get(
            url,
            {
                'status': 'pending',
                'q': order.order_code,
                'month': timezone.localtime().strftime('%Y-%m'),
            },
        )
        self.assertEqual(response.context['page_obj'].paginator.count, 1)
        self.assertEqual(response.context['selected_order'].pk, order.pk)
        response = self.client.get(reverse('orders:pending_orders_list'))
        self.assertRedirects(response, url + '?status=pending')
        self.assertEqual(
            self.client.get(reverse('orders:confirm_order', args=[order.pk])).status_code, 405
        )

    def test_staff_shipping_metadata_and_separate_collection(self):
        order = self.make_order(stage='confirmed')
        self.client.force_login(self.staff)
        url = reverse('orders:management_action', args=[order.pk, 'ship'])
        self.assertEqual(
            self.client.post(
                url,
                {
                    'shipping_note': 'Gọi trước',
                    'carrier_name': 'QH Express',
                    'tracking_number': 'QHE123',
                    'carrier_contact': '0905123456',
                },
            ).status_code,
            302,
        )
        order.refresh_from_db()
        self.assertEqual(order.tracking_number, 'QHE123')
        self.assertEqual(order.order_status, OrderStatus.SHIPPING)
        self.assertEqual(
            self.client.post(reverse('orders:deliver_order', args=[order.pk]), {}).status_code, 302
        )
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.UNPAID)

    def test_staff_cancellation_audits_actual_actor(self):
        order = self.make_order()
        cancel_customer_cod_order(
            order.pk, self.customer, 'OTHER', 'Khách nhờ hủy đơn', actor=self.staff
        )
        self.assertEqual(order.cancellation.requested_by_id, self.staff.pk)
        self.assertEqual(order.status_history.last().changed_by_id, self.staff.pk)

    def test_return_request_snapshot_idempotency_and_deadline(self):
        order = self.make_order(stage='delivered')
        data = self.request_data(order)
        record, created = submit_return_request(self.customer, order.pk, data, [])
        self.assertTrue(created)
        self.assertEqual(record.expected_amount, order.grand_total)
        self.assertEqual(record.pickup_fee, 0)
        self.assertAlmostEqual(
            (record.response_due_at - record.created_at).total_seconds(), 1800, delta=1
        )
        self.assertFalse(submit_return_request(self.customer, order.pk, data, [])[1])
        self.assertEqual(ReturnRequest.objects.count(), 1)

    def test_return_rejects_foreign_item_expired_and_nonhardware_exchange(self):
        order = self.make_order(stage='delivered')
        foreign = self.make_order(customer=self.other)
        data = self.request_data(order)
        data['items'] = [str(foreign.items.get().pk)]
        with self.assertRaises(ValidationError):
            submit_return_request(self.customer, order.pk, data, [])
        data = self.request_data(order)
        data['reason'] = ReturnReason.NO_LONGER_NEEDED
        with self.assertRaises(ValidationError):
            submit_return_request(self.customer, order.pk, data, [])
        Order.objects.filter(pk=order.pk).update(delivered_at=timezone.now() - timedelta(days=31))
        with self.assertRaises(ValidationError):
            submit_return_request(self.customer, order.pk, self.request_data(order), [])

    def test_cancel_request_releases_stock_atomically(self):
        order = self.make_order()
        record, _ = submit_return_request(
            self.customer, order.pk, self.request_data(order, ReturnType.CANCEL), []
        )
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.CANCELLED)
        self.assertEqual(record.status, ReturnStatus.COMPLETED)
        self.assertEqual(record.expected_amount, 0)
        self.assertEqual(
            order.items.get().reservations.get().status, InventoryReservationStatus.RELEASED
        )

    def test_return_upload_validation_and_private_access(self):
        order = self.make_order(stage='delivered')
        data = self.request_data(order)
        bad = SimpleUploadedFile('bad.png', b'<script>alert(1)</script>', content_type='image/png')
        form = ReturnRequestForm(data=data, files={'attachments': [bad]}, order=order)
        self.assertFalse(form.is_valid())
        self.assertIn('attachments', form.errors)
        form = ReturnRequestForm(
            data=data, files={'attachments': [self.image_upload() for _ in range(6)]}, order=order
        )
        self.assertFalse(form.is_valid())
        with tempfile.TemporaryDirectory() as folder, override_settings(PRIVATE_UPLOAD_ROOT=folder):
            record, _ = submit_return_request(self.customer, order.pk, data, [self.image_upload()])
            attachment = record.attachments.get()
            url = reverse('orders:return_attachment', args=[attachment.pk])
            self.client.force_login(self.other)
            self.assertEqual(self.client.get(url).status_code, 404)
            self.client.force_login(self.customer)
            response = self.client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response['Cache-Control'], 'private, no-store')
            response.close()

    def test_refund_admin_only_and_exact_paid_amount(self):
        order = self.make_order(stage='delivered')
        record, _ = submit_return_request(
            self.customer, order.pk, self.request_data(order, ReturnType.REFUND), []
        )
        review_return_request(
            record.pk, self.staff, ReturnStatus.APPROVED, 'Đã tiếp nhận và hẹn thu hồi'
        )
        with self.assertRaises(PermissionDenied):
            review_return_request(
                record.pk, self.staff, ReturnStatus.COMPLETED, 'Đã hoàn tiền', 'REF001', True
            )
        with self.assertRaises(ValidationError):
            review_return_request(
                record.pk, self.admin, ReturnStatus.COMPLETED, 'Đã hoàn tiền', 'REF001', False
            )
        result = review_return_request(
            record.pk, self.admin, ReturnStatus.COMPLETED, 'Đã hoàn tiền thực tế', 'REF001', True
        )
        self.assertEqual(result.refund.amount, order.grand_total)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.REFUNDED)
        self.assertEqual(order.refunds.count(), 1)
        review_return_request(
            record.pk, self.admin, ReturnStatus.COMPLETED, 'Gửi lại chứng từ', 'REF001', True
        )
        self.assertEqual(order.refunds.count(), 1)

    def test_retired_routes_absent(self):
        for url in (
            '/orders/1/notify-transfer/',
            '/payments/verification/',
            '/management/payments/verification/',
        ):
            with self.assertRaises(Resolver404):
                resolve(url)

    def test_return_status_appears_in_admin_tab(self):
        order = self.make_order(stage='delivered')
        submit_return_request(self.customer, order.pk, self.request_data(order), [])
        self.client.force_login(self.staff)
        response = self.client.get(
            reverse('orders:management_orders_list'), {'status': 'cancelled'}
        )
        self.assertContains(response, order.order_code)
        self.assertEqual(response.context['selected_order'].ui_status, 'Yêu cầu đổi / trả')
        completed = self.client.get(
            reverse('orders:management_orders_list'), {'status': 'completed'}
        )
        self.assertEqual(completed.context['page_obj'].paginator.count, 0)

    def test_refund_requires_actual_collection(self):
        order = self.make_order(stage='shipping')
        deliver_order(order.pk, self.staff)
        order.refresh_from_db()
        with self.assertRaises(ValidationError):
            submit_return_request(
                self.customer, order.pk, self.request_data(order, ReturnType.REFUND), []
            )
        self.assertEqual(ReturnRequest.objects.count(), 0)

    def test_shipping_replay_does_not_decrement_inventory_twice(self):
        order = self.make_order(stage='shipping')
        inventory = self.variant.inventory
        inventory.refresh_from_db()
        before = (inventory.physical_sellable, inventory.reserved)
        ship_order(order.pk, self.staff)
        inventory.refresh_from_db()
        self.assertEqual((inventory.physical_sellable, inventory.reserved), before)
        self.assertEqual(order.inventory_transactions.count(), 1)

    def test_pickup_handover_does_not_need_external_tracking(self):
        order = self.make_order(stage='confirmed')
        Order.objects.filter(pk=order.pk).update(shipping_method='PICKUP')
        self.client.force_login(self.staff)
        response = self.client.post(
            reverse('orders:management_action', args=[order.pk, 'ship']), {}
        )
        self.assertEqual(response.status_code, 302)
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.SHIPPING)

    def test_review_form_only_offers_permitted_actions(self):
        from orders.forms import ReturnReviewForm

        self.assertNotIn('COMPLETED', dict(ReturnReviewForm().fields['decision'].choices))
        self.assertNotIn('refund_reference', ReturnReviewForm().fields)
        self.assertIn(
            'COMPLETED', dict(ReturnReviewForm(allow_complete=True).fields['decision'].choices)
        )
