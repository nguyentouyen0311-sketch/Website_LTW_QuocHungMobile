"""Forms for product attributes and their values."""

from django import forms

from catalog.models import Attribute, AttributeValue, normalize_attribute_text


class AttributeForm(forms.ModelForm):

    class Meta:
        model = Attribute
        fields = ['name']
        labels = {
            'name': 'Tên thuộc tính',
        }
        widgets = {
            'name': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Màu sắc, Dung lượng, RAM...',
                'maxlength': '80',
                'autocomplete': 'off',
            }),
        }

    def clean_name(self):
        raw_name = self.cleaned_data.get('name', '')
        normalized = normalize_attribute_text(raw_name)
        if not normalized:
            raise forms.ValidationError('Tên thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Tên thuộc tính không được vượt quá 80 ký tự.')

        norm_key = normalized.casefold()
        qs = Attribute.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)

        for other in qs:
            if normalize_attribute_text(other.name).casefold() == norm_key:
                raise forms.ValidationError(
                    f"Tên thuộc tính '{normalized}' đã tồn tại trong hệ thống (trùng lặp sau khi chuẩn hóa)."
                )

        return normalized


class AttributeValueForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = ['value']
        labels = {
            'value': 'Giá trị thuộc tính',
        }
        widgets = {
            'value': forms.TextInput(attrs={
                'class': 'form-input',
                'placeholder': 'Ví dụ: Đen Titanium, 256GB, 8GB...',
                'maxlength': '80',
                'autocomplete': 'off',
            }),
        }

    def __init__(self, *args, attribute=None, **kwargs):
        super().__init__(*args, **kwargs)
        if attribute is not None:
            self.attribute = attribute
        elif self.instance and getattr(self.instance, 'attribute_id', None):
            self.attribute = self.instance.attribute
        else:
            self.attribute = None

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')

        target_attr = self.attribute or (self.instance.attribute if self.instance and getattr(self.instance, 'attribute_id', None) else None)
        if target_attr:
            norm_key = normalized.casefold()
            qs = AttributeValue.objects.filter(attribute=target_attr)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            for other in qs:
                if normalize_attribute_text(other.value).casefold() == norm_key:
                    raise forms.ValidationError(
                        f"Giá trị '{normalized}' đã tồn tại trong thuộc tính '{target_attr.name}'."
                    )

        return normalized

    def save(self, commit=True):
        instance = super().save(commit=False)
        if self.attribute:
            instance.attribute = self.attribute
        if commit:
            instance.save()
        return instance


class AttributeValueAdminForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = '__all__'

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance and self.instance.pk and 'attribute' in self.fields:
            self.fields['attribute'].disabled = True

    def clean_attribute(self):
        attr = self.cleaned_data.get('attribute')
        if self.instance and self.instance.pk:
            return self.instance.attribute
        return attr

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')
        return normalized

    def clean(self):
        cleaned_data = super().clean()
        attr = self.instance.attribute if (self.instance and self.instance.pk) else cleaned_data.get('attribute')
        val = cleaned_data.get('value')
        if attr and val:
            norm_key = normalize_attribute_text(val).casefold()
            qs = AttributeValue.objects.filter(attribute=attr)
            if self.instance and self.instance.pk:
                qs = qs.exclude(pk=self.instance.pk)
            for other in qs:
                if normalize_attribute_text(other.value).casefold() == norm_key:
                    self.add_error(
                        'value',
                        f"Giá trị '{val}' đã tồn tại trong thuộc tính '{attr.name}'."
                    )
        return cleaned_data


class AttributeValueInlineForm(forms.ModelForm):

    class Meta:
        model = AttributeValue
        fields = ('value',)

    def clean_value(self):
        raw_val = self.cleaned_data.get('value', '')
        normalized = normalize_attribute_text(raw_val)
        if not normalized:
            raise forms.ValidationError('Giá trị thuộc tính không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 80:
            raise forms.ValidationError('Giá trị thuộc tính không được vượt quá 80 ký tự.')
        return normalized


class AttributeValueInlineFormSet(forms.BaseInlineFormSet):

    def clean(self):
        seen_values = {}
        managed_pks = set()
        for form in self.forms:
            if getattr(form, 'instance', None) and form.instance.pk:
                managed_pks.add(form.instance.pk)

        existing_db_keys = {}
        if self.instance and self.instance.pk:
            for ev in AttributeValue.objects.filter(attribute=self.instance).exclude(pk__in=managed_pks):
                k = normalize_attribute_text(ev.value).casefold()
                existing_db_keys[k] = ev.value

        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue
            if self.can_delete and self._should_delete_form(form):
                continue
            if form.cleaned_data.get('DELETE', False):
                continue

            raw_val = form.cleaned_data.get('value')
            if not raw_val:
                continue

            normalized = normalize_attribute_text(raw_val)
            norm_key = normalized.casefold()

            if norm_key in seen_values:
                form.add_error(
                    'value',
                    f"Giá trị '{raw_val}' bị trùng lặp với dòng khác trong cùng thuộc tính."
                )
            else:
                seen_values[norm_key] = raw_val

            if norm_key in existing_db_keys:
                form.add_error(
                    'value',
                    f"Giá trị '{raw_val}' đã tồn tại trong thuộc tính '{self.instance.name}'."
                )

        super().clean()
