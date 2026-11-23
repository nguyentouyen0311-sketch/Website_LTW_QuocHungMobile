"""Views for product, product image, and soft-delete management."""

from django.contrib import messages
from django.db import transaction
from django.db.models import Q
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from accounts.permissions import is_admin, is_sales_staff
from catalog.forms import ProductForm, ProductImageForm, ProductImageVariantAssignForm, ProductPresentationForm
from catalog.models import Category, Product, ProductImage, ProductPresentation
from catalog.utils import format_vnd

from . import parse_positive_id


def product_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    queryset = Product.objects.select_related('category').prefetch_related(
        'variants__inventory',
        'variants__attribute_values__attribute',
        'variants__attribute_values__attribute_value',
    ).all()

    q = request.GET.get('q', '').strip()
    if q:
        q_filter = Q(name__icontains=q) | Q(brand__icontains=q)
        clean_q = q.upper()
        if clean_q.startswith('SP'):
            parsed_id = parse_positive_id(clean_q[2:])
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        else:
            parsed_id = parse_positive_id(q)
            if parsed_id is not None:
                q_filter |= Q(id=parsed_id)
        queryset = queryset.filter(q_filter)

    category_id = request.GET.get('category', '').strip()
    if category_id:
        cat_id = parse_positive_id(category_id)
        if cat_id is not None:
            queryset = queryset.filter(category_id=cat_id)
        else:
            queryset = queryset.none()

    status = request.GET.get('status', '').strip().lower()
    if status == 'active':
        queryset = queryset.filter(is_active=True, is_deleted=False)
    elif status == 'inactive':
        queryset = queryset.filter(is_active=False, is_deleted=False)
    elif status == 'deleted':
        queryset = queryset.filter(is_deleted=True)
    else:
        queryset = queryset.filter(is_deleted=False)

    categories = Category.objects.all().order_by('name')

    products = list(queryset.order_by('id'))
    for product in products:
        variants = list(product.variants.all())
        product.variant_count = len(variants)
        product.available_stock = sum(max(0, getattr(getattr(variant, 'inventory', None), 'available', 0)) for variant in variants)
        first_variant = next((variant for variant in variants if variant.is_active), None)
        product.price_display = format_vnd(first_variant.price) if first_variant else 'Liên hệ'

    selected_product_id = parse_positive_id(request.GET.get('product', ''))
    selected_product = next((product for product in products if product.id == selected_product_id), products[0] if products else None)
    selected_variants = []
    if selected_product:
        selected_variants = list(selected_product.variants.all())
        for variant in selected_variants:
            variant.attributes_display = ', '.join(link.attribute_value.value for link in variant.attribute_values.all()) or 'Phiên bản tiêu chuẩn'
            variant.stock_available = max(0, getattr(getattr(variant, 'inventory', None), 'available', 0))
            variant.price_display = format_vnd(variant.price)

    context = {
        'products': products,
        'categories': categories,
        'selected_category': category_id,
        'selected_status': status,
        'search_query': q,
        'selected_product': selected_product,
        'selected_variants': selected_variants,
        'is_admin_user': is_admin(request.user),
        'active_nav': 'catalog_products',
    }
    return render(request, 'admin/product_list.html', context)


def product_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method == 'POST':
        form = ProductForm(request.POST)
        presentation_form = ProductPresentationForm(request.POST)
        if form.is_valid() and presentation_form.is_valid():
            product = form.save()
            presentation = presentation_form.save(commit=False)
            presentation.product = product
            presentation.save()
            messages.success(request, f"Đã thêm mới sản phẩm '{product.name}' ({product.product_code}) thành công.")
            return redirect(f"{reverse('catalog:product_edit', args=[product.id])}#images")
    else:
        form = ProductForm()
        presentation_form = ProductPresentationForm()

    context = {
        'form': form,
        'presentation_form': presentation_form,
        'title': 'Thêm mới sản phẩm',
        'active_nav': 'catalog_products',
    }
    return render(request, 'admin/product_editor.html', context)


