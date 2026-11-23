"""Forms for SKU variants and variant attribute values."""

import re
import unicodedata

from django import forms
from django.core.exceptions import NON_FIELD_ERRORS
from django.db import transaction

from catalog.models import (
    Attribute,
    AttributeValue,
    Product,
    ProductVariant,
    VariantAttributeValue,
    derive_combination_key,
)


class ProductVariantForm(forms.ModelForm):

    class Meta:
        model = ProductVariant
        fields = ['price', 'original_price', 'is_active']
        labels = {
            'price': 'Giá bán (VNĐ)',
            'original_price': 'Giá gốc / niêm yết (VNĐ)',
            'is_active': 'Kích hoạt kinh doanh (Đang bán)',
        }
        widgets = {
            'price': forms.NumberInput(attrs={
                'class': 'qhm-form-control',
                'placeholder': '0.00',
                'step': '0.01',
                'min': '0',
            }),
            'original_price': forms.NumberInput(attrs={
                'class': 'qhm-form-control',
                'placeholder': '0.00 (tùy chọn)',
                'step': '0.01',
                'min': '0',
            }),
            'is_active': forms.CheckboxInput(attrs={
                'class': 'form-checkbox',
            }),
        }

    def _update_errors(self, errors):
        if isinstance(errors, forms.ValidationError) and hasattr(errors, 'error_dict'):
            new_error_dict = {}
            for field, error_list in errors.error_dict.items():
                if field in self.fields or field == NON_FIELD_ERRORS:
                    new_error_dict[field] = error_list
                else:
                    new_error_dict.setdefault(NON_FIELD_ERRORS, []).extend(error_list)
            errors = forms.ValidationError(new_error_dict)
        super()._update_errors(errors)

    def __init__(self, product, *args, **kwargs):
        self.product = product
        super().__init__(*args, **kwargs)

        self.available_attributes = list(Attribute.objects.all().order_by('name'))
        self.attribute_fields = []

        initial_attr_values = {}
        if self.instance and self.instance.pk:
            for link in self.instance.attribute_values.select_related('attribute', 'attribute_value'):
                initial_attr_values[link.attribute_id] = link.attribute_value_id

        for attr in self.available_attributes:
            field_name = f'attr_{attr.id}'
            self.attribute_fields.append((field_name, attr))
            self.fields[field_name] = forms.ModelChoiceField(
                queryset=AttributeValue.objects.filter(attribute=attr).order_by('value'),
                required=False,
                empty_label="-- Không chọn --",
                label=attr.name,
                initial=initial_attr_values.get(attr.id),
                widget=forms.Select(attrs={'class': 'qhm-form-control'})
            )

    def get_attribute_fields(self):
        for field_name, attr in self.attribute_fields:
            yield attr, self[field_name]

    @staticmethod
    def _sku_token(value):
        normalized = unicodedata.normalize('NFKD', str(value))
        normalized = ''.join(char for char in normalized if not unicodedata.combining(char))
        normalized = re.sub(r'[^A-Za-z0-9]+', '-', normalized).strip('-').upper()
        return normalized or 'GIA-TRI'

    def _build_automatic_sku(self, selected_values):
        value_tokens = [self._sku_token(value.value) for value in selected_values]
        suffix = '-'.join(value_tokens) if value_tokens else 'MAC-DINH'
        sku = f'{self.product.product_code}-{suffix}'
        if len(sku) > 60:
            value_ids = '-'.join(str(value.id) for value in selected_values) or '0'
            available_length = 60 - len(self.product.product_code) - len(value_ids) - 3
            sku = f'{self.product.product_code}-{suffix[:max(1, available_length)]}-V{value_ids}'
        return sku[:60]

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is None:
            raise forms.ValidationError('Giá bán không được để trống.')
        if price < 0:
            raise forms.ValidationError('Giá bán không được âm.')
        return price

    def clean_original_price(self):
        original_price = self.cleaned_data.get('original_price')
        if original_price is not None and original_price < 0:
            raise forms.ValidationError('Giá gốc không được âm.')
        return original_price

    def clean(self):
        cleaned_data = super().clean()

        if not self.product:
            raise forms.ValidationError('Thiếu thông tin sản phẩm cơ sở.')
        if self.product.is_deleted:
            raise forms.ValidationError('Không thể tạo hoặc chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.')
        if self.instance and self.instance.pk:
            if self.instance.product_id != self.product.id:
                raise forms.ValidationError('Không được phép chuyển biến thể sang sản phẩm khác.')

        price = cleaned_data.get('price')
        original_price = cleaned_data.get('original_price')
        if price is not None and original_price is not None:
            if original_price < price:
                self.add_error('original_price', 'Giá gốc phải lớn hơn hoặc bằng giá bán.')

        selected_values = []
        for field_name, attr in self.attribute_fields:
            val = cleaned_data.get(field_name)
            if val:
                if val.attribute_id != attr.id:
                    self.add_error(field_name, f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'.")
                    continue
                selected_values.append(val)

        val_ids = [v.id for v in selected_values]
        derived_key = derive_combination_key(val_ids)
        cleaned_data['derived_combination_key'] = derived_key
        cleaned_data['selected_values'] = selected_values
        if not self.instance.pk:
            automatic_sku = self._build_automatic_sku(selected_values)
            cleaned_data['automatic_sku'] = automatic_sku
            # ModelForm excludes `sku`, so make it available before Django runs
            # model validation in _post_clean().
            self.instance.sku = automatic_sku

        siblings = ProductVariant.objects.filter(product_id=self.product.id)
        if self.instance and self.instance.pk:
            siblings = siblings.exclude(pk=self.instance.pk)

        if derived_key == "":
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError('Sản phẩm đã có biến thể mặc định không thuộc tính.')
            if siblings.exclude(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể tạo biến thể không có thuộc tính vì sản phẩm đã có các biến thể theo thuộc tính. '
                    'Không được pha trộn giữa biến thể có thuộc tính và không có thuộc tính trong cùng một sản phẩm.'
                )
        else:
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể tạo biến thể có thuộc tính vì sản phẩm đang sử dụng biến thể mặc định không thuộc tính. '
                    'Vui lòng chuyển đổi hoặc xử lý biến thể hiện có trước khi thêm biến thể theo thuộc tính.'
                )
            if siblings.filter(combination_key=derived_key).exists():
                raise forms.ValidationError(
                    f"Tổ hợp thuộc tính này đã tồn tại cho sản phẩm '{self.product.name}'."
                )

        return cleaned_data

    def save(self, commit=True):
        instance = super().save(commit=False)
        instance.product = self.product
        if not instance.pk:
            instance.sku = self.cleaned_data['automatic_sku']
        instance.price = self.cleaned_data['price']
        instance.original_price = self.cleaned_data.get('original_price')
        instance.is_active = self.cleaned_data.get('is_active', True)
        instance.combination_key = self.cleaned_data['derived_combination_key']

        if commit:
            with transaction.atomic():
                siblings = ProductVariant.objects.filter(product_id=self.product.id)
                if instance.pk:
                    siblings = siblings.exclude(pk=instance.pk)

                if instance.combination_key == "":
                    if siblings.exists():
                        raise forms.ValidationError('Xung đột quy tắc không pha trộn khi lưu biến thể không thuộc tính.')
                else:
                    if siblings.filter(combination_key="").exists() or siblings.filter(combination_key=instance.combination_key).exists():
                        raise forms.ValidationError('Xung đột tổ hợp thuộc tính hoặc quy tắc không pha trộn.')

                instance.save()

                if instance.pk:
                    instance.attribute_values.all().delete()

                for val in self.cleaned_data.get('selected_values', []):
                    VariantAttributeValue.objects.create(
                        variant=instance,
                        attribute=val.attribute,
                        attribute_value=val
                    )

        return instance


