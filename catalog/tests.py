from django.test import TestCase

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
