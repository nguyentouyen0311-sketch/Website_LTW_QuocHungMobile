import uuid

from django import forms
from django.core.exceptions import ValidationError

from orders.models import (
    CancellationReasonCode,
    ReturnReason,
    ReturnType,
    ReturnRequestItem,
    ReturnStatus,
    ShippingMethod,
    validate_delivery_phone,
)
from orders.uploads import validate_return_upload
from payments.models import PaymentMethod


class CheckoutForm(forms.Form):
    recipient_name = forms.CharField(
        label='Họ và tên người nhận',
        max_length=120,
        widget=forms.TextInput(attrs={'autocomplete': 'name'}),
    )
    recipient_phone = forms.CharField(
        label='Số điện thoại liên hệ',
        max_length=20,
        widget=forms.TextInput(attrs={'autocomplete': 'tel', 'inputmode': 'tel'}),
    )
    province = forms.CharField(
        label='Tỉnh / Thành phố',
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={
                'list': 'province-options',
                'autocomplete': 'address-level1',
                'placeholder': 'Tỉnh / Thành phố',
            }
        ),
    )
    district = forms.CharField(
        label='Quận / Huyện',
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={'autocomplete': 'address-level2', 'placeholder': 'Quận / Huyện'}
        ),
    )
    ward = forms.CharField(
        label='Phường / Xã',
        max_length=100,
        required=False,
        widget=forms.TextInput(
            attrs={'autocomplete': 'address-level3', 'placeholder': 'Phường / Xã'}
        ),
    )
    shipping_address = forms.CharField(
        label='Địa chỉ cụ thể (Số nhà, tên đường)',
        max_length=1000,
        required=False,
        widget=forms.TextInput(
            attrs={'autocomplete': 'street-address', 'placeholder': 'Số nhà, tên đường'}
        ),
    )
    shipping_note = forms.CharField(
        label='Ghi chú giao hàng (Tùy chọn)',
        required=False,
        max_length=500,
        widget=forms.TextInput(
            attrs={'placeholder': 'Ví dụ: Giao giờ hành chính, gọi trước khi giao...'}
        ),
    )
    shipping_method = forms.ChoiceField(
        label='Phương thức vận chuyển',
        choices=ShippingMethod.choices,
        initial=ShippingMethod.DELIVERY,
        widget=forms.RadioSelect,
    )
    payment_method = forms.ChoiceField(
        choices=PaymentMethod.choices, initial=PaymentMethod.COD, widget=forms.HiddenInput
    )
    coupon_code = forms.CharField(
        label='Mã giảm giá (Voucher)',
        required=False,
        max_length=40,
        widget=forms.TextInput(attrs={'placeholder': 'Nhập mã giảm giá', 'autocomplete': 'off'}),
    )
    checkout_token = forms.CharField(widget=forms.HiddenInput)
    quote_token = forms.CharField(widget=forms.HiddenInput)

    def clean_recipient_phone(self):
        return validate_delivery_phone(self.cleaned_data['recipient_phone'])

    def clean_coupon_code(self):
        return self.cleaned_data['coupon_code'].strip().upper()

    def clean(self):
        data = super().clean()
        if data.get('shipping_method') == ShippingMethod.DELIVERY:
            for field in ('province', 'district', 'ward', 'shipping_address'):
                if not data.get(field):
                    self.add_error(field, 'Vui lòng nhập thông tin địa chỉ nhận hàng.')
        return data


class OrderConfirmationForm(forms.Form):
    confirmation_note = forms.CharField(
        label='Ghi chú xác nhận',
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class OrderShipForm(forms.Form):
    shipping_note = forms.CharField(
        label='Ghi chú giao nhận',
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 2}),
    )
    carrier_name = forms.CharField(label='Đơn vị vận chuyển', max_length=120)
    tracking_number = forms.CharField(label='Mã vận đơn', max_length=100)
    carrier_contact = forms.CharField(label='Điện thoại giao nhận', max_length=20, required=False)
    estimated_delivery_at = forms.DateTimeField(
        label='Dự kiến giao hàng',
        required=False,
        widget=forms.DateTimeInput(attrs={'type': 'datetime-local'}),
    )

    def __init__(self, *args, pickup=False, **kwargs):
        super().__init__(*args, **kwargs)
        if pickup:
            self.fields['carrier_name'].required = False
            self.fields['tracking_number'].required = False
            self.fields['carrier_name'].label = 'Điểm nhận hàng (tùy chọn)'
            self.fields['tracking_number'].label = 'Mã bàn giao tại cửa hàng (tùy chọn)'


class OrderDeliverForm(forms.Form):
    delivery_note = forms.CharField(
        label='Ghi chú giao thành công',
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 2}),
    )


