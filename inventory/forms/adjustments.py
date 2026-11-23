"""Forms for stocktake adjustments."""

from django import forms
from django.core.exceptions import ValidationError

from .common import MAX_INTEGER_LIMIT, StrictNonNegativeIntegerField


class StocktakeAdjustmentForm(forms.Form):
    counted_quantity = StrictNonNegativeIntegerField(
        label='Số lượng kiểm kê thực tế',
        min_value=0,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '0',
            'max': str(MAX_INTEGER_LIMIT),
            'step': '1',
            'placeholder': 'Nhập số lượng hàng đếm được thực tế trong kho...',
            'autocomplete': 'off',
            'id': 'id_counted_quantity',
        }),
        error_messages={
            'required': 'Vui lòng nhập số lượng kiểm kê thực tế.',
            'invalid': 'Số lượng kiểm kê phải là một số nguyên hợp lệ.',
            'min_value': 'Số lượng kiểm kê không được là số âm (phải >= 0).',
            'max_value': f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )
    reason = forms.CharField(
        label='Lý do điều chỉnh',
        required=True,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-control',
            'rows': '3',
            'placeholder': 'Ghi rõ nguyên nhân chênh lệch tồn kho (ví dụ: phát hiện hư hỏng, kiểm đếm thực tế định kỳ, thất thoát sau kiểm kho)...',
            'id': 'id_reason',
        }),
        error_messages={
            'required': 'Vui lòng nhập lý do điều chỉnh tồn kho.',
        },
    )
    request_token = forms.CharField(
        widget=forms.HiddenInput(attrs={'id': 'id_request_token'}),
        required=True,
        error_messages={
            'required': 'Mã xác thực phiên làm việc bị thiếu. Vui lòng tải lại trang.',
        },
    )

    def clean_counted_quantity(self):
        val = self.cleaned_data.get('counted_quantity')
        if val is None or isinstance(val, bool) or not isinstance(val, int):
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho số lượng kiểm kê.')
        if val < 0:
            raise ValidationError('Số lượng kiểm kê không được là số âm (phải >= 0).')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Số lượng kiểm kê không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val

    def clean_reason(self):
        val = self.cleaned_data.get('reason')
        if not val or not val.strip():
            raise ValidationError('Lý do điều chỉnh không được để trống hoặc chỉ chứa khoảng trắng.')
        return val.strip()
