"""Forms and validation for product images."""

from django import forms

from catalog.models import Product, ProductImage, ProductVariant


def validate_product_image_file(img_file):

    if not img_file:
        raise forms.ValidationError("Vui lòng chọn tập tin ảnh để tải lên.")

    max_size = 5 * 1024 * 1024
    if hasattr(img_file, 'size') and img_file.size > max_size:
        raise forms.ValidationError(
            f"Dung lượng tập tin quá lớn ({img_file.size / 1024 / 1024:.1f}MB). Tối đa cho phép là 5MB."
        )

    from PIL import Image
    try:
        img = Image.open(img_file)
        img.verify()
        image_format = (img.format or '').upper()
    except Exception:
        raise forms.ValidationError("Tập tin hình ảnh không hợp lệ hoặc bị hỏng.")
    finally:
        if hasattr(img_file, 'seek'):
            img_file.seek(0)

    allowed_formats = ['JPEG', 'PNG', 'WEBP', 'GIF']
    if image_format not in allowed_formats:
        raise forms.ValidationError(
            f"Định dạng ảnh '{image_format}' không được hỗ trợ. Chỉ chấp nhận các định dạng: JPEG, PNG, WEBP, GIF."
        )

    return img_file


class ProductImageForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = ['image', 'variant', 'sort_order']
        labels = {
            'image': 'Tập tin hình ảnh',
            'variant': 'Biến thể liên kết',
            'sort_order': 'Thứ tự hiển thị',
        }
        widgets = {
            'image': forms.FileInput(attrs={
                'class': 'form-input',
                'accept': 'image/jpeg,image/png,image/webp,image/gif',
                'id': 'product-image-file-input',
            }),
            'variant': forms.Select(attrs={
                'class': 'form-input',
                'id': 'product-image-variant-select',
            }),
            'sort_order': forms.NumberInput(attrs={
                'class': 'form-input',
                'min': '0',
                'placeholder': '0',
                'id': 'product-image-sort-order',
            }),
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            if product is not None:
                self.fields['variant'].queryset = product.variants.all().order_by('id')
            else:
                self.fields['variant'].queryset = ProductVariant.objects.all().order_by('id')
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")
        if 'sort_order' in self.fields:
            self.fields['sort_order'].required = False
            self.fields['sort_order'].initial = 0

    def clean_sort_order(self):
        val = self.cleaned_data.get('sort_order')
        return val if val is not None else 0

    def clean_image(self):
        img_file = self.cleaned_data.get('image')
        return validate_product_image_file(img_file)

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get('variant')
        prod = self.product or (self.instance.product if hasattr(self.instance, 'product') else None)
        if prod and prod.is_deleted:
            raise forms.ValidationError('Không thể thêm hoặc chỉnh sửa ảnh cho sản phẩm đã bị xóa mềm.')
        if prod and variant and variant.product_id != prod.id:
            self.add_error('variant', 'Biến thể được chọn không thuộc sản phẩm này.')
        return cleaned_data


class ProductImageVariantAssignForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = ['variant']
        labels = {
            'variant': 'Biến thể liên kết',
        }

    def __init__(self, *args, product=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.product = product
        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            if product is not None:
                self.fields['variant'].queryset = product.variants.all().order_by('id')
            else:
                self.fields['variant'].queryset = ProductVariant.objects.all().order_by('id')
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")
            self.fields['variant'].widget.attrs.update({'class': 'form-input form-input-sm'})

    def clean(self):
        cleaned_data = super().clean()
        variant = cleaned_data.get('variant')
        prod = self.product or (self.instance.product if hasattr(self.instance, 'product') else None)
        if prod and prod.is_deleted:
            raise forms.ValidationError('Không thể chỉnh sửa ảnh của sản phẩm đã bị xóa mềm.')
        if prod and variant and variant.product_id != prod.id:
            self.add_error('variant', 'Biến thể được chọn không thuộc sản phẩm này.')
        return cleaned_data


class ProductImageAdminForm(forms.ModelForm):

    class Meta:
        model = ProductImage
        fields = '__all__'

    def __init__(self, *args, parent_product=None, **kwargs):
        super().__init__(*args, **kwargs)
        if parent_product is not None:
            self.parent_product = parent_product

        target_product_id = None
        if hasattr(self, 'parent_product') and self.parent_product:
            target_product_id = self.parent_product.pk
        elif self.instance and getattr(self.instance, 'product_id', None):
            target_product_id = self.instance.product_id

        if 'product' in self.fields:
            if not self.is_bound:
                self.fields['product'].queryset = Product.objects.filter(is_deleted=False)
            else:
                self.fields['product'].queryset = Product.objects.all()

        if 'variant' in self.fields:
            self.fields['variant'].empty_label = "-- Ảnh chung của sản phẩm --"
            self.fields['variant'].required = False
            self.fields['variant'].label_from_instance = lambda obj: f"{obj.sku}" + (f" ({obj.attribute_summary})" if obj.attribute_summary != "Mặc định" else "")

            if target_product_id:
                self.fields['variant'].queryset = ProductVariant.objects.filter(product_id=target_product_id).order_by('id')
            elif 'product' in self.fields:
                if self.is_bound and self.data.get('product'):
                    try:
                        self.fields['variant'].queryset = ProductVariant.objects.filter(product_id=int(self.data.get('product'))).order_by('id')
                    except (ValueError, TypeError):
                        self.fields['variant'].queryset = ProductVariant.objects.none()
                else:
                    self.fields['variant'].queryset = ProductVariant.objects.none()
            else:
                pass

    def clean_product(self):
        product = self.cleaned_data.get('product')
        if product and product.is_deleted:
            raise forms.ValidationError('Không thể thêm hoặc chuyển ảnh cho sản phẩm đã bị xóa mềm.')
        return product

    def clean_image(self):
        img_file = self.cleaned_data.get('image')
        if img_file and (hasattr(img_file, 'file') or hasattr(img_file, 'chunks')):
            return validate_product_image_file(img_file)
        return img_file

    def clean(self):
        cleaned_data = super().clean()
        product = cleaned_data.get('product') or getattr(self, 'parent_product', None) or (self.instance.product if hasattr(self.instance, 'product') else None)
        variant = cleaned_data.get('variant')

        if product and product.is_deleted:
            msg = 'Không thể thêm hoặc chuyển ảnh cho sản phẩm đã bị xóa mềm.'
            if 'product' in self.fields:
                self.add_error('product', msg)
            else:
                self.add_error(None, msg)

        if product and variant and variant.product_id != product.id:
            msg = 'Biến thể được chọn không thuộc sản phẩm này.'
            if 'variant' in self.fields:
                self.add_error('variant', msg)
            else:
                self.add_error(None, msg)

        return cleaned_data
