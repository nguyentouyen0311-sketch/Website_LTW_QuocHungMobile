"""Forms for stock-alert thresholds."""

from django import forms
from django.core.exceptions import ValidationError

from inventory.models import Inventory

from .common import MAX_INTEGER_LIMIT


class InventoryThresholdForm(forms.ModelForm):
    min_threshold = forms.IntegerField(
        label='Ngưỡng cảnh báo tối thiểu',
        min_value=0,
        max_value=MAX_INTEGER_LIMIT,
        required=True,
        widget=forms.NumberInput(attrs={
            'class': 'qhm-form-control',
            'min': '0',
            'step': '1',
            'placeholder': 'Nhập số nguyên >= 0',
            'autocomplete': 'off',
            'id': 'id_min_threshold',
        }),
        error_messages={
            'required': 'Vui lòng nhập ngưỡng cảnh báo tồn kho tối thiểu.',
            'invalid': 'Ngưỡng cảnh báo phải là một số nguyên hợp lệ.',
            'min_value': 'Ngưỡng cảnh báo không được là số âm (phải >= 0).',
            'max_value': f'Ngưỡng cảnh báo không được vượt quá {MAX_INTEGER_LIMIT:,}.',
        },
    )

    class Meta:
        model = Inventory
        fields = ['min_threshold']

    def clean_min_threshold(self):
        val = self.cleaned_data.get('min_threshold')
        if val is None:
            raise ValidationError('Vui lòng nhập số nguyên hợp lệ cho ngưỡng cảnh báo.')
        if val < 0:
            raise ValidationError('Ngưỡng cảnh báo không được là số âm (phải >= 0).')
        if val > MAX_INTEGER_LIMIT:
            raise ValidationError(f'Ngưỡng cảnh báo không được vượt quá {MAX_INTEGER_LIMIT:,}.')
        return val

    def save(self, commit=True):
        instance = super().save(commit=False)
        if commit:
            instance.save(update_fields=['min_threshold', 'updated_at'])
        return instance
