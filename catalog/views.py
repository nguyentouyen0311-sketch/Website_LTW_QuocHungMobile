from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.db import transaction
from django.db.models import Q, Exists, OuterRef, Avg, Count
from django.http import HttpResponseForbidden, HttpResponseNotAllowed, HttpResponseBadRequest
from django.shortcuts import render, redirect, get_object_or_404
from django.core.paginator import Paginator
from django.urls import reverse
from django.utils import timezone
from urllib.parse import urlencode, quote

from accounts.permissions import is_sales_staff, is_admin
from catalog.models import (
    Category, Product, ProductImage, Attribute, AttributeValue,
    ProductVariant, VariantAttributeValue, ProductReview, ProductPresentation,
)
from catalog.forms import (
    CategoryForm, ProductForm, ProductImageForm, ProductImageVariantAssignForm,
    AttributeForm, AttributeValueForm, ProductVariantForm, ProductPresentationForm,
)
from coupons.models import Coupon


def parse_positive_id(val, max_digits=12):
    if not isinstance(val, str):
        return None
    val = val.strip()
    if not val or len(val) > max_digits:
        return None
    if not (val.isascii() and val.isdigit()):
        return None
    try:
        parsed = int(val)
        return parsed if parsed > 0 else None
    except ValueError:
        return None


def category_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    editing_category = None
    edit_id = request.GET.get('edit', '').strip()
    if edit_id.isdigit():
        editing_category = get_object_or_404(Category, id=int(edit_id))

    if request.method == 'POST':
        category_id = request.POST.get('category_id', '').strip()
        instance = get_object_or_404(Category, id=int(category_id)) if category_id.isdigit() else None
        form = CategoryForm(request.POST, instance=instance)
        if form.is_valid():
            saved_category = form.save()
            messages.success(request, f"Đã {'cập nhật' if instance else 'thêm'} danh mục '{saved_category.name}' thành công.")
            return redirect('catalog:category_list')
    else:
        form = CategoryForm(instance=editing_category)

    categories = Category.objects.annotate(product_count=Count('products')).order_by('id')
    search_query = request.GET.get('q', '').strip()
    selected_status = request.GET.get('status', '').strip()
    if search_query:
        categories = categories.filter(Q(name__icontains=search_query) | Q(description__icontains=search_query))
    if selected_status == 'visible':
        categories = categories.filter(is_visible=True)
    elif selected_status == 'hidden':
        categories = categories.filter(is_visible=False)

    context = {
        'categories': categories,
        'form': form,
        'editing_category': editing_category,
        'search_query': search_query,
        'selected_status': selected_status,
        'active_nav': 'catalog_categories',
    }
    return render(request, 'admin/category_management.html', context)


def category_create_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    return redirect('catalog:category_list')


