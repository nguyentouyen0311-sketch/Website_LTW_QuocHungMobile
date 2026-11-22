from django import forms
from django.core.exceptions import ValidationError
from cart.services import parse_strictly_positive_int


class AddToCartForm(forms.Form):
    variant_id = forms.IntegerField(min_value=1, required=True)
    quantity = forms.CharField(required=True)

    def clean_quantity(self):
        raw_qty = self.cleaned_data.get('quantity')
        try:
            return parse_strictly_positive_int(raw_qty)
        except ValidationError as e:
            raise ValidationError(e.message, code='invalid_quantity')


class UpdateCartItemQuantityForm(forms.Form):
    quantity = forms.CharField(required=True)

    def clean_quantity(self):
        raw_qty = self.cleaned_data.get('quantity')
        try:
            return parse_strictly_positive_int(raw_qty)
        except ValidationError as e:
            raise ValidationError(e.message, code='invalid_quantity')


class SwitchCartItemVariantForm(forms.Form):
    new_variant_id = forms.IntegerField(min_value=1, required=True)
