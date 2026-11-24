from django.conf import settings
from django.contrib import messages
from django.shortcuts import get_object_or_404, redirect, render, resolve_url
from django.urls import reverse
from catalog.models import Category, ProductReview
from orders.forms import PurchasedProductReviewForm
from orders.models import Order, OrderStatus


def purchased_product_review_view(request, pk, item_pk):
    if not request.user.is_authenticated:
        login_url = resolve_url(settings.LOGIN_URL)
        return redirect(f"{login_url}?next={request.get_full_path()}")

    order = get_object_or_404(Order, pk=pk, customer=request.user)
    item = get_object_or_404(order.items.select_related('variant__product'), pk=item_pk)
    if order.order_status != OrderStatus.DELIVERED:
        messages.error(request, 'Bạn chỉ có thể đánh giá sản phẩm sau khi đơn hàng đã giao thành công.')
        return redirect('orders:order_detail', pk=order.pk)

    existing_review = ProductReview.objects.filter(order_item=item).first()
    if existing_review:
        messages.info(request, 'Bạn đã đánh giá sản phẩm này trong đơn hàng.')
        return redirect(f"{reverse('catalog:public_product_detail', args=[item.variant.product_id])}#reviews")

    if request.method == 'POST':
        form = PurchasedProductReviewForm(request.POST)
        if form.is_valid():
            ProductReview.objects.create(
                product=item.variant.product,
                user=request.user,
                order_item=item,
                reviewer_name=request.user.get_full_name().strip() or request.user.username,
                rating=int(form.cleaned_data['rating']),
                variant_text=item.variant_snapshot,
                content=form.cleaned_data['content'],
                is_verified_purchase=True,
            )
            messages.success(request, 'Cảm ơn bạn. Đánh giá đã được đăng trên trang sản phẩm.')
            return redirect(f"{reverse('catalog:public_product_detail', args=[item.variant.product_id])}#reviews")
    else:
        form = PurchasedProductReviewForm()

    return render(request, 'orders/purchased_product_review.html', {
        'order': order, 'item': item, 'form': form,
        'categories': Category.objects.filter(is_visible=True).order_by('name'),
        'active_nav': 'cart', 'title': f'Đánh giá {item.product_name_snapshot} — Quốc Hùng Mobile',
    })
