"""Forms used by the return-stock domain model."""

from django import forms
from django.core.exceptions import ValidationError

from inventory.models import ReturnedStock, ReturnedStockCondition


class ReturnedStockInspectionForm(forms.ModelForm):
    condition = forms.ChoiceField(
        choices=ReturnedStockCondition.choices,
        label='Phân loại tình trạng hàng',
        widget=forms.Select(attrs={'class': 'qhm-form-control', 'id': 'id_condition'}),
    )
    inspection_notes = forms.CharField(
        label='Ghi chú kiểm định',
        required=False,
        widget=forms.Textarea(attrs={
            'class': 'qhm-form-control',
            'rows': '3',
            'placeholder': 'Ghi chú chi tiết về tình trạng ngoại quan, tính năng, phụ kiện, lý do từ chối hoặc chấp thuận bán lại...',
            'id': 'id_inspection_notes',
        }),
    )

    class Meta:
        model = ReturnedStock
        fields = ['condition', 'inspection_notes']

    def clean_condition(self):
        condition = self.cleaned_data.get('condition')
        if condition not in ReturnedStockCondition.values:
            raise ValidationError('Phân loại tình trạng không hợp lệ.')
        return condition

    def clean_inspection_notes(self):
        notes = self.cleaned_data.get('inspection_notes', '')
        return notes.strip() if notes else ''