class OrderCancelForm(forms.Form):
    reason_code = forms.ChoiceField(label='Lý do hủy', choices=CancellationReasonCode.choices)
    description = forms.CharField(
        label='Mô tả chi tiết',
        required=False,
        max_length=500,
        widget=forms.Textarea(attrs={'rows': 3}),
    )

    def clean(self):
        data = super().clean()
        if (
            data.get('reason_code') == CancellationReasonCode.OTHER
            and not data.get('description', '').strip()
        ):
            self.add_error('description', 'Vui lòng mô tả lý do khác.')
        return data


class MultipleFileInput(forms.ClearableFileInput):
    allow_multiple_selected = True


class MultipleFileField(forms.FileField):
    def clean(self, data, initial=None):
        files = data if isinstance(data, (list, tuple)) else ([data] if data else [])
        if len(files) > 5:
            raise ValidationError('Mỗi yêu cầu chỉ được gửi tối đa 5 tệp.')
        cleaned = []
        for upload in files:
            upload = super().clean(upload, initial)
            upload.verified_content_type = validate_return_upload(upload)
            cleaned.append(upload)
        return cleaned


class ReturnRequestForm(forms.Form):
    request_type = forms.ChoiceField(
        label='Chọn hình thức yêu cầu', choices=ReturnType.choices, widget=forms.RadioSelect
    )
    items = forms.MultipleChoiceField(
        label='Chọn sản phẩm trong đơn', widget=forms.CheckboxSelectMultiple
    )
    reason = forms.ChoiceField(label='Lý do đổi trả / hủy đơn', choices=ReturnReason.choices)
    description = forms.CharField(
        label='Mô tả chi tiết tình trạng sản phẩm',
        min_length=10,
        max_length=3000,
        widget=forms.Textarea(
            attrs={'rows': 4, 'placeholder': 'Mô tả tình trạng sản phẩm hoặc lý do yêu cầu...'}
        ),
    )
    attachments = MultipleFileField(
        label='Tải lên hình ảnh hoặc video thực tế (tối đa 5 tệp)',
        required=False,
        widget=MultipleFileInput(
            attrs={'accept': 'image/jpeg,image/png,image/webp,video/mp4,video/webm'}
        ),
    )
    request_key = forms.UUIDField(widget=forms.HiddenInput)

    def __init__(self, *args, order, **kwargs):
        self.order = order
        super().__init__(*args, **kwargs)
        completed_ids = set(
            ReturnRequestItem.objects.filter(
                order_item__order=order, request__status=ReturnStatus.COMPLETED
            )
            .exclude(request__request_type=ReturnType.CANCEL)
            .values_list('order_item_id', flat=True)
        )
        self.eligible_items = [item for item in order.items.all() if item.pk not in completed_ids]
        self.fields['items'].choices = [
            (str(item.pk), item.product_name_snapshot) for item in self.eligible_items
        ]
        self.initial.setdefault('request_key', uuid.uuid4())
        if order.order_status in ('PENDING_CONFIRMATION', 'CONFIRMED'):
            self.fields['request_type'].choices = [(ReturnType.CANCEL, ReturnType.CANCEL.label)]
            self.initial.setdefault('request_type', ReturnType.CANCEL)
        else:
            self.fields['request_type'].choices = [
                (ReturnType.EXCHANGE, ReturnType.EXCHANGE.label),
                (ReturnType.REFUND, ReturnType.REFUND.label),
            ]
            self.initial.setdefault('request_type', ReturnType.EXCHANGE)
        self.initial.setdefault('items', [str(item.pk) for item in self.eligible_items])
        self.initial.setdefault(
            'reason',
            (
                ReturnReason.NO_LONGER_NEEDED
                if order.order_status in ('PENDING_CONFIRMATION', 'CONFIRMED')
                else ReturnReason.HARDWARE
            ),
        )


class ReturnReviewForm(forms.Form):
    decision = forms.ChoiceField(
        label='Xử lý yêu cầu',
        choices=[
            ('APPROVED', 'Tiếp nhận yêu cầu'),
            ('REJECTED', 'Từ chối yêu cầu'),
            ('COMPLETED', 'Xác nhận đã hoàn tiền thực tế'),
        ],
    )
    note = forms.CharField(
        label='Ghi chú xử lý',
        min_length=5,
        max_length=3000,
        widget=forms.Textarea(attrs={'rows': 3}),
    )
    refund_reference = forms.CharField(
        label='Mã chứng từ hoàn tiền thực tế', required=False, max_length=100
    )
    received_return = forms.BooleanField(
        label='Đã nhận và kiểm tra đầy đủ các sản phẩm trả lại', required=False
    )

    def __init__(self, *args, allow_complete=False, **kwargs):
        super().__init__(*args, **kwargs)
        if not allow_complete:
            self.fields['decision'].choices = [
                ('APPROVED', 'Tiếp nhận yêu cầu'),
                ('REJECTED', 'Từ chối yêu cầu'),
            ]
            self.fields.pop('refund_reference')
            self.fields.pop('received_return')
