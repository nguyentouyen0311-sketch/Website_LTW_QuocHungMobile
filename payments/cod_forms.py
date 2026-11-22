from decimal import Decimal

from django import forms


class CODCollectionForm(forms.Form):
    amount = forms.DecimalField(
        label='Số tiền thực thu (đ)', min_value=Decimal('0.01'), max_digits=14, decimal_places=2
    )
    reference = forms.CharField(label='Mã chứng từ thu tiền', max_length=100)
    note = forms.CharField(
        label='Ghi chú', required=False, max_length=500, widget=forms.Textarea(attrs={'rows': 2})
    )
    confirm = forms.BooleanField(label='Tôi xác nhận đã thu đủ số tiền trên từ khách hàng.')
