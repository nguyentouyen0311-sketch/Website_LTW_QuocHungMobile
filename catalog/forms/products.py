"""Forms for core product content and presentation."""

from django import forms

from catalog.models import Product, ProductPresentation


class ProductForm(forms.ModelForm):

    class Meta:
        model = Product
        fields = ['category', 'name', 'brand', 'description', 'is_active']
        labels = {
            'category': 'Danh mục sản phẩm',
            'name': 'Tên sản phẩm',
            'brand': 'Thương hiệu',
            'description': 'Mô tả chi tiết',
            'is_active': 'Kích hoạt kinh doanh (Đang bán)',
        }
        widgets = {
            'category': forms.Select(attrs={
                'class': 'form-input',
            }),
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: iPhone 15 Pro Max 256GB',
                'maxlength': '150',
                'autocomplete': 'off',
            }),
            'brand': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Apple, Samsung, Xiaomi...',
                'maxlength': '100',
                'autocomplete': 'off',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-input',
                'placeholder': 'Mô tả chi tiết sản phẩm, cấu hình tổng quan...',
                'rows': 4,
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields['category'].queryset = Category.objects.all().order_by('name')
        self.fields['category'].empty_label = "-- Chọn danh mục sản phẩm --"

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = " ".join(raw_name.strip().split())
        if not normalized:
            raise forms.ValidationError('Tên sản phẩm không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 150:
            raise forms.ValidationError('Tên sản phẩm không được vượt quá 150 ký tự.')
        return normalized

    def clean_brand(self):
        raw_brand = self.cleaned_data.get('brand', '')
        if raw_brand:
            raw_brand = " ".join(raw_brand.strip().split())
            if len(raw_brand) > 100:
                raise forms.ValidationError('Tên thương hiệu không được vượt quá 100 ký tự.')
        return raw_brand or None


class ProductPresentationForm(forms.ModelForm):
    technical_specs = forms.CharField(required=False, label='Thông số kỹ thuật', widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 5, 'placeholder': 'Màn hình: 6.7 inch\nChip: A18 Pro\nCamera: 48MP'}), help_text='Mỗi dòng theo mẫu: Tên thông số: Giá trị.')
    highlights = forms.CharField(required=False, label='Điểm nổi bật', widget=forms.Textarea(attrs={'class': 'form-input', 'rows': 4, 'placeholder': 'Màn hình sáng, mượt\nCamera chụp đêm tốt\nBảo hành chính hãng'}), help_text='Mỗi dòng là một điểm nổi bật sẽ hiển thị cho khách.')

    class Meta:
        model = ProductPresentation
        fields = ['short_description', 'technical_specs', 'highlights', 'warranty_text']
        widgets = {
            'short_description': forms.Textarea(attrs={'class': 'form-input', 'rows': 3, 'placeholder': 'Tóm tắt điểm nổi bật hiển thị ngoài storefront'}),
            'warranty_text': forms.TextInput(attrs={'class': 'form-input', 'maxlength': '120'}),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk:
            self.initial['technical_specs'] = '\n'.join(f'{key}: {value}' for key, value in (self.instance.technical_specs or {}).items())
            self.initial['highlights'] = '\n'.join(self.instance.highlights or [])

    def clean_technical_specs(self):
        specs = {}
        for line in self.cleaned_data.get('technical_specs', '').splitlines():
            line = line.strip()
            if not line:
                continue
            if ':' not in line:
                raise forms.ValidationError('Mỗi thông số cần theo mẫu “Tên thông số: Giá trị”.')
            key, value = (part.strip() for part in line.split(':', 1))
            if not key or not value:
                raise forms.ValidationError('Tên thông số và giá trị đều không được để trống.')
            specs[key] = value
        return specs

    def clean_highlights(self):
        return [line.strip() for line in self.cleaned_data.get('highlights', '').splitlines() if line.strip()]
