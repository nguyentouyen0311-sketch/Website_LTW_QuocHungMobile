"""Views for product attributes, values, and SKU variants."""

from django.contrib import messages
from django.db import transaction
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import is_admin, is_sales_staff
from catalog.forms import AttributeForm, AttributeValueForm, ProductVariantForm
from catalog.models import Attribute, AttributeValue, Product, ProductVariant


def attribute_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attributes = Attribute.objects.prefetch_related('values').all().order_by('name')
    products = Product.objects.filter(is_deleted=False).order_by('name')
    variants = ProductVariant.objects.filter(is_deleted=False).select_related('product').prefetch_related(
        'attribute_values__attribute', 'attribute_values__attribute_value'
    ).order_by('product__name', 'id')
    for variant in variants:
        variant.attributes_display = ', '.join(
            f'{link.attribute.name}: {link.attribute_value.value}' for link in variant.attribute_values.all()
        ) or 'Phiên bản tiêu chuẩn'

    context = {
        'attributes': attributes,
        'products': products,
        'variants': variants,
        'is_admin': is_admin(request.user),
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/variant_attribute_center_v3.html', context)


def attribute_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = AttributeForm(request.POST)
        if form.is_valid():
            attribute = form.save()
            messages.success(request, f"Đã thêm thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeForm()

    context = {
        'form': form,
        'title': 'Thêm thuộc tính mới',
        'is_edit': False,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v2.html', context)


def attribute_edit_view(request, attribute_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)

    if request.method == 'POST':
        form = AttributeForm(request.POST, instance=attribute)
        if form.is_valid():
            attribute = form.save()
            messages.success(request, f"Đã cập nhật thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeForm(instance=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'title': f"Chỉnh sửa thuộc tính: {attribute.name}",
        'is_edit': True,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v2.html', context)


def attribute_delete_view(request, attribute_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if not is_admin(request.user):
        return HttpResponseForbidden("Chỉ Quản trị viên (Admin) mới có quyền xóa thuộc tính.")
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)
    used_value_count = AttributeValue.objects.filter(
        attribute=attribute,
        variant_value_links__isnull=False,
    ).distinct().count()
    if attribute.variant_links.exists() or used_value_count:
        messages.error(
            request,
            f"Không thể xóa thuộc tính '{attribute.name}' vì đã được dùng trong biến thể sản phẩm."
        )
        return redirect('catalog:attribute_list')

    attribute_name = attribute.name
    with transaction.atomic():
        attribute.values.all().delete()
        attribute.delete()
    messages.success(request, f"Đã xóa thuộc tính '{attribute_name}' và các giá trị chưa sử dụng của nó.")
    return redirect('catalog:attribute_list')


def attribute_value_create_view(request, attribute_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)

    if request.method == 'POST':
        form = AttributeValueForm(request.POST, attribute=attribute)
        if form.is_valid():
            val = form.save()
            messages.success(request, f"Đã thêm giá trị '{val.value}' cho thuộc tính '{attribute.name}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeValueForm(attribute=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'title': f"Thêm giá trị cho '{attribute.name}'",
        'is_edit': False,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v2.html', context)


def attribute_value_edit_view(request, attribute_id, value_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)
    value_obj = get_object_or_404(AttributeValue, pk=value_id, attribute=attribute)

    if request.method == 'POST':
        form = AttributeValueForm(request.POST, instance=value_obj, attribute=attribute)
        if form.is_valid():
            val = form.save()
            messages.success(request, f"Đã cập nhật giá trị '{val.value}' thành công.")
            return redirect('catalog:attribute_list')
    else:
        form = AttributeValueForm(instance=value_obj, attribute=attribute)

    context = {
        'form': form,
        'attribute': attribute,
        'value_obj': value_obj,
        'title': f"Chỉnh sửa giá trị: {value_obj.value}",
        'is_edit': True,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v2.html', context)


def attribute_value_delete_view(request, attribute_id, value_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if not is_admin(request.user):
        return HttpResponseForbidden("Chỉ Quản trị viên (Admin) mới có quyền xóa giá trị thuộc tính.")
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    attribute = get_object_or_404(Attribute, pk=attribute_id)
    value_obj = get_object_or_404(AttributeValue, pk=value_id, attribute=attribute)
    if value_obj.variant_value_links.exists():
        messages.error(
            request,
            f"Không thể xóa giá trị '{value_obj.value}' vì đã được dùng trong biến thể sản phẩm."
        )
        return redirect('catalog:attribute_list')

    value_name = value_obj.value
    value_obj.delete()
    messages.success(request, f"Đã xóa giá trị '{value_name}' của thuộc tính '{attribute.name}'.")
    return redirect('catalog:attribute_list')


def product_variant_list_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)
    variants = product.variants.filter(is_deleted=False).prefetch_related(
        'attribute_values__attribute',
        'attribute_values__attribute_value'
    ).order_by('id')

    variant_data = []
    for v in variants:
        if v.combination_key == "":
            comb_text = "Mặc định (Không thuộc tính)"
            is_empty_comb = True
        else:
            links = list(v.attribute_values.all())
            if links:
                comb_text = " | ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
            else:
                comb_text = v.combination_key
            is_empty_comb = False

        variant_data.append({
            'obj': v,
            'combination_display': comb_text,
            'is_empty_combination': is_empty_comb,
        })

    context = {
        'product': product,
        'variant_data': variant_data,
        'variant_count': len(variant_data),
        'active_nav': 'catalog_products',
        'is_admin': is_admin(request.user),
    }
    return render(request, 'catalog/variant_list.html', context)


def product_variant_create_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)

    if product.is_deleted or variant.is_deleted:
        messages.error(request, 'Không thể tạo biến thể cho sản phẩm đã bị xóa mềm.')
        return redirect('catalog:product_variant_list', product_id=product.id)

    if request.method == 'POST':
        form = ProductVariantForm(product=product, data=request.POST)
        if form.is_valid():
            variant = form.save()
            messages.success(request, f"Đã thêm biến thể mới với mã SKU '{variant.sku}' thành công.")
            return redirect('catalog:product_variant_list', product_id=product.id)
    else:
        form = ProductVariantForm(product=product)

    context = {
        'product': product,
        'form': form,
        'title': f"Thêm biến thể mới — {product.name}",
        'is_edit': False,
        'sku_is_automatic': True,
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v3.html', context)


def product_variant_edit_view(request, product_id, variant_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)
    variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)

    if product.is_deleted:
        messages.error(request, 'Không thể chỉnh sửa biến thể của sản phẩm đã bị xóa mềm.')
        return redirect('catalog:product_variant_list', product_id=product.id)

    if request.method == 'POST':
        form = ProductVariantForm(product=product, data=request.POST, instance=variant)
        if form.is_valid():
            v = form.save()
            messages.success(request, f"Đã cập nhật biến thể '{v.sku}' thành công.")
            return redirect('catalog:product_variant_list', product_id=product.id)
    else:
        form = ProductVariantForm(product=product, instance=variant)

    context = {
        'product': product,
        'variant': variant,
        'form': form,
        'title': f"Chỉnh sửa biến thể: {variant.sku}",
        'is_edit': True,
        'is_admin': is_admin(request.user),
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v3.html', context)


def product_variant_delete_view(request, product_id, variant_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")
    if not is_admin(request.user):
        return HttpResponseForbidden("Chỉ Quản trị viên (Admin) mới có quyền xóa biến thể sản phẩm.")
    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, pk=product_id)
    variant = get_object_or_404(ProductVariant, pk=variant_id, product=product)
    if variant.is_deleted:
        messages.info(request, f"Biến thể '{variant.sku}' đã được ngừng kinh doanh trước đó.")
        return redirect('catalog:attribute_list')

    variant.is_deleted = True
    variant.is_active = False
    variant.deleted_at = timezone.now()
    variant.save(update_fields=['is_deleted', 'is_active', 'deleted_at'])
    messages.success(request, f"Đã ngừng kinh doanh biến thể '{variant.sku}'. Lịch sử liên quan được giữ nguyên.")
    return redirect('catalog:attribute_list')
