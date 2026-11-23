"""Forms for category management."""

from django import forms

from catalog.models import Category, normalize_category_name


class CategoryForm(forms.ModelForm):

    class Meta:
        model = Category
        fields = ['name', 'description', 'is_visible']
        labels = {
            'name': 'Tên danh mục',
            'description': 'Mô tả danh mục',
            'is_visible': 'Hiển thị trên hệ thống',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Nhập tên danh mục (ví dụ: Điện thoại, Máy tính bảng, Phụ kiện...)',
                'maxlength': '100',
                'autocomplete': 'off',
            }),
            'description': forms.Textarea(attrs={
                'class': 'form-input',
                'placeholder': 'Mô tả tóm tắt danh mục (tùy chọn)',
                'rows': 3,
            }),
            'is_visible': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = normalize_category_name(raw_name)
        if not normalized:
            raise forms.ValidationError('Tên danh mục không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 100:
            raise forms.ValidationError('Tên danh mục không được vượt quá 100 ký tự.')

        norm_key = normalized.casefold()
        qs = Category.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        for other in qs:
            if normalize_category_name(other.name).casefold() == norm_key:
                raise forms.ValidationError(
                    f"Tên danh mục '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                )

        return normalized
