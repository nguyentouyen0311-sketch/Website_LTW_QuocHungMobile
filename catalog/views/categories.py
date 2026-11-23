"""Views for category administration."""

from django.contrib import messages
from django.db.models import Count, Q
from django.http import HttpResponseBadRequest, HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from accounts.permissions import is_sales_staff
from catalog.forms import CategoryForm
from catalog.models import Category


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
