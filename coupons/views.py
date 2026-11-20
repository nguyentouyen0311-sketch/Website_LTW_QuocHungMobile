from decimal import Decimal
from django.contrib import messages
from django.core.exceptions import ValidationError
from django.core.paginator import Paginator
from django.db import IntegrityError, OperationalError
from django.db.models import F, Q, Sum
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.http import require_http_methods

from accounts.permissions import ROLE_ADMIN, role_required
from coupons.forms import CouponForm
from coupons.models import Coupon, DiscountType


def _build_workspace_context(request, editing_coupon=None, form=None, is_edit=False):
    now = timezone.now()

    q_search = request.GET.get('q', '').strip()
    f_type = request.GET.get('type', 'all').strip()
    f_status = request.GET.get('status', 'all').strip().lower()
    f_scope = request.GET.get('scope', 'all').strip()

    qs = Coupon.objects.all().prefetch_related('coupon_products__product')

    if q_search:
        qs = qs.filter(code__icontains=q_search)

    if f_type in (DiscountType.PERCENT, DiscountType.FIXED):
        qs = qs.filter(discount_type=f_type)

    if f_scope == 'all_products':
        qs = qs.filter(applies_to_all=True)
    elif f_scope == 'specific_products':
        qs = qs.filter(applies_to_all=False)

    if f_status == 'active':
        qs = qs.filter(
            is_active=True,
            starts_at__lte=now,
            ends_at__gt=now,
            used_count__lt=F('max_uses') - F('reserved_count')
        )
    elif f_status == 'not_started':
        qs = qs.filter(is_active=True, starts_at__gt=now)
    elif f_status == 'expired':
        qs = qs.filter(is_active=True, ends_at__lte=now)
    elif f_status == 'exhausted':
        qs = qs.filter(
            is_active=True,
            starts_at__lte=now,
            ends_at__gt=now,
            used_count__gte=F('max_uses') - F('reserved_count')
        )
    elif f_status == 'inactive':
        qs = qs.filter(is_active=False)

    all_coupons = Coupon.objects.all()
    total_coupons = all_coupons.count()
    active_count = all_coupons.filter(
        is_active=True,
        starts_at__lte=now,
        ends_at__gt=now,
        used_count__lt=F('max_uses') - F('reserved_count')
    ).count()

    total_used = all_coupons.aggregate(total=Sum('used_count'))['total'] or 0
    total_reserved = all_coupons.aggregate(total=Sum('reserved_count'))['total'] or 0

    total_filtered = qs.count()
    paginator = Paginator(qs, 8)
    page_number = request.GET.get('page', 1)
    page_obj = paginator.get_page(page_number)
    elided_pages = list(paginator.get_elided_page_range(page_obj.number, on_each_side=1, on_ends=1))

    query_params = request.GET.copy()
    if 'page' in query_params:
        del query_params['page']
    pagination_query = query_params.urlencode()

    if form is None:
        if is_edit and editing_coupon:
            form = CouponForm(instance=editing_coupon)
        else:
            form = CouponForm()

    return {
        'coupons': page_obj,
        'page_obj': page_obj,
        'elided_pages': elided_pages,
        'total_filtered': total_filtered,
        'metrics': {
            'total_coupons': total_coupons,
            'active_count': active_count,
            'total_used': total_used,
            'total_reserved': total_reserved,
        },
        'filters': {
            'q': q_search,
            'type': f_type,
            'status': request.GET.get('status', 'all').strip(),
            'scope': f_scope,
        },
        'pagination_query': pagination_query,
        'active_nav': 'coupons',
        'module_badge': 'QUẢN LÝ VOUCHER',
        'is_edit': is_edit,
        'editing_coupon': editing_coupon,
        'form': form,
    }


@role_required(ROLE_ADMIN)
@require_http_methods(["GET", "POST"])
def coupon_list_view(request):
    if request.method == 'POST':
        return coupon_create_view(request)

    context = _build_workspace_context(request, is_edit=False)
    return render(request, 'coupons/voucher_workspace.html', context)


@role_required(ROLE_ADMIN)
@require_http_methods(["GET", "POST"])
def coupon_create_view(request):
    if request.method == 'POST':
        form = CouponForm(request.POST)
        if form.is_valid():
            try:
                coupon = form.save()
                messages.success(request, f"Đã khởi tạo thành công chương trình voucher '{coupon.code}'.")
                return redirect('coupons:coupon_list')
            except (ValidationError, IntegrityError, OperationalError) as e:
                err_msg = str(e).lower()
                if 'locked' in err_msg or 'busy' in err_msg:
                    form.add_error(None, "Hệ thống đang bận do có thao tác ghi đồng thời hoặc dữ liệu đang được cập nhật. Vui lòng thử lại sau giây lát.")
                elif 'code' in err_msg or 'unique' in err_msg:
                    code = form.cleaned_data.get('code', '')
                    form.add_error('code', f"Mã voucher '{code}' đã tồn tại hoặc xảy ra xung đột dữ liệu.")
                else:
                    form.add_error(None, str(e))
                messages.error(request, "Vui lòng kiểm tra và sửa các lỗi bên dưới trước khi lưu.")
        else:
            messages.error(request, "Vui lòng kiểm tra và sửa các lỗi bên dưới trước khi lưu.")

        context = _build_workspace_context(request, form=form, is_edit=False)
        return render(request, 'coupons/voucher_workspace.html', context)
    else:
        context = _build_workspace_context(request, is_edit=False)
        return render(request, 'coupons/voucher_workspace.html', context)


@role_required(ROLE_ADMIN)
@require_http_methods(["GET", "POST"])
def coupon_edit_view(request, pk):
    coupon = get_object_or_404(Coupon, pk=pk)

    if request.method == 'POST':
        form = CouponForm(request.POST, instance=coupon)
        if form.is_valid():
            try:
                coupon = form.save()
                messages.success(request, f"Đã cập nhật thành công voucher '{coupon.code}'.")
                return redirect('coupons:coupon_list')
            except (ValidationError, IntegrityError, OperationalError) as e:
                err_msg = str(e).lower()
                if 'locked' in err_msg or 'busy' in err_msg:
                    form.add_error(None, "Hệ thống đang bận do có thao tác ghi đồng thời hoặc dữ liệu đang được cập nhật. Vui lòng thử lại sau giây lát.")
                elif 'max_uses' in err_msg or 'lượt' in err_msg or 'quota' in err_msg:
                    form.add_error('max_uses', str(e))
                elif 'code' in err_msg or 'unique' in err_msg:
                    form.add_error('code', f"Mã voucher '{form.cleaned_data.get('code')}' đã tồn tại trong hệ thống.")
                else:
                    form.add_error(None, str(e))
                messages.error(request, "Vui lòng kiểm tra và sửa các lỗi bên dưới trước khi lưu.")
        else:
            messages.error(request, "Vui lòng kiểm tra và sửa các lỗi bên dưới trước khi lưu.")

        context = _build_workspace_context(request, editing_coupon=coupon, form=form, is_edit=True)
        return render(request, 'coupons/voucher_workspace.html', context)
    else:
        form = CouponForm(instance=coupon)
        context = _build_workspace_context(request, editing_coupon=coupon, form=form, is_edit=True)
        return render(request, 'coupons/voucher_workspace.html', context)


