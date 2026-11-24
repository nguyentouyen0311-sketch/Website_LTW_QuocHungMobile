from decimal import Decimal
from urllib.parse import parse_qs, urlsplit

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.forms import ProductVariantForm
from catalog.models import (
    Attribute, AttributeValue, Category, Product, ProductPresentation,
    ProductVariant, VariantAttributeValue,
)


class ProductVariantFormTests(TestCase):
    def test_create_variant_generates_sku_before_model_validation(self):
        category = Category.objects.create(name='Điện thoại')
        product = Product.objects.create(category=category, name='iPhone 17')
        form = ProductVariantForm(
            product=product,
            data={
                'price': '1000000',
                'original_price': '',
                'is_active': 'on',
            },
        )

        self.assertTrue(form.is_valid(), form.errors)
        self.assertEqual(form.cleaned_data['automatic_sku'], f'{product.product_code}-MAC-DINH')

        variant = form.save()
        self.assertEqual(variant.sku, f'{product.product_code}-MAC-DINH')


class ProductImageManagementTests(TestCase):
    def setUp(self):
        self.user = get_user_model().objects.create_superuser(
            username='catalog-admin', email='catalog-admin@example.test', password='safe-password-123'
        )
        self.category = Category.objects.create(name='Laptop')
        self.product = Product.objects.create(category=self.category, name='Laptop kiểm thử')
        self.client.force_login(self.user)

    def test_product_editor_includes_image_management_section(self):
        response = self.client.get(reverse('catalog:product_edit', args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ảnh sản phẩm')
        self.assertContains(response, reverse('catalog:product_image_upload', args=[self.product.id]))

    def test_legacy_product_detail_redirects_to_editor_image_section(self):
        response = self.client.get(reverse('catalog:product_detail', args=[self.product.id]))

        self.assertRedirects(
            response,
            f"{reverse('catalog:product_edit', args=[self.product.id])}#product-images",
            fetch_redirect_response=False,
        )


class AttributeDeletionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='attribute-admin', email='attribute-admin@example.test', password='safe-password-123'
        )
        self.client.force_login(self.admin)

    def test_admin_can_delete_unused_attribute_with_its_values(self):
        attribute = Attribute.objects.create(name='Màu sắc')
        value = AttributeValue.objects.create(attribute=attribute, value='Đỏ')

        response = self.client.post(reverse('catalog:attribute_delete', args=[attribute.id]))

        self.assertRedirects(response, reverse('catalog:attribute_list'))
        self.assertFalse(Attribute.objects.filter(pk=attribute.id).exists())
        self.assertFalse(AttributeValue.objects.filter(pk=value.id).exists())

    def test_admin_can_delete_unused_attribute_value(self):
        attribute = Attribute.objects.create(name='Dung lượng')
        value = AttributeValue.objects.create(attribute=attribute, value='256GB')

        response = self.client.post(reverse('catalog:attribute_value_delete', args=[attribute.id, value.id]))

        self.assertRedirects(response, reverse('catalog:attribute_list'))
        self.assertTrue(Attribute.objects.filter(pk=attribute.id).exists())
        self.assertFalse(AttributeValue.objects.filter(pk=value.id).exists())

    def test_used_attribute_value_is_protected_from_deletion(self):
        category = Category.objects.create(name='Máy tính bảng')
        product = Product.objects.create(category=category, name='Tablet kiểm thử')
        attribute = Attribute.objects.create(name='Màu')
        value = AttributeValue.objects.create(attribute=attribute, value='Xanh')
        variant = ProductVariant.objects.create(product=product, sku='TABLET-TEST-01', price='1000000', combination_key='1')
        VariantAttributeValue.objects.create(variant=variant, attribute=attribute, attribute_value=value)

        response = self.client.post(reverse('catalog:attribute_value_delete', args=[attribute.id, value.id]))

        self.assertRedirects(response, reverse('catalog:attribute_list'))
        self.assertTrue(AttributeValue.objects.filter(pk=value.id).exists())


class ProductVariantDeletionTests(TestCase):
    def setUp(self):
        self.admin = get_user_model().objects.create_superuser(
            username='variant-admin', email='variant-admin@example.test', password='safe-password-123'
        )
        self.client.force_login(self.admin)
        category = Category.objects.create(name='Phụ kiện')
        self.product = Product.objects.create(category=category, name='Sản phẩm kiểm thử biến thể')

    def test_variant_create_page_loads_for_active_product(self):
        response = self.client.get(reverse('catalog:product_variant_create', args=[self.product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Thêm biến thể mới')

    def test_admin_can_soft_delete_unused_zero_stock_variant(self):
        variant = ProductVariant.objects.create(
            product=self.product, sku='DELETE-ME-01', price='1000000', combination_key='delete-me'
        )

        response = self.client.post(
            reverse('catalog:product_variant_delete', args=[self.product.id, variant.id])
        )

        self.assertRedirects(response, reverse('catalog:attribute_list'))
        variant.refresh_from_db()
        self.assertTrue(variant.is_deleted)
        self.assertFalse(variant.is_active)

    def test_variant_with_stock_is_soft_deleted_and_inventory_is_preserved(self):
        variant = ProductVariant.objects.create(
            product=self.product, sku='KEEP-ME-01', price='1000000', combination_key='keep-me'
        )
        variant.inventory.physical_sellable = 1
        variant.inventory.save()

        response = self.client.post(
            reverse('catalog:product_variant_delete', args=[self.product.id, variant.id])
        )

        self.assertRedirects(response, reverse('catalog:attribute_list'))
        variant.refresh_from_db()
        self.assertTrue(variant.is_deleted)
        self.assertFalse(variant.is_active)
        self.assertEqual(variant.inventory.physical_sellable, 1)


class StorefrontSearchTests(TestCase):
    def setUp(self):
        category = Category.objects.create(name='Laptop')
        self.product = Product.objects.create(
            category=category,
            name='Laptop Tìm Kiếm',
            brand='Quốc Hùng',
        )

    def test_searches_storefront_by_name_and_product_code(self):
        by_name = self.client.get(reverse('catalog:public_catalog'), {'q': 'tìm kiếm'})
        by_code = self.client.get(reverse('catalog:public_catalog'), {'q': self.product.product_code})

        self.assertContains(by_name, self.product.name)
        self.assertContains(by_code, self.product.name)


class ProductDetailContentTests(TestCase):
    def test_product_detail_renders_short_and_full_descriptions(self):
        category = Category.objects.create(name='Điện thoại')
        product = Product.objects.create(
            category=category,
            name='Sản phẩm đủ nội dung',
            description='Đây là mô tả chi tiết của sản phẩm.',
        )
        ProductPresentation.objects.create(
            product=product,
            short_description='Đây là mô tả nổi bật của sản phẩm.',
        )

        response = self.client.get(reverse('catalog:public_product_detail', args=[product.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Mô tả nổi bật:')
        self.assertContains(response, 'Đây là mô tả nổi bật của sản phẩm.')
        self.assertContains(response, 'Mô tả chi tiết:')
        self.assertContains(response, 'Đây là mô tả chi tiết của sản phẩm.')


class CatalogPriceFilterTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.phones = Category.objects.create(name='Điện thoại', is_visible=True)
        cls.laptops = Category.objects.create(name='Laptop', is_visible=True)
        cls.hidden = Category.objects.create(name='Danh mục ẩn', is_visible=False)
        cls.boundaries = {}
        for price in (
            '0', '9999999.99', '10000000', '14999999.99', '15000000',
            '19999999.99', '20000000', '24999999.99', '25000000',
            '29999999.99', '30000000', '30000000.01',
        ):
            product = cls.make_product(f'Giá {price}', cls.phones, [price])
            cls.boundaries[price] = product.pk
        cls.laptop = cls.make_product('Laptop mẫu', cls.laptops, ['12000000'])
        cls.hidden_product = cls.make_product('Sản phẩm danh mục ẩn', cls.hidden, ['12000000'])
        cls.inactive_product = cls.make_product('Ngừng bán', cls.phones, ['12000000'], is_active=False)
        cls.deleted_product = cls.make_product('Đã xóa', cls.phones, ['12000000'])
        Product.objects.filter(pk=cls.deleted_product.pk).update(is_deleted=True)
        cls.contact_product = cls.make_product('Giá liên hệ', cls.phones, [])
        cls.inactive_variant_product = cls.make_product('Phiên bản ngừng bán', cls.phones, ['12000000'])
        cls.inactive_variant_product.variants.update(is_active=False)

    @classmethod
    def make_product(cls, name, category, prices, **kwargs):
        product = Product.objects.create(name=name, category=category, **kwargs)
        for index, price in enumerate(prices):
            ProductVariant.objects.create(
                product=product, sku=f'FILTER-{product.pk}-{index}',
                combination_key=str(index), price=Decimal(price), is_active=True,
            )
        return product

    def product_ids(self, response):
        self.assertEqual(response.status_code, 200)
        return {product.pk for product in response.context['products']}

    def test_each_price_boundary_is_assigned_to_exactly_one_segment(self):
        expected = {
            'under-10': ['0', '9999999.99'],
            '10-15': ['10000000', '14999999.99'],
            '15-20': ['15000000', '19999999.99'],
            '20-25': ['20000000', '24999999.99'],
            '25-30': ['25000000', '29999999.99', '30000000'],
            'over-30': ['30000000.01'],
        }
        url = reverse('catalog:public_category', args=[self.phones.pk])
        for segment, prices in expected.items():
            with self.subTest(segment=segment):
                response = self.client.get(url, {'price': segment})
                self.assertEqual(self.product_ids(response), {self.boundaries[price] for price in prices})
                selected = [item for item in response.context['price_segments'] if item['selected']]
                self.assertEqual(len(selected), 1)

    def test_unfiltered_catalog_keeps_expensive_and_contact_products(self):
        response = self.client.get(reverse('catalog:public_catalog'))
        ids = self.product_ids(response)
        self.assertIn(self.boundaries['30000000.01'], ids)
        self.assertIn(self.contact_product.pk, ids)
        self.assertIn(self.inactive_variant_product.pk, ids)
        self.assertIn(self.laptop.pk, ids)
        self.assertNotIn(self.hidden_product.pk, ids)
        self.assertNotIn(self.inactive_product.pk, ids)
        self.assertNotIn(self.deleted_product.pk, ids)
        self.assertFalse(any(item['selected'] for item in response.context['price_segments']))
        self.assertContains(response, 'Phân khúc giá')
        self.assertEqual(len(response.context['price_segments']), 6)

    def test_over_30_has_no_upper_limit_on_all_catalog_pages(self):
        expensive = self.make_product('Giá cao hơn 30 triệu', self.phones, ['99999999999'])
        for url in (
            reverse('catalog:public_catalog'),
            reverse('catalog:public_category', args=[self.phones.pk]),
            reverse('catalog:storefront_category', args=['phones']),
        ):
            with self.subTest(url=url):
                response = self.client.get(url, {'price': 'over-30'})
                self.assertEqual(self.product_ids(response), {self.boundaries['30000000.01'], expensive.pk})
                self.assertContains(response, 'Trên 30 triệu')
                self.assertEqual(response.context['category_price_query'], 'price=over-30')
                selected = next(item for item in response.context['price_segments'] if item['selected'])
                reset = self.client.get(selected['url'])
                self.assertIn(self.boundaries['30000000'], self.product_ids(reset))

    def test_catalog_category_and_slug_pages_share_filter_behavior(self):
        urls = [
            (reverse('catalog:public_catalog'), {self.laptop.pk}),
            (reverse('catalog:public_category', args=[self.phones.pk]), set()),
            (reverse('catalog:storefront_category', args=['phones']), set()),
        ]
        expected_phones = {self.boundaries['10000000'], self.boundaries['14999999.99']}
        for url, extra_ids in urls:
            with self.subTest(url=url):
                response = self.client.get(url, {'price': '10-15'})
                self.assertEqual(self.product_ids(response), expected_phones | extra_ids)
                self.assertTemplateUsed(response, 'storefront/category_live.html')
                self.assertContains(response, f'{2 + len(extra_ids)} sản phẩm')

    def test_filter_uses_the_same_active_variant_as_the_displayed_card(self):
        first_expensive = self.make_product('Bản đầu giá cao', self.phones, ['20000000', '12000000'])
        first_in_range = self.make_product('Bản đầu trong khoảng', self.phones, ['12000000', '20000000'])
        inactive_first = self.make_product('Bản cũ ngừng bán', self.phones, ['25000000', '12000000'])
        old_variant = inactive_first.variants.order_by('id').first()
        old_variant.is_active = False
        old_variant.save(update_fields=['is_active'])
        original_price_product = self.make_product('Giá gốc khác giá bán', self.phones, ['12000000'])
        original_price_product.variants.update(original_price=Decimal('25000000'))
        for url in (reverse('catalog:public_catalog'), reverse('catalog:storefront_category', args=['phones'])):
            with self.subTest(url=url):
                response = self.client.get(url, {'price': '10-15'})
                ids = self.product_ids(response)
                self.assertNotIn(first_expensive.pk, ids)
                for product in (first_in_range, inactive_first, original_price_product):
                    self.assertIn(product.pk, ids)
                    card = next(item for item in response.context['products'] if item.pk == product.pk)
                    self.assertEqual(card.price_display.replace(' ', ''), '12.000.000đ')

    def test_selected_button_toggles_off_and_preserves_other_query_parameters(self):
        url = reverse('catalog:public_category', args=[self.phones.pk])
        response = self.client.get(url, {'price': '10-15', 'page': '4', 'q': 'Điện thoại'})
        active = next(item for item in response.context['price_segments'] if item['selected'])
        self.assertEqual(parse_qs(urlsplit(active['url']).query), {'q': ['Điện thoại']})
        self.assertEqual(response.context['clear_price_filter_url'], active['url'])
        unfiltered = self.client.get(active['url'])
        self.assertIn(self.boundaries['30000000.01'], self.product_ids(unfiltered))
        self.assertFalse(any(item['selected'] for item in unfiltered.context['price_segments']))
        next_segment = response.context['price_segments'][2]
        self.assertEqual(parse_qs(urlsplit(next_segment['url']).query), {'q': ['Điện thoại'], 'price': ['15-20']})

    def test_category_links_keep_selected_price_and_switch_category(self):
        response = self.client.get(reverse('catalog:public_catalog'), {'price': '10-15'})
        target = reverse('catalog:public_category', args=[self.laptops.pk]) + '?price=10-15'
        self.assertContains(response, f'href="{target}"')
        category_response = self.client.get(target)
        self.assertEqual(self.product_ids(category_response), {self.laptop.pk})
        self.assertContains(category_response, f'href="{reverse("catalog:public_catalog")}?price=10-15"')

    def test_empty_segment_has_a_reset_link(self):
        url = reverse('catalog:public_category', args=[self.laptops.pk])
        response = self.client.get(url, {'price': '25-30'})
        self.assertEqual(self.product_ids(response), set())
        self.assertContains(response, 'Không có sản phẩm trong phân khúc từ 25 - 30 triệu.')
        self.assertContains(response, f'href="{url}"')
        self.assertContains(response, '0 sản phẩm')

    def test_invalid_price_is_ignored_without_hiding_products_or_injecting_html(self):
        url = reverse('catalog:public_category', args=[self.phones.pk])
        for value in ('invalid', '-1', '<script>alert(1)</script>'):
            with self.subTest(value=value):
                response = self.client.get(url, {'price': value})
                self.assertIn(self.boundaries['30000000.01'], self.product_ids(response))
                self.assertIsNone(response.context['selected_price_segment'])
                self.assertEqual(response.context['category_price_query'], '')
                self.assertNotContains(response, '<script>alert(1)</script>')

    def test_hidden_and_unknown_categories_cannot_be_exposed_by_filter(self):
        response = self.client.get(reverse('catalog:public_category', args=[self.hidden.pk]), {'price': '10-15'})
        self.assertEqual(response.status_code, 404)
        empty_slug = self.client.get(reverse('catalog:storefront_category', args=['audio']), {'price': '10-15'})
        self.assertEqual(self.product_ids(empty_slug), set())

    def test_price_filter_combines_with_search_and_reset_keeps_search(self):
        url = reverse('catalog:public_catalog')
        response = self.client.get(url, {'q': 'Laptop', 'price': 'under-10'})
        self.assertEqual(self.product_ids(response), set())
        self.assertEqual(response.context['storefront_search_query'], 'Laptop')
        reset = self.client.get(response.context['clear_price_filter_url'])
        self.assertEqual(self.product_ids(reset), {self.laptop.pk})
        self.assertEqual(parse_qs(urlsplit(response.context['clear_price_filter_url']).query), {'q': ['Laptop']})

    def test_category_links_keep_both_search_and_price(self):
        response = self.client.get(reverse('catalog:public_catalog'), {'q': 'Laptop', 'price': '10-15'})
        query = response.context['category_filter_query']
        self.assertEqual(parse_qs(query), {'q': ['Laptop'], 'price': ['10-15']})
        target = reverse('catalog:public_category', args=[self.laptops.pk]) + '?' + query
        self.assertContains(response, target.replace('&', '&amp;'))
        self.assertEqual(self.product_ids(self.client.get(target)), {self.laptop.pk})
