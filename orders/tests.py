from types import SimpleNamespace

from django.test import SimpleTestCase

from orders.forms import CheckoutForm
from orders.views import _get_initial_checkout_data, _refresh_quote_token


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