class ProductVariantAdminForm(forms.ModelForm):

    class Meta:
        model = ProductVariant
        fields = ('product', 'sku', 'price', 'original_price', 'is_active')

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if 'product' in self.fields:
            if not self.instance.pk:
                self.fields['product'].queryset = Product.objects.all().order_by('name')
            else:
                self.fields['product'].disabled = True

    def _update_errors(self, errors):

        if isinstance(errors, forms.ValidationError) and hasattr(errors, 'error_dict'):
            new_error_dict = {}
            for field, error_list in errors.error_dict.items():
                if field in self.fields or field == NON_FIELD_ERRORS:
                    new_error_dict[field] = error_list
                else:
                    new_error_dict.setdefault(NON_FIELD_ERRORS, []).extend(error_list)
            errors = forms.ValidationError(new_error_dict)
        super()._update_errors(errors)

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and product.is_deleted:
            raise forms.ValidationError('Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')
        return product

    def clean_sku(self):
        raw_sku = self.cleaned_data.get('sku', '')
        normalized = raw_sku.strip().upper()
        if not normalized:
            raise forms.ValidationError('Mã SKU không được để trống hoặc chỉ chứa khoảng trắng.')
        if len(normalized) > 60:
            raise forms.ValidationError('Mã SKU không được vượt quá 60 ký tự.')
        qs = ProductVariant.objects.all()
        if self.instance and self.instance.pk:
            qs = qs.exclude(pk=self.instance.pk)
        for other in qs:
            if (other.sku or '').strip().upper() == normalized:
                raise forms.ValidationError(f"Mã SKU '{normalized}' đã tồn tại trong hệ thống.")
        return normalized

    def clean_price(self):
        price = self.cleaned_data.get('price')
        if price is None:
            raise forms.ValidationError('Giá bán không được để trống.')
        if price < 0:
            raise forms.ValidationError('Giá bán không được âm.')
        return price

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product') or getattr(self.instance, 'product', None)
        if product and product.is_deleted:
            if 'product' in self.fields:
                self.add_error('product', 'Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')
            else:
                self.add_error(None, 'Không thể tạo hoặc sửa biến thể của sản phẩm đã bị xóa mềm.')

        price = cleaned_data.get('price')
        orig_price = cleaned_data.get('original_price')
        if price is not None and orig_price is not None and orig_price < price:
            self.add_error('original_price', 'Giá gốc phải lớn hơn hoặc bằng giá bán.')

        if self.data:
            if self.instance and self.instance.pk:
                try:
                    initial_forms_val = int(self.data.get('attribute_values-INITIAL_FORMS', -1))
                    actual_count = self.instance.attribute_values.count()
                    if initial_forms_val != actual_count:
                        return cleaned_data
                except (ValueError, TypeError):
                    return cleaned_data

                expected_pks = set(self.instance.attribute_values.values_list('pk', flat=True))
                try:
                    total_forms = int(self.data.get('attribute_values-TOTAL_FORMS', 0))
                    seen_pks = []
                    for i in range(total_forms):
                        rid_str = self.data.get(f'attribute_values-{i}-id')
                        if rid_str is not None and str(rid_str).strip() != '':
                            if not str(rid_str).strip().isdigit():
                                self.add_error(None, "Mã hàng liên kết thuộc tính không hợp lệ.")
                                return cleaned_data
                            rid = int(str(rid_str).strip())
                            if rid not in expected_pks:
                                self.add_error(None, f"Dữ liệu hàng liên kết thuộc tính (ID: {rid}) không thuộc về biến thể này hoặc bị giả mạo.")
                                return cleaned_data
                            if i >= initial_forms_val:
                                self.add_error(None, f"Dòng thêm mới không được sử dụng lại ID của hàng thuộc tính hiện có (ID: {rid}).")
                                return cleaned_data
                            if rid in seen_pks:
                                self.add_error(None, f"Mã hàng liên kết thuộc tính (ID: {rid}) bị lặp lại trong biểu mẫu.")
                                return cleaned_data
                            seen_pks.append(rid)
                except (ValueError, TypeError):
                    return cleaned_data

            staged_val_ids = []
            for k, v in self.data.items():
                if k.startswith('attribute_values-') and k.endswith('-attribute_value') and v:
                    prefix = k[:-len('-attribute_value')]
                    if self.data.get(f"{prefix}-DELETE") in ('1', 'true', 'True', 'on'):
                        continue
                    try:
                        staged_val_ids.append(int(v))
                    except (ValueError, TypeError):
                        pass
            if staged_val_ids or any(k.startswith('attribute_values-') for k in self.data):
                staged_key = derive_combination_key(staged_val_ids)
                self.instance.combination_key = staged_key

        return cleaned_data