def category_edit_view(request, category_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    get_object_or_404(Category, id=category_id)
    return redirect(f"{reverse('catalog:category_list')}?edit={category_id}")


def category_toggle_visibility_view(request, category_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'], "Phương thức không được hỗ trợ. Thao tác yêu cầu POST.")

    category = get_object_or_404(Category, id=category_id)
    action = request.POST.get('action', '').strip().lower()

    if action not in ['hide', 'show']:
        return HttpResponseBadRequest("Hành động không hợp lệ. Chỉ chấp nhận 'hide' hoặc 'show'.")

    if action == 'hide':
        category.is_visible = False
        action_text = "ẩn khỏi giao diện khách hàng"
    else:
        category.is_visible = True
        action_text = "kích hoạt hiển thị cho khách hàng"

    category.save(update_fields=['is_visible'])
    messages.success(request, f"Danh mục '{category.name}' đã được {action_text}.")
    return redirect('catalog:category_list')


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


from .utils import format_vnd


def storefront_category_view(request, category_slug):
    category_definitions = {
        'phones': ('Điện thoại chính hãng', ('điện thoại', 'smartphone', 'phone')),
        'laptops': ('Laptop chính hãng', ('laptop', 'máy tính xách tay', 'notebook')),
        'tablets': ('Tablet chính hãng', ('tablet', 'máy tính bảng', 'ipad')),
        'audio': ('Thiết bị âm thanh', ('âm thanh', 'tai nghe', 'loa', 'audio')),
        'accessories': ('Phụ kiện công nghệ', ('phụ kiện', 'accessory')),
    }
    definition = category_definitions.get(category_slug)
    if definition is None:
        return HttpResponseBadRequest('Danh mục không hợp lệ.')

    fallback_title, keywords = definition
    visible_categories = list(Category.objects.filter(is_visible=True).order_by('name'))
    selected_category = next((item for item in visible_categories if any(keyword in item.name.casefold() for keyword in keywords)), None)
    products = []
    if selected_category:
        products = list(Product.objects.filter(category=selected_category, is_active=True, is_deleted=False).prefetch_related('images', 'variants').order_by('name'))

    for item in products:
        first_image = next(iter(item.images.all()), None)
        first_variant = next((variant for variant in item.variants.all() if variant.is_active), None)
        item.image_url = first_image.image.url if first_image else ''
        item.price_display = format_vnd(first_variant.price) if first_variant else 'Liên hệ'
        item.original_price_display = format_vnd(first_variant.original_price) if first_variant and first_variant.original_price else ''
        item.product_url = reverse('catalog:public_product_detail', args=[item.id])

    return render(request, 'storefront/category_live.html', {
        'categories': visible_categories,
        'selected_category': selected_category,
        'products': products,
        'page_key': category_slug,
        'storefront_nav_category_id': selected_category.id if selected_category else None,
        'category_title': selected_category.name if selected_category else fallback_title,
        'category_description': selected_category.description if selected_category and selected_category.description else '',
    })


def homepage_view(request):
    visible_categories = list(Category.objects.filter(is_visible=True).order_by('name'))
    products = list(
        Product.objects.filter(category__is_visible=True, is_active=True, is_deleted=False)
        .select_related('category', 'presentation')
        .prefetch_related('images', 'variants')
        .annotate(
            review_average=Avg('reviews__rating', filter=Q(reviews__is_visible=True)),
            review_count=Count('reviews', filter=Q(reviews__is_visible=True)),
        )
        .order_by('-id')[:12]
    )
    for product in products:
        product.card_image = next(iter(product.images.all()), None)
        product.card_variant = next((variant for variant in product.variants.all() if variant.is_active), None)
        product.price_display = format_vnd(product.card_variant.price) if product.card_variant else 'Liên hệ'
        product.original_price_display = format_vnd(product.card_variant.original_price) if product.card_variant and product.card_variant.original_price else ''
        product.discount_percent = int(((product.card_variant.original_price - product.card_variant.price) * 100 / product.card_variant.original_price)) if product.card_variant and product.card_variant.original_price and product.card_variant.original_price > product.card_variant.price else 0
        product.rating_display = f'{float(product.review_average):.1f}' if product.review_average else '0.0'
        presentation = getattr(product, 'presentation', None)
        product.short_description = getattr(presentation, 'short_description', '') or product.description or ''
    now = timezone.now()
    active_coupon = Coupon.objects.filter(is_active=True, starts_at__lte=now, ends_at__gt=now).order_by('ends_at').first()
    return render(request, 'storefront/homepage.html', {
        'categories': visible_categories[:8],
        'products': products,
        'deal_products': products[:4],
        'featured_products': products[4:],
        'hero_product': products[0] if products else None,
        'active_coupon': active_coupon,
        'page_key': 'home',
    })


def public_catalog_view(request, category_id=None):
    visible_categories = Category.objects.filter(is_visible=True).order_by('name')

    selected_category = get_object_or_404(visible_categories, id=category_id) if category_id else None
    products = Product.objects.filter(category__is_visible=True, is_active=True, is_deleted=False).select_related('category').prefetch_related('images', 'variants')
    if selected_category:
        products = products.filter(category=selected_category)
    products = list(products.order_by('name'))
    for item in products:
        first_image = next(iter(item.images.all()), None)
        first_variant = next((variant for variant in item.variants.all() if variant.is_active), None)
        item.image_url = first_image.image.url if first_image else ''
        item.price_display = format_vnd(first_variant.price) if first_variant else 'Liên hệ'
        item.original_price_display = format_vnd(first_variant.original_price) if first_variant and first_variant.original_price else ''
        item.product_url = reverse('catalog:public_product_detail', args=[item.id])
    return render(request, 'storefront/category_live.html', {
        'categories': visible_categories,
        'selected_category': selected_category,
        'products': products,
        'page_key': '',
        'storefront_nav_category_id': selected_category.id if selected_category else None,
        'category_title': selected_category.name if selected_category else 'Danh mục sản phẩm',
        'category_description': selected_category.description if selected_category else '',
    })

    selected_category = None
    invalid_category = False

    if category_id:
        selected_category = get_object_or_404(Category, id=category_id, is_visible=True)
    else:
        query_cat = request.GET.get('category', '').strip()
        if query_cat:
            cat_id = parse_positive_id(query_cat)
            if cat_id is not None:
                selected_category = Category.objects.filter(id=cat_id, is_visible=True).first()
                if not selected_category:
                    invalid_category = True
            else:
                invalid_category = True

    products_qs = (
        Product.objects.filter(
            category__is_visible=True,
            is_active=True,
            is_deleted=False,
        )
        .select_related('category')
        .prefetch_related('images')
        .annotate(
            has_active_variants=Exists(
                ProductVariant.objects.filter(product=OuterRef('pk'), is_active=True)
            )
        )
    )

    if invalid_category:
        products_qs = products_qs.none()
    elif selected_category:
        products_qs = products_qs.filter(category=selected_category)

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
        products_qs = products_qs.filter(q_filter)

    products_qs = products_qs.order_by('category__name', 'name')

    paginator = Paginator(products_qs, 12)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)

    preserved_params = {}
    if q:
        preserved_params['q'] = q
    query_cat_param = request.GET.get('category', '').strip()
    if not category_id and query_cat_param and not invalid_category and selected_category:
        preserved_params['category'] = str(selected_category.id)
    preserved_querystring = urlencode(preserved_params, quote_via=quote)

    context = {
        'categories': visible_categories,
        'selected_category': selected_category,
        'products': page_obj,
        'page_obj': page_obj,
        'total_results_count': paginator.count,
        'search_query': q,
        'preserved_querystring': preserved_querystring,
        'active_nav': 'public_catalog',
        'customer_notice': 'Thông tin phiên bản và giá bán đang được cập nhật',
    }
    return render(request, 'catalog/public_catalog.html', context)


def public_product_detail_view(request, product_id):
    product = get_object_or_404(
        Product.objects.select_related('category'),
        id=product_id,
        category__is_visible=True,
        is_active=True,
        is_deleted=False,
    )

    if request.method == 'POST' and request.POST.get('action') == 'create_review':
        rating = request.POST.get('rating', '').strip()
        content = request.POST.get('content', '').strip()
        if not rating.isdigit() or not 1 <= int(rating) <= 5 or not content:
            messages.error(request, 'Vui lòng chọn số sao từ 1 đến 5 và nhập nội dung đánh giá.')
        else:
            reviewer_name = request.POST.get('reviewer_name', '').strip()
            if request.user.is_authenticated:
                reviewer_name = request.user.get_full_name().strip() or request.user.username
            ProductReview.objects.create(
                product=product,
                user=request.user if request.user.is_authenticated else None,
                reviewer_name=reviewer_name or 'Khách hàng Quốc Hùng Mobile',
                rating=int(rating),
                variant_text=request.POST.get('variant_text', '').strip(),
                content=content,
            )
            messages.success(request, 'Cảm ơn bạn. Đánh giá đã được gửi và chờ kiểm duyệt.')
        return redirect(f'{reverse("catalog:public_product_detail", args=[product.id])}#reviews')

    gallery_images = list(product.images.all().order_by('sort_order', 'id'))
    variants = list(
        product.variants.filter(is_active=True).select_related('inventory').prefetch_related(
            'attribute_values__attribute', 'attribute_values__attribute_value'
        ).order_by('id')
    )
    selected_variant = variants[0] if variants else None
    requested_variant = request.GET.get('variant')
    if requested_variant and requested_variant.isdigit():
        selected_variant = next((v for v in variants if v.id == int(requested_variant)), selected_variant)

    attribute_options = {}
    variant_matrix = []
    for variant in variants:
        links = list(variant.attribute_values.all())
        variant.option_values = {link.attribute.name: link.attribute_value.value for link in links}
        variant.attributes_display = ', '.join(link.attribute_value.value for link in links) or 'Phiên bản tiêu chuẩn'
        variant.price_display = format_vnd(variant.price)
        variant.original_price_display = format_vnd(variant.original_price) if variant.original_price else ''
        variant.available_count = max(0, getattr(getattr(variant, 'inventory', None), 'available', 0))
        variant.discount_percent = int(((variant.original_price - variant.price) * 100 / variant.original_price)) if variant.original_price and variant.original_price > variant.price else 0
        for link in links:
            name = link.attribute.name
            attribute_options.setdefault(name, {})[link.attribute_value.value] = True
        variant_matrix.append({
            'id': variant.id,
            'options': variant.option_values,
            'price': variant.price_display,
            'originalPrice': variant.original_price_display,
            'stock': variant.available_count,
            'discount': variant.discount_percent,
        })

    attribute_groups = []
    default_palette = ['#C9B08D', '#D6D5D1', '#FAFAFA', '#252525', '#BFD0C9', '#A7B2C4']
    for attr_name, values in attribute_options.items():
        is_color = 'màu' in attr_name.casefold() or 'color' in attr_name.casefold()
        options = []
        for idx, value in enumerate(values):
            option = {
                'name': value,
                'selected': bool(selected_variant and selected_variant.option_values.get(attr_name) == value),
            }
            if is_color:
                option['hex'] = default_palette[idx % len(default_palette)]
            options.append(option)
        attribute_groups.append({
            'name': attr_name,
            'options': options,
            'is_color': is_color,
        })

    now = timezone.now()
    coupons = Coupon.objects.filter(is_active=True, starts_at__lte=now, ends_at__gt=now).filter(
        Q(applies_to_all=True) | Q(coupon_products__product=product)
    ).distinct()
    for coupon in coupons:
        coupon.description = f'Giảm {coupon.value_display} cho đơn từ {coupon.min_order_amount_display}'

    review_filter = request.GET.get('review_filter', 'all')
    reviews_qs = ProductReview.objects.filter(product=product, is_visible=True).select_related('user').prefetch_related('media')
    all_review_stats = reviews_qs.aggregate(avg=Avg('rating'), total=Count('id'))
    total_reviews = all_review_stats['total'] or 0
    avg_rating = round(float(all_review_stats['avg'] or 0), 1)
    counts = {row['rating']: row['count'] for row in reviews_qs.values('rating').annotate(count=Count('id'))}
    star_breakdown = []
    for star in range(5, 0, -1):
        count = counts.get(star, 0)
        star_breakdown.append({'value': star, 'count': count, 'percent': round((count / total_reviews * 100) if total_reviews else 0)})
    media_reviews_count = reviews_qs.filter(media__isnull=False).distinct().count()
    if review_filter in {'1', '2', '3', '4', '5'}:
        reviews_qs = reviews_qs.filter(rating=int(review_filter))
    elif review_filter == 'media':
        reviews_qs = reviews_qs.filter(media__isnull=False).distinct()
    elif review_filter == 'verified':
        reviews_qs = reviews_qs.filter(is_verified_purchase=True)
    reviews_page = Paginator(reviews_qs, 5).get_page(request.GET.get('review_page', 1))
    for review in reviews_page:
        review.initials = ''.join(part[0] for part in review.reviewer_name.split()[:2]).upper() or 'KH'
        review.stars = range(review.rating)
        review.empty_stars = range(5 - review.rating)

    presentation = getattr(product, 'presentation', None)
    technical_specs = list(presentation.technical_specs.items()) if presentation else []
    highlights = presentation.highlights if presentation else []
    related_products = list(
        Product.objects.filter(category=product.category, is_active=True, is_deleted=False).exclude(pk=product.pk)
        .prefetch_related('images', 'variants').order_by('-id')[:4]
    )
    for item in related_products:
        item.card_image = next(iter(item.images.all()), None)
        card_variant = next((v for v in item.variants.all() if v.is_active), None)
        item.card_variant = card_variant
        item.price_display = format_vnd(card_variant.price) if card_variant else 'Liên hệ'
        item.original_price_display = format_vnd(card_variant.original_price) if card_variant and card_variant.original_price else ''
        item.discount_percent = int(((card_variant.original_price - card_variant.price) * 100 / card_variant.original_price)) if card_variant and card_variant.original_price and card_variant.original_price > card_variant.price else 0

    return render(request, 'storefront/product_detail.html', {
        'product': product, 'gallery_images': gallery_images, 'variants': variants,
        'selected_variant': selected_variant, 'attribute_groups': attribute_groups, 'variant_matrix': variant_matrix,
        'coupons': coupons, 'presentation': presentation, 'technical_specs': technical_specs,
        'highlights': highlights, 'avg_rating': avg_rating, 'total_reviews': total_reviews,
        'star_breakdown': star_breakdown, 'star_counts': counts, 'media_reviews_count': media_reviews_count,
        'reviews_page': reviews_page, 'review_filter': review_filter, 'related_products': related_products,
        'page_key': 'phones',
        'storefront_nav_category_id': product.category_id,
    })

    all_images = list(product.images.all().order_by('sort_order', 'id'))
    visible_categories = Category.objects.filter(is_visible=True).order_by('name')

    active_variants = list(
        product.variants.filter(is_active=True)
        .select_related('inventory')
        .prefetch_related('attribute_values__attribute', 'attribute_values__attribute_value')
        .order_by('id')
    )

    has_variants = bool(active_variants)
    selected_variant = None
    selected_variant_data = None
    selected_gallery_images = []
    primary_img = None
    variants_json = {}

    if has_variants:
        for v in active_variants:
            links = list(v.attribute_values.all())
            if links:
                links.sort(key=lambda link: (link.attribute.name, link.id))
                v.display_label = ", ".join(f"{link.attribute.name}: {link.attribute_value.value}" for link in links)
            else:
                v.display_label = "Phiên bản mặc định"

            try:
                inv = v.inventory
                has_inv = True
                avail = max(0, inv.available)
            except (AttributeError, ObjectDoesNotExist):
                has_inv = False
                avail = 0

            v.avail_count = avail
            v.has_inv = has_inv
            v.is_in_stock = (avail > 0)

            v_linked = [img for img in all_images if img.variant_id == v.id]
            v_general = [img for img in all_images if img.variant_id is None]
            v_imgs = v_linked + v_general

            has_disc = bool(v.original_price is not None and v.original_price > v.price)

            variants_json[str(v.id)] = {
                'id': v.id,
                'sku': v.sku,
                'label': v.display_label,
                'price': str(v.price),
                'price_formatted': format_vnd(v.price),
                'original_price': str(v.original_price) if v.original_price is not None else None,
                'original_price_formatted': format_vnd(v.original_price) if has_disc else "",
                'has_discount': has_disc,
                'available': avail,
                'has_inventory': has_inv,
                'is_in_stock': v.is_in_stock,
                'images': [
                    {
                        'id': img.id,
                        'url': img.image.url,
                        'alt': f"{product.name} - {v.display_label}",
                    }
                    for img in v_imgs
                ],
            }

        req_variant_id = request.GET.get('variant')
        requested_variant = None
        if req_variant_id:
            try:
                var_id_int = int(req_variant_id)
                requested_variant = next((v for v in active_variants if v.id == var_id_int), None)
            except (ValueError, TypeError):
                requested_variant = None

        if requested_variant:
            selected_variant = requested_variant
        else:
            in_stock_variants = [v for v in active_variants if v.avail_count > 0]
            if in_stock_variants:
                selected_variant = min(in_stock_variants, key=lambda v: v.id)
            else:
                selected_variant = min(active_variants, key=lambda v: v.id)

        selected_variant_data = variants_json[str(selected_variant.id)]
        selected_linked = [img for img in all_images if img.variant_id == selected_variant.id]
        selected_general = [img for img in all_images if img.variant_id is None]
        selected_gallery_images = selected_linked + selected_general
        if selected_gallery_images:
            primary_img = selected_gallery_images[0]
    else:
        selected_gallery_images = [img for img in all_images if img.variant_id is None]
        if selected_gallery_images:
            primary_img = selected_gallery_images[0]

    initial_quantity = '1'
    req_qty = request.GET.get('quantity')
    if req_qty:
        cleaned_qty = req_qty.strip()
        if cleaned_qty:
            initial_quantity = cleaned_qty

    context = {
        'product': product,
        'has_variants': has_variants,
        'active_variants': active_variants,
        'selected_variant': selected_variant,
        'selected_variant_data': selected_variant_data,
        'selected_gallery_images': selected_gallery_images,
        'primary_img': primary_img,
        'variants_json': variants_json,
        'initial_quantity': initial_quantity,
        'categories': visible_categories,
        'active_nav': 'public_catalog',
        'customer_notice': 'Thông tin phiên bản và giá bán đang được cập nhật',
    }
    return render(request, 'catalog/public_product_detail.html', context)


def attribute_list_view(request):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    attributes = Attribute.objects.prefetch_related('values').all().order_by('name')
    products = Product.objects.filter(is_deleted=False).order_by('name')
    variants = ProductVariant.objects.select_related('product').prefetch_related(
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


def product_variant_list_view(request, product_id):
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_sales_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    product = get_object_or_404(Product.objects.select_related('category'), pk=product_id)
    variants = product.variants.all().prefetch_related(
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

    if product.is_deleted:
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
        'active_nav': 'catalog_attributes',
    }
    return render(request, 'admin/standard_form_v3.html', context)
