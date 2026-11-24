import uuid
from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.exceptions import ValidationError
from django.test import TestCase
from django.urls import reverse

from catalog.models import Category, Product, ProductVariant
from inventory.models import Inventory, InventoryAdjustment, InventoryTransaction
from inventory.services import (
    generate_adjustment_token, generate_receiving_token, receive_purchase,
    verify_adjustment_token, verify_receiving_token,
)
from orders.models import Order, OrderStatus


class InventoryFixtures:
    @classmethod
    def setUpTestData(cls):
        users = get_user_model()
        cls.operator = users.objects.create_user(username='stock_operator')
        cls.operator.groups.add(Group.objects.get_or_create(name='Inventory Staff')[0])
        cls.admin = users.objects.create_user(username='stock_admin', is_superuser=True, is_staff=True)
        cls.customer = users.objects.create_user(username='stock_customer')
        cls.customer.groups.add(Group.objects.get_or_create(name='Customer')[0])
        cls.sales = users.objects.create_user(username='stock_sales')
        cls.sales.groups.add(Group.objects.get_or_create(name='Sales Staff')[0])
        cls.category = Category.objects.create(name='Thiết bị kiểm thử kho')
        cls.product = Product.objects.create(name='Thiết bị STOCK chính', category=cls.category)
        cls.variant = ProductVariant.objects.create(product=cls.product, sku='STOCK-PRIMARY', price=Decimal('1000000'))
        cls.inventory = Inventory.objects.get(variant=cls.variant)
        Inventory.objects.filter(pk=cls.inventory.pk).update(physical_sellable=10, reserved=3, min_threshold=5)
        cls.inventory.refresh_from_db()
        other_product = Product.objects.create(name='Thiết bị STOCK phụ', category=cls.category)
        cls.other_variant = ProductVariant.objects.create(product=other_product, sku='STOCK-SECONDARY', price=Decimal('2000000'))
        cls.other_inventory = Inventory.objects.get(variant=cls.other_variant)
        Inventory.objects.filter(pk=cls.other_inventory.pk).update(physical_sellable=4, min_threshold=1)
        cls.other_inventory.refresh_from_db()

    def setUp(self):
        self.client.force_login(self.operator)


