"""Public storefront browsing and product review views."""

from django.contrib import messages
from django.core.exceptions import ObjectDoesNotExist
from django.core.paginator import Paginator
from django.db.models import Avg, Count, Exists, F, OuterRef, Q
from django.http import HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from urllib.parse import quote, urlencode

from catalog.models import Category, Product, ProductReview, ProductVariant
from catalog.utils import format_vnd
from coupons.models import Coupon

from . import parse_positive_id


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
    category_icon_keywords = (
        ('laptop', 'laptop'), ('điện thoại', 'phone'), ('smartphone', 'phone'),
        ('tablet', 'tablet'), ('máy tính bảng', 'tablet'), ('âm thanh', 'audio'),
        ('tai nghe', 'audio'), ('loa', 'audio'), ('phụ kiện', 'accessory'),
    )
    for category in visible_categories:
        category_name = category.name.casefold()
        category.icon_key = next(
            (icon_key for keyword, icon_key in category_icon_keywords if keyword in category_name),
            'accessory',
        )
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


def promotion_list_view(request):
    """Show active vouchers and products with a genuine variant discount."""
    now = timezone.now()
    coupons = list(
        Coupon.objects.filter(is_active=True, starts_at__lte=now, ends_at__gt=now)
        .filter(used_count__lt=F('max_uses') - F('reserved_count'))
        .order_by('ends_at', 'id')
    )
    for coupon in coupons:
        coupon.scope_display = 'Áp dụng cho toàn bộ sản phẩm' if coupon.applies_to_all else 'Áp dụng cho sản phẩm được chọn'

    products = list(
        Product.objects.filter(
            category__is_visible=True,
            is_active=True,
            is_deleted=False,
            variants__is_active=True,
            variants__original_price__gt=F('variants__price'),
        )
        .select_related('category', 'presentation')
        .prefetch_related('images', 'variants')
        .annotate(
            review_average=Avg('reviews__rating', filter=Q(reviews__is_visible=True)),
            review_count=Count('reviews', filter=Q(reviews__is_visible=True)),
        )
        .distinct()
        .order_by('-id')
    )
    for product in products:
        discounted_variants = [
            variant for variant in product.variants.all()
            if variant.is_active and variant.original_price and variant.original_price > variant.price
        ]
        product.card_variant = max(
            discounted_variants,
            key=lambda variant: (variant.original_price - variant.price) / variant.original_price,
        )
        product.card_image = next(iter(product.images.all()), None)
        product.price_display = format_vnd(product.card_variant.price)
        product.original_price_display = format_vnd(product.card_variant.original_price)
        product.discount_percent = int(
            (product.card_variant.original_price - product.card_variant.price) * 100
            / product.card_variant.original_price
        )
        product.rating_display = f'{float(product.review_average):.1f}' if product.review_average else '0.0'
        presentation = getattr(product, 'presentation', None)
        product.short_description = getattr(presentation, 'short_description', '') or product.description or ''

    return render(request, 'storefront/promotions.html', {
        'coupons': coupons,
        'products': products,
        'page_key': 'promotions',
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