def product_edit_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), id=product_id)

    if product.is_deleted:
        messages.error(request, f"Sản phẩm {product.product_code} đã bị xóa mềm, không thể chỉnh sửa.")
        return redirect('catalog:product_list')

    if request.method == 'POST':
        form = ProductForm(request.POST, instance=product)
        presentation, _ = ProductPresentation.objects.get_or_create(product=product)
        presentation_form = ProductPresentationForm(request.POST, instance=presentation)
        if form.is_valid() and presentation_form.is_valid():
            form.save()
            presentation_form.save()
            messages.success(request, f"Đã cập nhật thông tin sản phẩm '{product.name}' ({product.product_code}) thành công.")
            return redirect(f"{reverse('catalog:product_edit', args=[product.id])}#images")
    else:
        form = ProductForm(instance=product)
        presentation_form = ProductPresentationForm(instance=getattr(product, 'presentation', None))

    context = {
        'form': form,
        'presentation_form': presentation_form,
        'product': product,
        'title': f"Chỉnh sửa sản phẩm: {product.name}",
        'active_nav': 'catalog_products',
    }
    return render(request, 'admin/product_editor.html', context)


def product_detail_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(
        Product.objects.select_related('category').prefetch_related('images'),
        id=product_id
    )
    images = product.images.select_related('variant').all().order_by('sort_order', 'id')
    image_form = ProductImageForm(product=product)

    context = {
        'product': product,
        'images': images,
        'image_form': image_form,
        'has_soft_delete': True,
        'active_nav': 'catalog_products',
    }
    return render(request, 'catalog/product_detail.html', context)


