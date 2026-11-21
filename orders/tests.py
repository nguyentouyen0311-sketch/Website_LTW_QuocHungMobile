from types import SimpleNamespace

from django.test import SimpleTestCase

from orders.forms import CheckoutForm
from orders.views import _refresh_quote_token


class CheckoutQuoteTokenTests(SimpleTestCase):
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
