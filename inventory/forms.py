"""
Biểu mẫu nghiệp vụ Quản lý Kho hàng (Inventory Forms) — Quốc Hùng Mobile.
Tuân thủ giới hạn ghi theo Task 05.1: Chỉ cho phép người dùng điều chỉnh ngưỡng min_threshold.
Tuyệt đối không cho phép chỉnh sửa tồn thực tế (physical_sellable) hay tồn đã giữ (reserved).
"""
import re
from django import forms
from django.core.exceptions import ValidationError
from inventory.models import Inventory, ReturnedStock, ReturnedStockCondition

# Giới hạn giá trị số nguyên 32-bit có dấu tiêu chuẩn của trường IntegerField
MAX_INTEGER_LIMIT = 2147483647


class StrictNonNegativeIntegerField(forms.IntegerField):
    """
    Trường nhập số nguyên không âm nghiêm ngặt (QA-T053-01 / QA-T053-04):
    - Kiểm tra trực tiếp chuỗi gốc trước khi chuyển đổi kiểu để tránh ép kiểu ngầm làm mất dấu thập phân (vd: '6.0' -> 6).
    - Đồng bộ hoàn toàn với chính sách regex ASCII thuần của client-side JS preview: /^(0|[1-9]\d*)$/ (chỉ nhận chữ số ASCII [0-9]).
    - Chấp nhận: '0' và các số nguyên dương ASCII không chứa số 0 ở đầu ('1'..'2147483647').
    - Từ chối dứt điểm: chữ số Unicode phi-ASCII (vd: '1\u0662', '1\uff12'), chuỗi thập phân ('6.0', '2.5'),
      ký hiệu khoa học ('6e0', '1e2'), số âm ('-5'), số 0 thừa đầu ('05'), chuỗi chữ hoặc rỗng.
    - Giữ vững hợp đồng service: từ chối kiểu bool, float hoặc non-integer.
    """
    def to_python(self, value):
        if value in self.empty_values:
            return None
        if isinstance(value, str):
            val_str = value.strip()
            if not (val_str.isascii() and re.fullmatch(r'(?:0|[1-9][0-9]*)', val_str)):
                raise ValidationError(
                    self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                    code='invalid',
                )
        elif isinstance(value, bool) or not isinstance(value, int):
            raise ValidationError(
                self.error_messages.get('invalid', 'Số lượng kiểm kê phải là một số nguyên hợp lệ.'),
                code='invalid',
            )
        return super().to_python(value)



class InventoryThresholdForm(forms.ModelForm):
    """
    Biểu mẫu cập nhật ngưỡng cảnh báo tồn kho (min_threshold).
    - Whitelist chặt chẽ: Chỉ chứa trường min_threshold.
    - Chống mass assignment: Bất kỳ tham số nào khác (physical_sellable, reserved, variant)
      gửi lên trong POST đều bị loại bỏ hoàn toàn.
    - Thẩm định số nguyên: Từ chối số âm, số thực/thập phân và số vượt quá giới hạn integer.
    """
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


class PurchaseReceivingForm(forms.Form):
    """
    Biểu mẫu Nhập hàng vào kho (UI-31) — Task 05.2.
    - Whitelist nghiêm ngặt: Chỉ nhận `quantity` (số nguyên dương) và `request_token` (token ký từ máy chủ).
    - Chống tràn số và ép kiểu an toàn: min 1, max 2,147,483,647.
    - Chống mass assignment: Không nhận bất kỳ trường tồn trước, tồn sau, trạng thái hay người thực hiện từ POST.
    """
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
    notes = forms.CharField(
        label='Nguồn nhập / Ghi chú',
        required=False,
        widget=forms.TextInput(attrs={
            'class': 'qhm-form-control',
            'placeholder': 'Nhà cung cấp, mã phiếu hoặc ghi chú lô hàng (tùy chọn)...',
            'id': 'id_notes',
            'autocomplete': 'off',
        }),
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





class StocktakeAdjustmentForm(forms.Form):
    """
    Biểu mẫu Kiểm kê và Điều chỉnh tồn kho (UI-32) — Task 05.3.
    - Whitelist nghiêm ngặt: Chỉ nhận `counted_quantity` (số nguyên >= 0), `reason` (chuỗi văn bản) và `request_token` (token ký từ máy chủ).
    - Thẩm định số nguyên: min 0, max 2,147,483,647.
    - Lý do điều chỉnh: Bắt buộc, không để trống hoặc chỉ có khoảng trắng, loại bỏ khoảng trắng thừa đầu cuối.
    - Chống mass assignment: Không nhận bất kỳ trường tồn trước, tồn sau, trạng thái hay người thực hiện từ POST.
    """
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


class ReturnedStockInspectionForm(forms.ModelForm):
    """
    Biểu mẫu kiểm định phân loại hàng đổi trả (Task: Returned-stock classification).
    - Whitelist: condition, inspection_notes.
    - Điều kiện: Chờ kiểm định, Đủ điều kiện bán lại, Không đủ điều kiện bán lại.
    """
    condition = forms.ChoiceField(
        choices=ReturnedStockCondition.choices,
        label='Phân loại tình trạng hàng',
        widget=forms.Select(attrs={
            'class': 'qhm-form-control',
            'id': 'id_condition',
        }),
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
        cond = self.cleaned_data.get('condition')
        if cond not in ReturnedStockCondition.values:
            raise ValidationError('Phân loại tình trạng không hợp lệ.')
        return cond

    def clean_inspection_notes(self):
        notes = self.cleaned_data.get('inspection_notes', '')
        return notes.strip() if notes else ''

