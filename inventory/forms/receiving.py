"""Forms for purchase receiving."""

from django import forms
from django.core.exceptions import ValidationError

from .common import MAX_INTEGER_LIMIT


class PurchaseReceivingForm(forms.Form):
    quantity = forms.IntegerField(
        label='Số lượng nhập',
        min_value=1,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '1',
            'max': str(MAX_INTEGER_LIMIT),
            'step': '1',
            'placeholder': 'Nhập số lượng hàng thực tế cần thêm...',
            'autocomplete': 'off',
            'id': 'id_quantity',
        }),
        error_messages={
            'required': 'Vui lòng nhập số lượng hàng cần nhập kho.',
            'invalid': 'Số lượng nhập phải là một số nguyên hợp lệ.',
            'min_value': 'Số lượng nhập phải là số nguyên dương lớn hơn 0.',
            'max_value': f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )
    request_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_request_token'}),
        required=True,
        error_messages={
            'required': 'Mã xác thực phiên làm việc bị thiếu. Vui lòng tải lại trang.',
        },
    )

    def clean_quantity(self):
        val = self.cleaned_data.get('quantity')
        if val is None:
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho số lượng nhập.')
        if val <= 0:
            raise ValidationError('Số lượng nhập phải là số nguyên dương lớn hơn 0.')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Số lượng nhập không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val
