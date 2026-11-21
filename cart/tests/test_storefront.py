from decimal import Decimal

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import Client, TestCase, override_settings
from django.urls import reverse

from cart.models import Cart, CartItem
from catalog.models import Category, Product, ProductVariant
from inventory.models import Inventory
from orders.checkout_helpers import calculate_server_quote


@override_settings(PASSWORD_HASHERS=['django.contrib.auth.hashers.MD5PasswordHasher'])
class CartStorefrontTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        role = Group.objects.create(name='Customer')
        cls.customer = get_user_model().objects.create_user('cart_customer', password='test-password')
        cls.customer.groups.add(role)
        cls.other = get_user_model().objects.create_user('other_customer', password='test-password')
        cls.other.groups.add(role)
        cls.category = Category.objects.create(name='Điện thoại', is_visible=True)
        cls.product = Product.objects.create(name='Điện thoại kiểm thử', category=cls.category, is_active=True)
        cls.variant = ProductVariant.objects.create(product=cls.product, sku='CART-256', combination_key='256', price=Decimal('1000000'), original_price=Decimal('1200000'))
        cls.alternative = ProductVariant.objects.create(product=cls.product, sku='CART-512', combination_key='512', price=Decimal('1500000'))
        for variant in (cls.variant, cls.alternative):
            Inventory.objects.update_or_create(variant=variant, defaults={'physical_sellable': 10, 'reserved': 2})
        cls.cart = Cart.objects.create(user=cls.customer)
        cls.item = CartItem.objects.create(cart=cls.cart, variant=cls.variant, quantity=2, price_at_addition=cls.variant.price)
        cls.other_cart = Cart.objects.create(user=cls.other)
        cls.foreign_item = CartItem.objects.create(cart=cls.other_cart, variant=cls.variant, quantity=1)

    def setUp(self):
        self.client.force_login(self.customer)

    def test_empty_guest_cart_has_no_side_effects(self):
        guest = Client()
        count = Cart.objects.count()
        response = guest.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Giỏ hàng đang trống')
        self.assertEqual(Cart.objects.count(), count)

    def test_render_real_data_without_voucher_controls(self):
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertTemplateUsed(response, 'cart/detail.html')
        self.assertContains(response, self.product.name)
        self.assertContains(response, '2.000.000')
        for text in ('Mã giảm giá', 'coupon', 'voucher', 'Áp dụng'):
            self.assertNotContains(response, text)
        self.assertEqual(response.context['evaluation']['total_quantity'], 2)

    def test_admin_cannot_use_customer_cart(self):
        admin = get_user_model().objects.create_superuser('cart_admin', password='test-password')
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse('cart:cart_detail')).status_code, 403)
        self.assertEqual(self.client.post(reverse('cart:cart_remove_selected'), {'item_ids': [self.item.pk]}).status_code, 403)

    def test_mutations_require_post(self):
        for name in ('cart_update_quantity', 'cart_switch_variant', 'cart_remove_item'):
            self.assertEqual(self.client.get(reverse('cart:' + name, args=[self.item.pk])).status_code, 405)
        self.assertEqual(self.client.get(reverse('cart:cart_remove_selected')).status_code, 405)

    def test_quantity_updates_persist_and_do_not_reserve_stock(self):
        response = self.client.post(reverse('cart:cart_update_quantity', args=[self.item.pk]), {'quantity': '3'})
        self.assertRedirects(response, reverse('cart:cart_detail'))
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 3)
        self.assertEqual(Inventory.objects.get(variant=self.variant).reserved, 2)

    def test_invalid_quantity_is_retained_without_changing_saved_quantity(self):
        for value in ('0', '-1', '1.5', 'abc'):
            response = self.client.post(reverse('cart:cart_update_quantity', args=[self.item.pk]), {'quantity': value})
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, f'value="{value}"')
            self.item.refresh_from_db()
            self.assertEqual(self.item.quantity, 2)

    def test_excess_stock_quantity_rejected_and_retained(self):
        response = self.client.post(reverse('cart:cart_update_quantity', args=[self.item.pk]), {'quantity': '9'})
        self.assertContains(response, 'vượt quá tồn kho')
        self.assertContains(response, 'value="9"')
        self.item.refresh_from_db()
        self.assertEqual(self.item.quantity, 2)

    def test_foreign_rows_cannot_be_updated_removed_or_switched(self):
        for name, data in (('cart_update_quantity', {'quantity': '1'}), ('cart_remove_item', {}), ('cart_switch_variant', {'new_variant_id': self.alternative.pk})):
            response = self.client.post(reverse('cart:' + name, args=[self.foreign_item.pk]), data)
            self.assertEqual(response.status_code, 404)

    def test_switch_to_existing_variant_merges_rows(self):
        destination = CartItem.objects.create(cart=self.cart, variant=self.alternative, quantity=1)
        response = self.client.post(reverse('cart:cart_switch_variant', args=[self.item.pk]), {'new_variant_id': self.alternative.pk})
        self.assertRedirects(response, reverse('cart:cart_detail'))
        destination.refresh_from_db()
        self.assertEqual(destination.quantity, 3)
        self.assertFalse(CartItem.objects.filter(pk=self.item.pk).exists())

    def test_switch_variant_changes_persisted_variant_and_price(self):
        self.client.post(reverse('cart:cart_switch_variant', args=[self.item.pk]), {'new_variant_id': self.alternative.pk})
        self.item.refresh_from_db()
        self.assertEqual(self.item.variant_id, self.alternative.pk)
        self.assertEqual(self.item.price_at_addition, self.alternative.price)

    def test_bulk_delete_is_atomic_and_rejects_foreign_ids(self):
        response = self.client.post(reverse('cart:cart_remove_selected'), {'item_ids': [self.item.pk, self.foreign_item.pk]})
        self.assertEqual(response.status_code, 404)
        self.assertTrue(CartItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=self.foreign_item.pk).exists())

    def test_bulk_delete_removes_only_selected_owned_rows(self):
        keep = CartItem.objects.create(cart=self.cart, variant=self.alternative, quantity=1)
        response = self.client.post(reverse('cart:cart_remove_selected'), {'item_ids': [self.item.pk]})
        self.assertRedirects(response, reverse('cart:cart_detail'))
        self.assertFalse(CartItem.objects.filter(pk=self.item.pk).exists())
        self.assertTrue(CartItem.objects.filter(pk=keep.pk).exists())

    def test_empty_or_invalid_bulk_selection_does_not_delete(self):
        for ids in ([], ['invalid'], ['0']):
            self.client.post(reverse('cart:cart_remove_selected'), {'item_ids': ids})
            self.assertTrue(CartItem.objects.filter(pk=self.item.pk).exists())

    def test_csrf_protection_remains_enabled(self):
        protected = Client(enforce_csrf_checks=True)
        protected.force_login(self.customer)
        self.assertEqual(protected.post(reverse('cart:cart_remove_selected'), {'item_ids': [self.item.pk]}).status_code, 403)

    def test_removed_coupon_preview_endpoint_is_not_available(self):
        self.assertEqual(self.client.post('/cart/coupon/preview/', {'code': 'SAMPLE'}).status_code, 404)

    def test_unavailable_stock_blocks_checkout_and_is_visible(self):
        Inventory.objects.filter(variant=self.variant).update(physical_sellable=2)
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Hết hàng')
        self.assertFalse(response.context['evaluation']['can_checkout'])
        self.assertEqual(response.context['evaluation']['available_quantity'], 0)
        self.assertContains(response, '0 sản phẩm khả dụng')
        self.assertNotContains(response, 'class="cart-checkout" href=')

    def test_price_change_is_visible_and_totals_match_checkout(self):
        ProductVariant.objects.filter(pk=self.variant.pk).update(price=Decimal('1100000'))
        response = self.client.get(reverse('cart:cart_detail'))
        self.assertContains(response, 'Giá đã thay đổi')
        self.assertEqual(response.context['evaluation']['subtotal_available'], calculate_server_quote(self.cart).subtotal)

    def test_guest_can_add_and_modify_own_cart(self):
        guest = Client()
        guest.post(reverse('cart:cart_add'), {'variant_id': self.variant.pk, 'quantity': '1'})
        guest_cart = Cart.objects.get(session_key=guest.session.session_key, user__isnull=True)
        item = guest_cart.items.get()
        guest.post(reverse('cart:cart_update_quantity', args=[item.pk]), {'quantity': '2'})
        item.refresh_from_db()
        self.assertEqual(item.quantity, 2)
        self.assertEqual(guest.post(reverse('cart:cart_remove_selected'), {'item_ids': [self.item.pk]}).status_code, 404)

    def test_pending_guest_cart_can_still_be_synced(self):
        guest_cart = Cart.objects.create(session_key='pending-guest-cart')
        CartItem.objects.create(cart=guest_cart, variant=self.alternative, quantity=1)
        session = self.client.session
        session['_pending_guest_cart_session_key'] = guest_cart.session_key
        session.save()
        self.assertContains(self.client.get(reverse('cart:cart_detail')), 'Đồng bộ giỏ hàng')
        self.client.post(reverse('cart:sync_guest_cart'))
        self.assertFalse(Cart.objects.filter(pk=guest_cart.pk).exists())
        self.assertTrue(self.cart.items.filter(variant=self.alternative).exists())