def product_soft_delete_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_admin(request.user):
        return HttpResponseForbidden("Chỉ Quản trị viên (Admin) mới có quyền thực hiện xóa mềm sản phẩm.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if not product.is_deleted:
        product.is_deleted = True
        product.deleted_at = timezone.now()
        product.save(update_fields=['is_deleted', 'deleted_at'])
        messages.success(request, f"Đã xóa mềm sản phẩm '{product.name}' ({product.product_code}) thành công.")
    else:
        messages.info(request, f"Sản phẩm '{product.name}' ({product.product_code}) đã ở trạng thái xóa mềm trước đó.")

    return redirect('catalog:product_list')


def product_image_upload_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể tải ảnh cho sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    form = ProductImageForm(request.POST, request.FILES, product=product)
    if form.is_valid():
        product_image = form.save(commit=False)
        product_image.product = product
        product_image.save()
        if product_image.variant:
            messages.success(request, f"Đã tải lên ảnh mới cho sản phẩm {product.product_code} gắn với biến thể {product_image.variant.sku} thành công.")
        else:
            messages.success(request, f"Đã tải lên ảnh chung mới cho sản phẩm {product.product_code} thành công.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"{error}")

    return redirect('catalog:product_detail', product_id=product.id)


def product_image_delete_view(request, product_id, image_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể xóa hình ảnh của sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    image_obj = get_object_or_404(ProductImage, id=image_id, product=product)

    image_obj.image.delete(save=False)
    image_obj.delete()
    messages.success(request, "Đã xóa ảnh sản phẩm thành công.")

    return redirect('catalog:product_detail', product_id=product.id)


def product_image_assign_variant_view(request, product_id, image_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    if product.is_deleted:
        messages.error(request, "Không thể chỉnh sửa ảnh của sản phẩm đã bị xóa mềm.")
        return redirect('catalog:product_detail', product_id=product.id)

    image_obj = get_object_or_404(ProductImage, id=image_id, product=product)

    form = ProductImageVariantAssignForm(request.POST, instance=image_obj, product=product)
    if form.is_valid():
        form.save()
        if image_obj.variant:
            messages.success(request, f"Đã cập nhật ảnh #{image_obj.id} gắn với biến thể {image_obj.variant.sku} thành công.")
        else:
            messages.success(request, f"Đã chuyển ảnh #{image_obj.id} thành ảnh chung của sản phẩm thành công.")
    else:
        for field, errors in form.errors.items():
            for error in errors:
                messages.error(request, f"Lỗi cập nhật ảnh #{image_obj.id}: {error}")

    return redirect('catalog:product_detail', product_id=product.id)


def product_images_reorder_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Thao tác yêu cầu phương thức POST.")

    product = get_object_or_404(Product, id=product_id)

    with transaction.atomic():
        locked_product = Product.objects.select_for_update().get(id=product.id)
        if locked_product.is_deleted:
            messages.error(request, "Không thể thay đổi thứ tự ảnh của sản phẩm đã bị xóa mềm.")
            return redirect('catalog:product_detail', product_id=product.id)

        existing_images = {img.id: img for img in locked_product.images.select_for_update().all()}

        action_raw = request.POST.get('action')
        if action_raw is None or action_raw == '':
            action = 'save_order'
        else:
            action = action_raw.strip()
            if action not in ('save_order', 'set_primary'):
                messages.error(request, f"Hành động '{action}' không hợp lệ.")
                return redirect('catalog:product_detail', product_id=product.id)

        if action == 'set_primary':
            primary_id_list = request.POST.getlist('primary_image_id')
            if len(primary_id_list) != 1:
                messages.error(request, "Dữ liệu chỉ định ảnh đại diện không hợp lệ.")
                return redirect('catalog:product_detail', product_id=product.id)

            primary_id_raw = primary_id_list[0].strip()
            parsed_primary_id = parse_positive_id(primary_id_raw)
            if parsed_primary_id is None or parsed_primary_id not in existing_images:
                messages.error(request, "Ảnh đại diện được chỉ định không hợp lệ hoặc không thuộc sản phẩm này.")
                return redirect('catalog:product_detail', product_id=product.id)

            other_images = [img for img_id, img in existing_images.items() if img_id != parsed_primary_id]
            other_images.sort(key=lambda x: (x.sort_order, x.id))

            existing_images[parsed_primary_id].sort_order = 0
            existing_images[parsed_primary_id].save(update_fields=['sort_order'])

            for idx, img in enumerate(other_images, start=1):
                if img.sort_order != idx:
                    img.sort_order = idx
                    img.save(update_fields=['sort_order'])

            messages.success(request, f"Đã đặt ảnh #{parsed_primary_id} làm ảnh đại diện chính.")
            return redirect('catalog:product_detail', product_id=product.id)

        updates = {}
        seen_ids = set()
        found_any = False

        for key, values in request.POST.lists():
            if key.startswith('sort_order_'):
                found_any = True
                if len(values) > 1:
                    messages.error(request, f"Dữ liệu gửi lên chứa khóa thứ tự trùng lặp ({key}). Thao tác đã bị hủy.")
                    return redirect('catalog:product_detail', product_id=product.id)

                img_id_str = key[len('sort_order_'):]
                parsed_id = parse_positive_id(img_id_str)
                if parsed_id is None or parsed_id not in existing_images:
                    messages.error(request, f"Dữ liệu ảnh không hợp lệ hoặc chứa mã ảnh không thuộc sản phẩm ({img_id_str}).")
                    return redirect('catalog:product_detail', product_id=product.id)

                if parsed_id in seen_ids:
                    messages.error(request, f"Phát hiện mã ảnh trùng lặp trong dữ liệu gửi lên (#{parsed_id}). Thao tác đã bị hủy.")
                    return redirect('catalog:product_detail', product_id=product.id)
                seen_ids.add(parsed_id)

                val_str = str(values[0]).strip()
                if not (val_str.isascii() and val_str.isdigit()) or len(val_str) > 19:
                    messages.error(request, f"Thứ tự hiển thị '{val_str}' không hợp lệ. Vui lòng nhập số nguyên không âm.")
                    return redirect('catalog:product_detail', product_id=product.id)

                val_int = int(val_str)
                if val_int > 9223372036854775807:
                    messages.error(request, "Thứ tự hiển thị vượt quá giới hạn tối đa cho phép.")
                    return redirect('catalog:product_detail', product_id=product.id)

                updates[parsed_id] = val_int

        if not found_any or not updates:
            messages.warning(request, "Không có thay đổi thứ tự nào được ghi nhận.")
            return redirect('catalog:product_detail', product_id=product.id)

        for img_id, new_order in updates.items():
            img = existing_images[img_id]
            if img.sort_order != new_order:
                img.sort_order = new_order
                img.save(update_fields=['sort_order'])

        messages.success(request, "Đã lưu cập nhật thứ tự hiển thị ảnh thành công.")
        return redirect('catalog:product_detail', product_id=product.id)
