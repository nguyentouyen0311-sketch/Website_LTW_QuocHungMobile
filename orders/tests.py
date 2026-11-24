from datetime import timedelta
from decimal import Decimal
from types import SimpleNamespace

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from cart.models import Cart, CartItem
from catalog.models import Category, Product, ProductVariant
from coupons.models import Coupon, CouponUsageStatus, DiscountType
from inventory.models import Inventory, InventoryReservationStatus, InventoryTransaction
from orders.forms import CheckoutForm
from orders.models import (
    CancellationReasonCode, Order, OrderStatus, ReturnReasonCode,
    ReturnRequest, ReturnRequestStatus, ReturnRequestType,
)
from orders.services import (
    cancel_customer_cod_order, confirm_order, create_cod_order_from_cart,
    create_cod_order_with_coupon_from_cart, deliver_order, ship_order,
)
from orders.views import _get_initial_checkout_data, _refresh_quote_token
from payments.cod_services import record_cod_collection
from payments.models import CODCollection, PaymentMethod, PaymentStatus, RefundStatus
from payments.return_refund_services import create_return_refund_request, confirm_return_refund


class CheckoutQuoteTokenTests(SimpleTestCase):
    def test_checkout_prefills_vietnamese_name_in_family_name_first_order(self):
        request = SimpleNamespace(
            user=SimpleNamespace(
                first_name='A',
                last_name='Nguyễn Văn',
                username='khach_hang',
            )
        )

        initial_data = _get_initial_checkout_data(request, db_available=False)

        self.assertEqual(initial_data['recipient_name'], 'Nguyễn Văn A')

    def test_refreshes_hidden_quote_token_on_bound_form(self):
        form = CheckoutForm(data={
            'recipient_name': 'Khách hàng',
            'recipient_phone': '0976146821',
            'shipping_address': '71 Ngũ Hành Sơn, Đà Nẵng',
            'payment_method': 'COD',
            'checkout_token': 'checkout-token',
            'quote_token': 'stale-token',
        })

        _refresh_quote_token(form, SimpleNamespace(quote_token='fresh-token'))

        self.assertEqual(form.data['quote_token'], 'fresh-token')
        self.assertEqual(form.initial['quote_token'], 'fresh-token')

    def test_coupon_is_a_dropdown_with_only_supplied_choices(self):
        form = CheckoutForm(eligible_coupon_choices=[
            ('PUBLIC10', 'PUBLIC10 — Giảm 10.000 đ'),
        ])

        self.assertEqual(form.fields['coupon_code'].widget.__class__.__name__, 'Select')
        self.assertEqual(
            list(form.fields['coupon_code'].choices),
            [
                ('', 'Không áp dụng mã giảm giá'),
                ('PUBLIC10', 'PUBLIC10 — Giảm 10.000 đ'),
            ],
        )

    def test_coupon_dropdown_rejects_codes_outside_current_choices(self):
        form = CheckoutForm(data={
            'recipient_name': 'Khách hàng',
            'recipient_phone': '0976146821',
            'shipping_address': '71 Ngũ Hành Sơn, Đà Nẵng',
            'coupon_code': 'PRIVATE20',
            'payment_method': 'COD',
            'checkout_token': 'checkout-token',
        }, eligible_coupon_choices=[('PUBLIC10', 'PUBLIC10 — Giảm 10.000 đ')])

        self.assertFalse(form.is_valid())
        self.assertIn('coupon_code', form.errors)


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CODOnlyOrderTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        customer_role = Group.objects.create(name='Customer')
        sales_role = Group.objects.create(name='Sales Staff')
        cls.customer = get_user_model().objects.create_user('cod_customer', password='test-password')
        cls.customer.groups.add(customer_role)
        cls.sales = get_user_model().objects.create_user('cod_sales', password='test-password')
        cls.sales.groups.add(sales_role)
        cls.admin = get_user_model().objects.create_superuser('cod_admin', password='test-password')
        category = Category.objects.create(name='Điện thoại', is_visible=True)
        product = Product.objects.create(name='Điện thoại COD', category=category, is_active=True)
        cls.variant = ProductVariant.objects.create(
            product=product, sku='COD-256', combination_key='256', price=Decimal('1000000'),
        )
        Inventory.objects.filter(variant=cls.variant).update(physical_sellable=10, reserved=0)
        cls.cart = Cart.objects.create(user=cls.customer)
        CartItem.objects.create(cart=cls.cart, variant=cls.variant, quantity=2)

    def order_data(self, code='COD-TEST-01'):
        return {
            'customer': self.customer,
            'recipient_name': 'Nguyễn Văn A',
            'recipient_phone': '0987654321',
            'shipping_address': '71 Ngũ Hành Sơn, Đà Nẵng',
            'order_code': code,
            'cart': self.cart,
        }

    def test_cod_checkout_then_confirmation_shipping_delivery_and_collection(self):
        self.client.force_login(self.customer)
        checkout = self.client.get(reverse('orders:checkout'))
        self.assertEqual(checkout.status_code, 200)
        form = checkout.context['form']
        self.assertEqual([value for value, _ in form.fields['payment_method'].choices], ['COD'])
        data = {key: value for key, value in self.order_data().items() if key.startswith('recipient_') or key == 'shipping_address'}
        data.update(payment_method='COD', checkout_token=form.initial['checkout_token'], quote_token=form.initial['quote_token'])
        response = self.client.post(reverse('orders:checkout'), data)
        order = Order.objects.get()
        self.assertRedirects(response, reverse('orders:order_detail', args=[order.pk]))
        self.assertEqual(order.order_status, OrderStatus.PENDING_CONFIRMATION)
        self.assertEqual(order.payment.method, PaymentMethod.COD)
        self.assertFalse(self.cart.items.exists())
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 2)

        confirmed = confirm_order(order.pk, self.sales)
        self.assertEqual(confirmed.order.order_status, OrderStatus.CONFIRMED)
        ship_order(order.pk, self.sales)
        ship_order(order.pk, self.sales)
        inventory = Inventory.objects.get(variant=self.variant)
        self.assertEqual((inventory.physical_sellable, inventory.reserved), (8, 0))
        self.assertEqual(InventoryTransaction.objects.filter(order=order).count(), 1)
        self.assertEqual(order.items.get().reservations.get().status, InventoryReservationStatus.SHIPPED)
        deliver_order(order.pk, self.sales)
        deliver_order(order.pk, self.sales)
        receipt = record_cod_collection(order.payment.pk, self.sales, order.grand_total, 'COD-RECEIPT-01')
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.DELIVERED)
        self.assertEqual(order.payment.status, PaymentStatus.PAID)
        self.assertEqual(receipt.amount, order.grand_total)

    def test_coupon_is_consumed_without_reserving_quota_and_replay_is_safe(self):
        now = timezone.now()
        coupon = Coupon.objects.create(
            code='COD10', discount_type=DiscountType.FIXED, value=Decimal('100000'),
            max_uses=10, starts_at=now-timedelta(days=1), ends_at=now+timedelta(days=1),
        )
        data = self.order_data()
        result = create_cod_order_with_coupon_from_cart(**data, coupon_code=coupon.code)
        replay = create_cod_order_with_coupon_from_cart(**data, coupon_code=coupon.code)
        coupon.refresh_from_db()
        self.assertTrue(replay.is_replay)
        self.assertEqual(replay.order.pk, result.order.pk)
        self.assertEqual((coupon.used_count, coupon.reserved_count), (1, 0))
        self.assertEqual(result.order.coupon_usage.status, CouponUsageStatus.USED)
        self.assertEqual(result.order.grand_total, Decimal('1900000'))

    def test_customer_cod_cancellation_releases_stock_without_double_release(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        confirm_order(order.pk, self.sales)
        result = cancel_customer_cod_order(order.pk, self.customer, CancellationReasonCode.WRONG_ORDER)
        replay = cancel_customer_cod_order(order.pk, self.customer, CancellationReasonCode.WRONG_ORDER)
        self.assertEqual(result.order.order_status, OrderStatus.CANCELLED)
        self.assertTrue(replay.is_replay)
        inventory = Inventory.objects.get(variant=self.variant)
        self.assertEqual((inventory.physical_sellable, inventory.reserved), (10, 0))
        self.assertEqual(order.items.get().reservations.get().status, InventoryReservationStatus.RELEASED)

    def test_paid_cod_order_can_still_be_refunded_after_return(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        confirm_order(order.pk, self.sales)
        ship_order(order.pk, self.sales)
        deliver_order(order.pk, self.sales)
        record_cod_collection(order.payment.pk, self.sales, order.grand_total, 'COD-RETURN-01')
        return_request = ReturnRequest.objects.create(
            order=order, order_item=order.items.get(), customer=self.customer,
            request_type=ReturnRequestType.RETURN, reason_code=ReturnReasonCode.WRONG_ITEM,
            quantity=2, status=ReturnRequestStatus.COMPLETED,
        )
        refund, created = create_return_refund_request(return_request.pk, self.admin)
        self.assertTrue(created)
        refund, confirmed = confirm_return_refund(
            refund.pk, self.admin, 'RETURN-REFUND-01', timezone.now(), 'Đã hoàn tiền trả hàng COD.',
        )
        self.assertTrue(confirmed)
        self.assertEqual(refund.status, RefundStatus.COMPLETED)
        self.assertEqual(refund.amount, order.grand_total)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.REFUNDED)

    def test_unsupported_payment_checkout_is_rejected_without_creating_order(self):
        self.client.force_login(self.customer)
        checkout = self.client.get(reverse('orders:checkout'))
        form = checkout.context['form']
        data = {key: value for key, value in self.order_data().items() if key.startswith('recipient_') or key == 'shipping_address'}
        data.update(payment_method='INVALID', checkout_token=form.initial['checkout_token'], quote_token=form.initial['quote_token'])
        response = self.client.post(reverse('orders:checkout'), data)
        self.assertEqual(response.status_code, 200)
        self.assertIn('payment_method', response.context['form'].errors)
        self.assertFalse(Order.objects.exists())
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 0)
        self.assertTrue(self.cart.items.exists())

    def test_unsupported_method_is_rejected_by_model_and_database(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        payment = order.payment
        payment.method = 'INVALID'
        with self.assertRaises(ValidationError) as error:
            payment.full_clean()
        self.assertIn('method', error.exception.message_dict)
        with self.assertRaises(IntegrityError), transaction.atomic():
            payment.save(update_fields=['method'])
        payment.refresh_from_db()
        self.assertEqual(payment.method, PaymentMethod.COD)
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 2)

    def test_cancellation_rejects_cod_receipt_even_if_payment_status_is_inconsistent(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        CODCollection.objects.create(
            payment=order.payment, amount=order.grand_total, reference='INCONSISTENT-COD-01',
            collected_by=self.sales, collected_at=timezone.now(),
        )
        with self.assertRaises(ValidationError) as error:
            cancel_customer_cod_order(order.pk, self.customer, CancellationReasonCode.WRONG_ORDER)
        self.assertEqual(error.exception.code, 'existing_cod_collection')
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.PENDING_CONFIRMATION)
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 2)


    def test_order_detail_accepts_get_and_rejects_post(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        self.client.force_login(self.customer)
        url = reverse('orders:order_detail', args=[order.pk])
        self.assertEqual(self.client.get(url).status_code, 200)
        self.assertEqual(self.client.post(url).status_code, 405)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.UNPAID)

    def test_management_urls_complete_cod_workflow_after_module_split(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        self.client.force_login(self.sales)
        detail_url = reverse('orders:management_detail', args=[order.pk])
        self.assertEqual(self.client.get(reverse('orders:pending_orders_list')).status_code, 200)
        self.assertEqual(self.client.get(detail_url).status_code, 200)

        for action, expected_status in (
            ('confirm_order', OrderStatus.CONFIRMED),
            ('ship_order', OrderStatus.SHIPPING),
            ('deliver_order', OrderStatus.DELIVERED),
        ):
            with self.subTest(action=action):
                response = self.client.post(reverse(f'orders:{action}', args=[order.pk]))
                self.assertRedirects(response, detail_url)
                order.refresh_from_db()
                self.assertEqual(order.order_status, expected_status)

        response = self.client.post(reverse('orders:collect_cod', args=[order.pk]), {
            'amount': str(order.grand_total), 'reference': 'COD-HTTP-01', 'confirm': 'on',
        })
        self.assertRedirects(response, detail_url)
        order.payment.refresh_from_db()
        self.assertEqual(order.payment.status, PaymentStatus.PAID)
        self.assertEqual(self.client.get(reverse('orders:return_request_management')).status_code, 200)

        self.client.force_login(self.customer)
        for url in (
            reverse('orders:customer_orders_list'),
            reverse('orders:purchased_product_review', args=[order.pk, order.items.get().pk]),
            reverse('orders:return_request_create', args=[order.pk]),
        ):
            with self.subTest(url=url):
                self.assertEqual(self.client.get(url).status_code, 200)

    def test_customer_cancel_urls_preserve_validation_and_stock_release(self):
        order = create_cod_order_from_cart(**self.order_data()).order
        self.client.force_login(self.customer)
        cancel_url = reverse('orders:cancel_order', args=[order.pk])
        self.assertEqual(self.client.get(cancel_url).status_code, 200)
        invalid = self.client.post(cancel_url, {'reason_code': CancellationReasonCode.OTHER})
        self.assertEqual(invalid.status_code, 200)
        self.assertIn('description', invalid.context['cancel_form'].errors)
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.PENDING_CONFIRMATION)

        response = self.client.post(cancel_url, {'reason_code': CancellationReasonCode.WRONG_ORDER})
        self.assertRedirects(response, reverse('orders:order_detail', args=[order.pk]))
        order.refresh_from_db()
        self.assertEqual(order.order_status, OrderStatus.CANCELLED)
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 0)
