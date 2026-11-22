from datetime import timedelta

from django.test import TestCase
from django.utils import timezone

from coupons.forms import CouponForm
from coupons.models import Coupon


class CouponPublicationSchemaTests(TestCase):
    def payload(self, code='PUBLICATION_TEST'):
        now = timezone.now()
        return {
            'code': code,
            'discount_type': 'FIXED',
            'value': '100000',
            'min_order_amount': '0',
            'max_uses': '10',
            'starts_at': (now - timedelta(days=1)).isoformat(),
            'ends_at': (now + timedelta(days=1)).isoformat(),
            'is_active': 'on',
            'applies_to_all': 'True',
        }

    def test_create_without_publication_field_saves_non_public_coupon(self):
        form = CouponForm(data=self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        coupon = form.save()
        coupon.refresh_from_db()
        self.assertFalse(coupon.is_public)
        self.assertTrue(coupon.is_active)

    def test_edit_without_publication_field_preserves_existing_publication(self):
        form = CouponForm(data=self.payload())
        self.assertTrue(form.is_valid(), form.errors)
        coupon = form.save()
        Coupon.objects.filter(pk=coupon.pk).update(is_public=True)
        coupon.refresh_from_db()

        data = self.payload()
        data['value'] = '150000'
        edit_form = CouponForm(data=data, instance=coupon)
        self.assertTrue(edit_form.is_valid(), edit_form.errors)
        updated = edit_form.save()
        updated.refresh_from_db()
        self.assertTrue(updated.is_public)
        self.assertEqual(updated.value, 150000)