class VariantAttributeValueInlineForm(forms.ModelForm):

    class Meta:
        model = VariantAttributeValue
        fields = ('attribute', 'attribute_value')

    def validate_unique(self):

        pass

    def validate_constraints(self):

        pass

    def clean(self):
        cleaned_data = super().clean()
        attr = cleaned_data.get('attribute')
        val = cleaned_data.get('attribute_value')
        if attr and val:
            if val.attribute_id != attr.id:
                self.add_error(
                    'attribute_value',
                    f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'."
                )
        return cleaned_data


class VariantAttributeValueInlineFormSet(forms.BaseInlineFormSet):

    def _extract_form_row_id(self, form):
        row_id = None
        if hasattr(form, 'cleaned_data') and form.cleaned_data:
            raw_id = form.cleaned_data.get('id')
            if raw_id:
                if getattr(raw_id, 'pk', None):
                    row_id = raw_id.pk
                elif isinstance(raw_id, int):
                    row_id = raw_id

        if row_id is None and getattr(form, 'instance', None) and form.instance.pk:
            row_id = form.instance.pk

        if row_id is None and form.is_bound:
            pk_val = form[self.model._meta.pk.name].value()
            if pk_val is None and hasattr(self, 'data') and self.data:
                pk_val = self.data.get(f"{form.prefix}-{self.model._meta.pk.name}")
            if pk_val is not None and str(pk_val).strip() != '':
                if str(pk_val).strip().isdigit():
                    row_id = int(str(pk_val).strip())
                else:
                    raise forms.ValidationError('Mã hàng liên kết thuộc tính không hợp lệ.')
        return row_id

    def _validate_management_and_row_identities(self, expected_existing_pks):

        if not self.management_form.is_valid():
            raise forms.ValidationError('Dữ liệu quản lý biểu mẫu thuộc tính không hợp lệ.')

        expected_initial_count = len(expected_existing_pks)
        submitted_initial_forms = self.management_form.cleaned_data.get('INITIAL_FORMS')
        if submitted_initial_forms != expected_initial_count:
            raise forms.ValidationError(
                'Dữ liệu quản lý biểu mẫu (INITIAL_FORMS) không khớp với số lượng thuộc tính hiện có trong cơ sở dữ liệu.'
            )

        seen_all_pks = []
        seen_initial_pks = []
        for i, form in enumerate(self.forms):
            row_id = self._extract_form_row_id(form)
            is_initial = (i < expected_initial_count)

            if is_initial:
                if row_id is None:
                    raise forms.ValidationError('Thiếu thông tin nhận diện dòng thuộc tính ban đầu.')
                if self.instance and self.instance.pk:
                    if row_id not in expected_existing_pks:
                        raise forms.ValidationError(
                            f"Dữ liệu hàng liên kết thuộc tính (ID: {row_id}) không thuộc về biến thể này hoặc bị giả mạo."
                        )
                if row_id in seen_all_pks:
                    raise forms.ValidationError(
                        f"Mã hàng liên kết thuộc tính (ID: {row_id}) bị lặp lại trong biểu mẫu."
                    )
                seen_all_pks.append(row_id)
                seen_initial_pks.append(row_id)
            else:
                if row_id is not None:
                    if self.instance and self.instance.pk and row_id in expected_existing_pks:
                        raise forms.ValidationError(
                            f"Dòng thêm mới không được sử dụng lại ID của hàng thuộc tính hiện có (ID: {row_id})."
                        )
                    if row_id in seen_all_pks:
                        raise forms.ValidationError(
                            f"Mã hàng liên kết thuộc tính (ID: {row_id}) bị lặp lại trong biểu mẫu."
                        )
                    raise forms.ValidationError(
                        f"Dữ liệu hàng liên kết thuộc tính (ID: {row_id}) không thuộc về biến thể này hoặc bị giả mạo."
                    )

        if self.instance and self.instance.pk:
            if set(seen_initial_pks) != expected_existing_pks or len(seen_initial_pks) != len(expected_existing_pks):
                raise forms.ValidationError('Thiếu hoặc sai lệch danh sách các dòng thuộc tính ban đầu.')

    def _collect_and_validate_attribute_rows(self):

        seen_attributes = set()
        selected_value_ids = []

        for form in self.forms:
            if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                continue

            if self.can_delete and self._should_delete_form(form):
                continue
            if form.cleaned_data.get('DELETE', False):
                continue

            attr = form.cleaned_data.get('attribute')
            val = form.cleaned_data.get('attribute_value')

            if not attr and not val:
                continue
            if not attr and val:
                form.add_error('attribute', 'Vui lòng chọn thuộc tính tương ứng với giá trị này.')
                continue
            if attr and not val:
                form.add_error('attribute_value', 'Vui lòng chọn giá trị cho thuộc tính này.')
                continue

            if attr.id in seen_attributes:
                form.add_error('attribute', f"Thuộc tính '{attr.name}' đã được chọn ở một dòng khác.")
            else:
                seen_attributes.add(attr.id)

            if val.attribute_id != attr.id:
                form.add_error('attribute_value', f"Giá trị '{val.value}' không thuộc về thuộc tính '{attr.name}'.")

            selected_value_ids.append(val.id)

        return selected_value_ids

    def _validate_combination_and_siblings(self, selected_value_ids):

        derived_key = derive_combination_key(selected_value_ids)
        self.instance.combination_key = derived_key

        product = getattr(self.instance, 'product', None)
        if not product:
            return

        if product.is_deleted:
            raise forms.ValidationError('Không thể lưu biến thể của sản phẩm đã bị xóa mềm.')

        siblings = ProductVariant.objects.filter(product_id=product.id)
        if self.instance and self.instance.pk:
            siblings = siblings.exclude(pk=self.instance.pk)

        if derived_key == "":
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError('Sản phẩm đã có biến thể mặc định không thuộc tính.')
            if siblings.exclude(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể để trống thuộc tính vì sản phẩm đã có các biến thể theo thuộc tính (quy tắc không pha trộn).'
                )
        else:
            if siblings.filter(combination_key="").exists():
                raise forms.ValidationError(
                    'Không thể gán thuộc tính vì sản phẩm đang sử dụng biến thể mặc định không thuộc tính (quy tắc không pha trộn).'
                )
            if siblings.filter(combination_key=derived_key).exists():
                raise forms.ValidationError(
                    f"Tổ hợp thuộc tính này đã tồn tại cho sản phẩm '{product.name}'."
                )

    def clean(self):
        super().clean()

        expected_existing_pks = set()
        if self.instance and self.instance.pk:
            expected_existing_pks = set(self.instance.attribute_values.values_list('pk', flat=True))

        self._validate_management_and_row_identities(expected_existing_pks)

        if any(self.errors):
            return

        selected_value_ids = self._collect_and_validate_attribute_rows()

        if any(self.errors):
            return

        self._validate_combination_and_siblings(selected_value_ids)

    def save(self, commit=True):
        if not commit:
            return super().save(commit=False)

        with transaction.atomic():
            self.instance.attribute_values.all().delete()
            saved_instances = []
            created_pks = set()
            for i, form in enumerate(self.forms):
                if not hasattr(form, 'cleaned_data') or not form.cleaned_data:
                    continue
                if self.can_delete and self._should_delete_form(form):
                    continue
                if form.cleaned_data.get('DELETE', False):
                    continue
                attr = form.cleaned_data.get('attribute')
                val = form.cleaned_data.get('attribute_value')
                if attr and val:
                    row_id = None
                    if i < self.initial_form_count():
                        raw_id = form.cleaned_data.get('id')
                        if raw_id and getattr(raw_id, 'pk', None):
                            row_id = raw_id.pk
                        elif getattr(form, 'instance', None) and form.instance.pk:
                            row_id = form.instance.pk

                    create_kwargs = {
                        'variant': self.instance,
                        'attribute': attr,
                        'attribute_value': val
                    }
                    if row_id is not None and row_id not in created_pks:
                        create_kwargs['id'] = row_id
                        created_pks.add(row_id)

                    link = VariantAttributeValue.objects.create(**create_kwargs)
                    form.instance = link
                    saved_instances.append(link)
            self.new_objects = saved_instances
            self.changed_objects = []
            self.deleted_objects = []
            return saved_instances