class InventoryQuickUpdateTests(InventoryFixtures, TestCase):
    def list_response(self, **params):
        return self.client.get(reverse('inventory:inventory_list'), params)

    def receive_url(self, inventory=None):
        return reverse('inventory:inventory_receive', args=[(inventory or self.inventory).pk])

    def adjust_url(self):
        return reverse('inventory:inventory_adjust', args=[self.inventory.pk])

    def test_list_provides_selection_and_valid_tokens_for_every_row(self):
        response = self.list_response(selected=str(self.inventory.pk))
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context['selected_inventory'].pk, self.inventory.pk)
        for inventory in response.context['inventory_list']:
            verify_receiving_token(inventory.recv_token, inventory.pk, self.operator.pk)
            payload = verify_adjustment_token(inventory.adj_token, inventory.pk, self.operator.pk)
            self.assertEqual(payload['snapshot_physical'], inventory.physical_sellable)
        self.assertTrue(response.context['receive_form'].initial['request_token'])
        self.assertTrue(response.context['adjust_form'].initial['request_token'])
        self.assertContains(response, f'data-receive-url="{self.receive_url()}"')
        self.assertContains(response, f'data-adjust-url="{self.adjust_url()}"')
        self.assertEqual(response.context['total_physical_count'], 14)
        self.assertEqual(response.context['total_reserved_count'], 3)
        self.assertEqual(response.context['total_available_count'], 11)
        self.assertEqual(response.context['total_holding_count'], 1)

    def test_selected_row_can_be_kept_outside_current_filtered_results(self):
        response = self.list_response(q='SECONDARY', selected=str(self.inventory.pk))
        self.assertEqual([row.pk for row in response.context['inventory_list']], [self.other_inventory.pk])
        selected = response.context['selected_inventory']
        self.assertEqual(selected.pk, self.inventory.pk)
        verify_receiving_token(selected.recv_token, selected.pk, self.operator.pk)

    def test_inventory_codes_and_holding_filter_keep_selected_action_panel(self):
        response = self.list_response(q=f'TK{self.inventory.pk:03d}', alert='holding')
        self.assertEqual([row.pk for row in response.context['inventory_list']], [self.inventory.pk])
        self.assertEqual(response.context['selected_inventory'].pk, self.inventory.pk)
        empty = self.list_response(q='NO-SUCH-INVENTORY')
        self.assertEqual(empty.status_code, 200)
        self.assertIsNone(empty.context['selected_inventory'])

    def test_receiving_from_list_preserves_filters_and_does_not_double_add(self):
        response = self.list_response(selected=str(self.inventory.pk))
        token = response.context['receive_form'].initial['request_token']
        data = {'quantity': '4', 'request_token': token, 'next': '/inventory/?q=STOCK&alert=holding&page=1'}
        for _ in range(2):
            result = self.client.post(self.receive_url(), data)
            self.assertEqual(result.status_code, 302)
            self.assertEqual(urlsplit(result.url).path, '/inventory/')
            self.assertEqual(parse_qs(urlsplit(result.url).query), {
                'q': ['STOCK'], 'alert': ['holding'], 'page': ['1'], 'selected': [str(self.inventory.pk)],
            })
            self.assertEqual(self.client.get(result.url).status_code, 200)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.inventory.reserved, self.inventory.available), (14, 3, 11))
        transaction = self.inventory.transactions.get()
        self.assertEqual((transaction.quantity_delta, transaction.physical_before, transaction.physical_after), (4, 10, 14))

    def test_stocktake_from_list_keeps_reserved_stock_and_one_ledger_entry(self):
        response = self.list_response(selected=str(self.inventory.pk))
        data = {
            'counted_quantity': '8', 'reason': 'Kiểm kê định kỳ',
            'request_token': response.context['adjust_form'].initial['request_token'], 'next': '/inventory/',
        }
        for _ in range(2):
            result = self.client.post(self.adjust_url(), data)
            self.assertRedirects(result, f'/inventory/?selected={self.inventory.pk}')
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.inventory.reserved, self.inventory.available), (8, 3, 5))
        adjustment = InventoryAdjustment.objects.get(inventory=self.inventory)
        self.assertEqual((adjustment.old_quantity, adjustment.new_quantity), (10, 8))
        self.assertEqual(self.inventory.transactions.count(), 1)
        self.assertEqual(adjustment.transaction.quantity_delta, -2)

    def test_invalid_receiving_returns_to_list_without_changing_stock(self):
        for value in ('0', '-2', 'invalid'):
            with self.subTest(quantity=value):
                token, _ = generate_receiving_token(self.inventory.pk, self.operator.pk)
                result = self.client.post(self.receive_url(), {'quantity': value, 'request_token': token, 'next': '/inventory/'})
                self.assertEqual(result.status_code, 302)
                self.assertContains(self.client.get(result.url), 'Lỗi nhập kho:')
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.physical_sellable, 10)
        self.assertFalse(self.inventory.transactions.exists())

    def test_below_reserved_or_missing_reason_cannot_change_stock(self):
        for quantity, reason in (('2', 'Hàng hỏng'), ('8', '')):
            with self.subTest(quantity=quantity, reason=reason):
                token, _ = generate_adjustment_token(self.inventory, self.operator.pk)
                result = self.client.post(self.adjust_url(), {
                    'counted_quantity': quantity, 'reason': reason, 'request_token': token, 'next': '/inventory/',
                })
                self.assertEqual(result.status_code, 302)
                self.assertContains(self.client.get(result.url), 'Lỗi điều chỉnh kiểm kê:')
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.inventory.reserved), (10, 3))
        self.assertFalse(self.inventory.transactions.exists())

    def test_stale_stocktake_returns_fresh_tokens_without_overwriting_new_stock(self):
        old_token, _ = generate_adjustment_token(self.inventory, self.operator.pk)
        receive_token, _ = generate_receiving_token(self.inventory.pk, self.operator.pk)
        receive_purchase(self.inventory.pk, 2, receive_token, self.operator)
        result = self.client.post(self.adjust_url(), {
            'counted_quantity': '8', 'reason': 'Kiểm kê', 'request_token': old_token, 'next': '/inventory/',
        })
        self.assertEqual(result.status_code, 302)
        refreshed = self.client.get(result.url)
        token = refreshed.context['adjust_form'].initial['request_token']
        self.assertEqual(verify_adjustment_token(token, self.inventory.pk, self.operator.pk)['snapshot_physical'], 12)
        self.assertContains(refreshed, 'Tồn kho đã thay đổi')
        self.inventory.refresh_from_db()
        self.assertEqual(self.inventory.physical_sellable, 12)
        self.assertEqual(self.inventory.transactions.count(), 1)

    def test_token_cannot_be_used_for_another_inventory_or_different_payload(self):
        token, _ = generate_receiving_token(self.inventory.pk, self.operator.pk)
        result = self.client.post(self.receive_url(self.other_inventory), {'quantity': '4', 'request_token': token})
        self.assertEqual(result.status_code, 200)
        self.assertTrue(result.context['form'].errors)
        self.client.post(self.receive_url(), {'quantity': '4', 'request_token': token})
        conflict = self.client.post(self.receive_url(), {'quantity': '7', 'request_token': token})
        self.assertEqual(conflict.status_code, 200)
        self.assertTrue(conflict.context['form'].errors)
        self.inventory.refresh_from_db()
        self.other_inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.other_inventory.physical_sellable), (14, 4))
        self.assertEqual(InventoryTransaction.objects.count(), 1)

    def test_standalone_receiving_still_redirects_to_detail(self):
        page = self.client.get(self.receive_url())
        self.assertEqual(page.status_code, 200)
        result = self.client.post(self.receive_url(), {'quantity': '1', 'request_token': page.context['form'].initial['request_token']})
        self.assertRedirects(result, reverse('inventory:inventory_detail', args=[self.inventory.pk]))

    def test_external_return_url_cannot_redirect_outside_inventory(self):
        token, _ = generate_receiving_token(self.inventory.pk, self.operator.pk)
        result = self.client.post(self.receive_url(), {
            'quantity': '1', 'request_token': token, 'next': 'https://example.invalid/inventory/',
        })
        self.assertRedirects(result, reverse('inventory:inventory_detail', args=[self.inventory.pk]))

    def test_updating_threshold_does_not_change_stock_or_create_transactions(self):
        url = reverse('inventory:inventory_update_threshold', args=[self.inventory.pk])
        self.assertEqual(self.client.get(url).status_code, 405)
        self.assertRedirects(self.client.post(url, {'min_threshold': '9'}), reverse('inventory:inventory_detail', args=[self.inventory.pk]))
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.min_threshold, self.inventory.physical_sellable, self.inventory.reserved), (9, 10, 3))
        self.assertFalse(self.inventory.transactions.exists())

    def test_customers_and_sales_cannot_mutate_inventory(self):
        token, _ = generate_receiving_token(self.inventory.pk, self.operator.pk)
        for user in (self.customer, self.sales):
            self.client.force_login(user)
            for url, data in (
                (self.receive_url(), {'quantity': '5', 'request_token': token}),
                (self.adjust_url(), {'counted_quantity': '8', 'reason': 'Test', 'request_token': token}),
                (reverse('inventory:inventory_update_threshold', args=[self.inventory.pk]), {'min_threshold': '99'}),
            ):
                with self.subTest(user=user.pk, url=url):
                    self.assertEqual(self.client.post(url, data).status_code, 403)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.inventory.reserved, self.inventory.min_threshold), (10, 3, 5))


