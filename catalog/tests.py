from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse

from catalog.forms import ProductVariantForm
from catalog.models import Category, Product


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