class InventoryHistoryTests(InventoryFixtures, TestCase):
    @classmethod
    def setUpTestData(cls):
        super().setUpTestData()
        order = Order.objects.create(
            customer=cls.customer, order_code='STOCK-HISTORY-ORDER', recipient_name='Khách kiểm thử',
            recipient_phone='0987654321', shipping_address='Địa chỉ kiểm thử', order_status=OrderStatus.SHIPPING,
            subtotal=Decimal('1000000'), discount_total=Decimal('0'), grand_total=Decimal('1000000'),
        )
        cls.transactions = {}
        for kind, before, after in (
            (InventoryTransaction.TYPE_PURCHASE_IN, 0, 10),
            (InventoryTransaction.TYPE_ADJUSTMENT, 10, 8),
            (InventoryTransaction.TYPE_SHIP, 8, 7),
            (InventoryTransaction.TYPE_RETURN_RESTOCK, 7, 8),
        ):
            cls.transactions[kind] = InventoryTransaction.objects.create(
                inventory=cls.inventory, transaction_type=kind, quantity_delta=after-before,
                physical_before=before, physical_after=after, performed_by=cls.operator,
                request_key=uuid.uuid4(), order=order if kind == InventoryTransaction.TYPE_SHIP else None,
            )
        InventoryAdjustment.objects.create(
            inventory=cls.inventory, old_quantity=10, new_quantity=8, reason='Hư hỏng khi kiểm kê',
            adjusted_by=cls.operator, transaction=cls.transactions[InventoryTransaction.TYPE_ADJUSTMENT],
        )

    def history(self, **params):
        return self.client.get(reverse('inventory:inventory_history'), params)

    def test_history_loads_for_inventory_staff_and_admin_with_counters(self):
        for user in (self.operator, self.admin):
            self.client.force_login(user)
            response = self.history()
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.context['total_tx_count'], 4)
            for key in ('count_purchase', 'count_adjust', 'count_ship', 'count_return'):
                self.assertEqual(response.context[key], 1)
            self.assertContains(response, 'Sổ cái Lịch sử biến động kho')
            self.assertEqual({tx.pk for tx in response.context['transaction_list']}, {tx.pk for tx in self.transactions.values()})

    def test_all_four_visible_type_filters_actually_filter_transactions(self):
        for kind, transaction in self.transactions.items():
            with self.subTest(kind=kind):
                response = self.history(type=kind)
                self.assertEqual(response.context['tx_type'], kind)
                self.assertEqual(list(response.context['transaction_list']), [transaction])
                self.assertEqual(response.context['filtered_count'], 1)
        invalid = self.history(type='invalid')
        self.assertEqual(invalid.context['tx_type'], 'ALL')
        self.assertEqual(invalid.context['filtered_count'], 4)

    def test_search_by_sku_product_code_uuid_and_adjustment_reason(self):
        shipment = self.transactions[InventoryTransaction.TYPE_SHIP]
        for query, expected in (
            (self.variant.sku, 4), (self.product.product_code, 4),
            (str(shipment.request_key), 1), ('Hư hỏng', 1), ('not-a-valid-uuid-or-sku', 0),
        ):
            with self.subTest(query=query):
                response = self.history(q=query)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.context['filtered_count'], expected)
        combined = self.history(q=self.variant.sku, type=InventoryTransaction.TYPE_SHIP)
        self.assertEqual(list(combined.context['transaction_list']), [shipment])

    def test_history_redirects_guests_and_forbids_non_inventory_roles(self):
        self.client.logout()
        self.assertEqual(self.history().status_code, 302)
        for user in (self.customer, self.sales):
            self.client.force_login(user)
            self.assertEqual(self.history().status_code, 403)

    def test_history_pagination_keeps_transactions_visible(self):
        for index in range(23):
            InventoryTransaction.objects.create(
                inventory=self.inventory, transaction_type=InventoryTransaction.TYPE_PURCHASE_IN,
                quantity_delta=1, physical_before=index, physical_after=index+1,
                performed_by=self.operator, request_key=uuid.uuid4(),
            )
        first = self.history(page=1)
        second = self.history(page=2)
        self.assertEqual(len(first.context['transaction_list']), 25)
        self.assertEqual(len(second.context['transaction_list']), 2)
        self.assertFalse({tx.pk for tx in first.context['transaction_list']} & {tx.pk for tx in second.context['transaction_list']})

    def test_reading_history_does_not_change_stock_or_mutate_ledger(self):
        before = (self.inventory.physical_sellable, self.inventory.reserved)
        self.history(q=self.variant.sku)
        self.inventory.refresh_from_db()
        self.assertEqual((self.inventory.physical_sellable, self.inventory.reserved), before)
        transaction = self.transactions[InventoryTransaction.TYPE_PURCHASE_IN]
        with self.assertRaises(ValidationError):
            transaction.delete()
        with self.assertRaises(ValidationError):
            transaction.save()
        self.assertEqual(InventoryTransaction.objects.count(), 4)
