"""
Views cho phân hệ Quản lý Kho hàng (Inventory Views) — Quốc Hùng Mobile.
Tuân thủ đặc tả SRS v2.3 Mục XI, XIV Bảng 24 và UI-29, UI-30 (phần chỉ số tồn hiện tại).
"""
import uuid
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.db.models import F, Q, Sum
from django.http import HttpResponseForbidden, HttpResponseNotAllowed
from django.shortcuts import render, redirect, get_object_or_404
from django.urls import reverse

from accounts.permissions import is_inventory_staff, is_admin, is_sales_staff
from inventory.models import Inventory, InventoryTransaction, InventoryAdjustment
from inventory.forms import (
    InventoryThresholdForm,
    PurchaseReceivingForm,
    StocktakeAdjustmentForm,
)
from inventory.services import (
    generate_receiving_token,
    receive_purchase,
    generate_adjustment_token,
    adjust_inventory,
    confirm_return_receipt,
)



def parse_positive_id(val, max_digits=12):
    """
    Chuyển đổi an toàn chuỗi đầu vào thành số nguyên dương (ID) (QA-T051-02).
    - Chỉ chấp nhận chuỗi chứa ký tự chữ số ASCII thuần (0-9).
    - Từ chối các ký tự số Unicode (như '²', '³', '½') vốn thỏa .isdigit() nhưng ném ValueError khi gọi int().
    - Giới hạn độ dài chuỗi tối đa (mặc định 12 ký tự) để chống DoS và tràn số.
    - Bắt cụ thể ValueError và trả về None nếu không hợp lệ hoặc int <= 0.
    """
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

def inventory_list_view(request):
    """
    Giao diện Quản lý Danh sách tồn kho và Cảnh báo (UI-29).
    Quyền truy cập: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    - Khách ẩn danh: Chuyển hướng đến trang đăng nhập kèm ?next=.
    - Người dùng không có vai trò kho: Từ chối với HTTP 403 Forbidden.
    - Hiển thị danh sách tồn kho gắn với từng biến thể (bao gồm cả biến thể của sản phẩm
      đang ẩn, xóa mềm hoặc ngừng kích hoạt).
    - Tìm kiếm theo mã SKU, tên sản phẩm, mã sản phẩm (SP0001).
    - Bộ lọc cảnh báo tồn kho (Tất cả, Cảnh báo thiếu hàng available <= min_threshold,
      Hết hàng available <= 0, Đủ hàng available > min_threshold).
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    alert_filter = request.GET.get('alert', '').strip().lower()

    # Base Queryset được chú thích trường available_count
    base_qs = (
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        )
        .prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        )
        .annotate(available_count=F('physical_sellable') - F('reserved'))
    )

    # Thống kê nhanh tổng thể 5 KPI cards (không phụ thuộc vào bộ lọc tìm kiếm)
    total_variants_count = base_qs.count()
    aggregates = base_qs.aggregate(
        total_physical=Sum('physical_sellable'),
        total_reserved=Sum('reserved'),
    )
    total_physical_count = aggregates['total_physical'] or 0
    total_reserved_count = aggregates['total_reserved'] or 0
    total_available_count = max(0, total_physical_count - total_reserved_count)
    total_low_stock_count = base_qs.filter(available_count__lte=F('min_threshold')).count()
    total_out_of_stock_count = base_qs.filter(available_count__lte=0).count()
    total_holding_count = base_qs.filter(reserved__gt=0).count()

    queryset = base_qs

    # 1. Tìm kiếm theo từ khóa
    if q:
        search_filter = (
            Q(variant__sku__icontains=q)
            | Q(variant__product__name__icontains=q)
            | Q(variant__attribute_values__attribute_value__value__icontains=q)
        )
        clean_code = q.upper().strip()
        if clean_code.startswith('TK'):
            tk_id = parse_positive_id(clean_code[2:])
            if tk_id is not None:
                search_filter |= Q(id=tk_id)
        elif clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(id=pid) | Q(variant__product__id=pid)

        queryset = queryset.filter(search_filter).distinct()

    # 2. Bộ lọc cảnh báo tồn kho
    if alert_filter in ['low_stock', 'need_restock']:
        queryset = queryset.filter(available_count__lte=F('min_threshold'))
    elif alert_filter == 'out_of_stock':
        queryset = queryset.filter(available_count__lte=0)
    elif alert_filter == 'in_stock':
        queryset = queryset.filter(available_count__gt=F('min_threshold'))
    elif alert_filter in ['holding', 'reserved']:
        queryset = queryset.filter(reserved__gt=0)
    else:
        alert_filter = 'all'

    # Sắp xếp mặc định
    queryset = queryset.order_by('variant__product__name', 'variant__id')

    # Phân trang an toàn (15 dòng / trang để vừa vặn split-view)
    paginator = Paginator(queryset, 15)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    # Sinh signed tokens cho từng dòng tồn kho trên trang hiện hành
    selected_id = parse_positive_id(request.GET.get('selected', '')) or parse_positive_id(request.GET.get('inventory_id', ''))
    selected_inventory = None

    for inv in page_obj.object_list:
        try:
            inv.recv_token, _ = generate_receiving_token(inventory_id=inv.id, actor_id=request.user.id)
            inv.adj_token, _ = generate_adjustment_token(inventory=inv, actor_id=request.user.id)
        except Exception:
            inv.recv_token = ''
            inv.adj_token = ''
        if selected_id and inv.id == selected_id:
            selected_inventory = inv

    # Nếu người dùng chọn SKU ở trang khác hoặc chưa chọn
    if selected_id and not selected_inventory:
        selected_inventory = base_qs.filter(id=selected_id).first()
        if selected_inventory:
            try:
                selected_inventory.recv_token, _ = generate_receiving_token(inventory_id=selected_inventory.id, actor_id=request.user.id)
                selected_inventory.adj_token, _ = generate_adjustment_token(inventory=selected_inventory, actor_id=request.user.id)
            except Exception:
                selected_inventory.recv_token = ''
                selected_inventory.adj_token = ''

    if not selected_inventory and page_obj.object_list:
        selected_inventory = page_obj.object_list[0]

    receive_form = PurchaseReceivingForm(initial={
        'request_token': getattr(selected_inventory, 'recv_token', '') if selected_inventory else ''
    })
    adjust_form = StocktakeAdjustmentForm(initial={
        'counted_quantity': selected_inventory.physical_sellable if selected_inventory else 0,
        'request_token': getattr(selected_inventory, 'adj_token', '') if selected_inventory else ''
    })

    context = {
        'page_obj': page_obj,
        'inventory_list': page_obj.object_list,
        'selected_inventory': selected_inventory,
        'receive_form': receive_form,
        'adjust_form': adjust_form,
        'q': q,
        'alert_filter': alert_filter,
        'total_variants_count': total_variants_count,
        'total_physical_count': total_physical_count,
        'total_reserved_count': total_reserved_count,
        'total_available_count': total_available_count,
        'total_low_stock_count': total_low_stock_count,
        'total_out_of_stock_count': total_out_of_stock_count,
        'total_holding_count': total_holding_count,
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_list.html', context)


def _build_inventory_detail_context(request, inventory, form=None):
    """
    Hàm trợ giúp xây dựng ngữ cảnh dùng chung cho trang chi tiết tồn kho (UI-30).
    Đảm bảo tính nhất quán giữa GET thông thường và các nhánh render lại khi form lỗi (HTTP 200),
    giữ đầy đủ lịch sử biến động kho, phân trang, đếm tổng số và form đã liên kết (bound).
    """
    if form is None:
        form = InventoryThresholdForm(instance=inventory)

    transactions_qs = (
        inventory.transactions
        .select_related('performed_by', 'adjustment')
        .order_by('-created_at', '-id')
    )
    total_tx_count = transactions_qs.count()
    tx_paginator = Paginator(transactions_qs, 10)
    tx_page_number = request.GET.get('tx_page') or request.POST.get('tx_page', 1)
    tx_page_obj = tx_paginator.get_page(tx_page_number)

    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'tx_page_obj': tx_page_obj,
        'total_tx_count': total_tx_count,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }


def inventory_detail_view(request, inventory_id):
    """
    View chi tiết tồn kho cho một SKU (UI-30).
    Quyền truy cập: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    Hiển thị thông tin sản phẩm, biến thể, 3 chỉ số tồn kho, ngưỡng cảnh báo,
    form cập nhật ngưỡng và bảng lịch sử biến động kho (InventoryTransaction) phân trang.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    context = _build_inventory_detail_context(request, inventory)
    return render(request, 'inventory/inventory_detail.html', context)


def inventory_update_threshold_view(request, inventory_id):
    """
    View xử lý đột biến ngưỡng cảnh báo tồn kho (Threshold Mutation).
    Quy tắc an toàn nghiêm ngặt (Task 05.1 và QA-T052-01):
    1. Chỉ chấp nhận phương thức POST có CSRF. Từ chối GET bằng 405 Method Not Allowed.
    2. Xác thực và phân quyền máy chủ: Chỉ Nhân viên kho và Admin.
    3. Whitelist biểu mẫu: Chỉ cập nhật duy nhất min_threshold.
    4. Giữ nguyên 100% physical_sellable và reserved.
    5. Xử lý lỗi thẩm định (số âm, chữ, số thực, vượt giới hạn) trả về HTTP 200 kèm
       thông báo lỗi biểu mẫu rõ ràng, không làm gián đoạn hay ném lỗi máy chủ 500.
       Đồng thời bảo toàn nguyên vẹn danh sách và số lượng lịch sử biến động kho hiển thị.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    old_threshold = inventory.min_threshold
    form = InventoryThresholdForm(request.POST, instance=inventory)
    if form.is_valid():
        updated_inv = form.save()
        messages.success(
            request,
            f"Đã cập nhật ngưỡng cảnh báo cho SKU '{inventory.variant.sku}' "
            f"từ {old_threshold} thành {updated_inv.min_threshold} thành công.",
        )
        return redirect('inventory:inventory_detail', inventory_id=inventory.id)

    # Nếu dữ liệu không hợp lệ, render lại trang chi tiết kèm lỗi biểu mẫu (HTTP 200) và đầy đủ lịch sử giao dịch
    context = _build_inventory_detail_context(request, inventory, form=form)
    return render(request, 'inventory/inventory_detail.html', context, status=200)


def _build_inventory_form_context(inventory, form, request_token, user):
    """
    Xây dựng context chuẩn dùng chung cho giao diện nhập hàng và kiểm kê kho (UI-31, UI-32).
    Bảo toàn toàn bộ thông tin đối tượng, biểu mẫu, token ký và cờ phân quyền người dùng.
    """
    return {
        'inventory': inventory,
        'variant': inventory.variant,
        'product': inventory.variant.product if inventory.variant else None,
        'form': form,
        'request_token': request_token,
        'active_nav': 'inventory_list',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(user),
        'is_sales_staff': is_sales_staff(user),
        'is_inventory_staff': is_inventory_staff(user),
    }


def _add_validation_error_to_form(form, exc):
    """
    Chuyển tiếp lỗi ValidationError từ service layer vào form fields hoặc non-field errors.
    Bảo toàn cấu trúc message_dict và danh sách lỗi exc.messages.
    """
    if hasattr(exc, 'message_dict'):
        for field, errs in exc.message_dict.items():
            for err in errs:
                if field in form.fields:
                    form.add_error(field, err)
                else:
                    form.add_error(None, err)
    else:
        for err in exc.messages:
            form.add_error(None, err)


def inventory_receive_view(request, inventory_id):
    """
    Giao diện và Xử lý Nhập hàng vào kho (UI-31) — Task 05.2.
    Quyền truy cập: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    - GET: Hiển thị form nhập hàng với ngữ cảnh SKU, tồn hiện tại, và mã request_token đã ký.
      Không tạo bản ghi hay ghi dữ liệu CSDL trên GET.
    - POST: Xác thực token ký, thẩm định số lượng > 0, gọi receive_purchase thực thi nguyên tử,
      idempotent và áp dụng mô hình PRG (Post-Redirect-Get).
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    if request.method == 'GET':
        token, _ = generate_receiving_token(inventory_id=inventory.id, actor_id=request.user.id)
        form = PurchaseReceivingForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_receive.html', context)

    elif request.method == 'POST':
        form = PurchaseReceivingForm(request.POST)
        if form.is_valid():
            quantity = form.cleaned_data['quantity']
            token = form.cleaned_data['request_token']
            try:
                tx, is_replay = receive_purchase(
                    inventory_id=inventory.id,
                    quantity=quantity,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu nhập hàng trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Số lượng: {quantity}, Tồn thực tế: {tx.physical_after}, Mã yêu cầu: {tx.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã nhập thành công {quantity} sản phẩm cho biến thể '{inventory.variant.sku}'. "
                        f"Tồn thực tế mới: {tx.physical_after} (Khả dụng: {tx.physical_after - inventory.reserved})."
                    )
                next_url = request.POST.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        next_url = request.POST.get('next')
        if next_url:
            err_msg = "; ".join([f"{', '.join(errs)}" for _, errs in form.errors.items()])
            messages.error(request, f"Lỗi nhập kho: {err_msg}")
            sep = '&' if '?' in next_url else '?'
            return redirect(f"{next_url}{sep}selected={inventory.id}")

        # Nếu form không hợp lệ hoặc validation error, render lại form với HTTP 200
        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_receive.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])


def inventory_transaction_list_view(request):
    """
    Giao diện Sổ cái lịch sử biến động kho toàn hệ thống (UI-30 / Global Ledger).
    Quyền truy cập: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    - Xem toàn bộ giao dịch biến động kho theo thứ tự giảm dần thời gian tạo.
    - Tìm kiếm theo mã SKU, tên sản phẩm, mã SP hoặc mã UUID request_key.
    - Bộ lọc theo loại giao dịch (PURCHASE_IN hoặc tất cả).
    - Phân trang an toàn 25 dòng / trang.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    q = request.GET.get('q', '').strip()
    tx_type = request.GET.get('type', '').strip().upper()

    base_qs = (
        InventoryTransaction.objects
        .select_related(
            'inventory',
            'inventory__variant',
            'inventory__variant__product',
            'performed_by',
            'adjustment',
            'order',
            'return_request',
        )
        .prefetch_related(
            'inventory__variant__attribute_values__attribute_value',
        )
        .order_by('-created_at', '-id')
    )

    total_tx_count = base_qs.count()
    count_purchase = base_qs.filter(transaction_type=InventoryTransaction.TYPE_PURCHASE_IN).count()
    count_ship = base_qs.filter(transaction_type=InventoryTransaction.TYPE_SHIP).count()
    count_adjust = base_qs.filter(transaction_type=InventoryTransaction.TYPE_ADJUSTMENT).count()
    count_return = base_qs.filter(transaction_type=InventoryTransaction.TYPE_RETURN_RESTOCK).count()

    queryset = base_qs

    if q:
        search_filter = (
            Q(inventory__variant__sku__icontains=q)
            | Q(inventory__variant__product__name__icontains=q)
            | Q(performed_by__username__icontains=q)
            | Q(adjustment__reason__icontains=q)
        )
        clean_code = q.upper().strip()
        if clean_code.startswith('TK'):
            tk_id = parse_positive_id(clean_code[2:])
            if tk_id is not None:
                search_filter |= Q(inventory_id=tk_id)
        elif clean_code.startswith('SP'):
            pid = parse_positive_id(clean_code[2:])
            if pid is not None:
                search_filter |= Q(inventory__variant__product__id=pid)
        else:
            pid = parse_positive_id(q)
            if pid is not None:
                search_filter |= Q(inventory_id=pid) | Q(inventory__variant__product__id=pid) | Q(order_id=pid) | Q(return_request_id=pid)

        # Hỗ trợ tìm kiếm theo UUID request_key
        try:
            val_uuid = uuid.UUID(q)
            search_filter |= Q(request_key=val_uuid)
        except (ValueError, TypeError):
            pass

        queryset = queryset.filter(search_filter).distinct()

    valid_types = {
        'PURCHASE_IN': InventoryTransaction.TYPE_PURCHASE_IN,
        'SHIP': InventoryTransaction.TYPE_SHIP,
        'ADJUSTMENT': InventoryTransaction.TYPE_ADJUSTMENT,
        'RETURN_RESTOCK': InventoryTransaction.TYPE_RETURN_RESTOCK,
    }

    if tx_type in valid_types:
        queryset = queryset.filter(transaction_type=valid_types[tx_type])
    else:
        tx_type = 'ALL'

    paginator = Paginator(queryset, 25)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    context = {
        'page_obj': page_obj,
        'transaction_list': page_obj.object_list,
        'q': q,
        'tx_type': tx_type,
        'total_tx_count': total_tx_count,
        'count_purchase': count_purchase,
        'count_ship': count_ship,
        'count_adjust': count_adjust,
        'count_return': count_return,
        'filtered_count': queryset.count(),
        'active_nav': 'inventory_history',
        'module_badge': 'QUẢN LÝ KHO HÀNG',
        'is_admin': is_admin(request.user),
        'is_sales_staff': is_sales_staff(request.user),
        'is_inventory_staff': is_inventory_staff(request.user),
    }
    return render(request, 'inventory/inventory_history.html', context)



def inventory_adjust_view(request, inventory_id):
    """
    Giao diện và Xử lý Kiểm kê / Điều chỉnh tồn kho (UI-32) — Task 05.3.
    Quyền truy cập: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    - GET: Hiển thị form kiểm kê với ngữ cảnh SKU, các chỉ số tồn hiện tại, và mã request_token đã ký
      chứa snapshot phiên bản tồn kho. Không tạo bản ghi hay ghi dữ liệu CSDL trên GET.
    - POST: Xác thực token ký, đối chiếu snapshot tồn kho, thẩm định số lượng kiểm kê (>= 0),
      lý do điều chỉnh (bắt buộc, không để trống), gọi adjust_inventory thực thi nguyên tử,
      idempotent và áp dụng mô hình PRG (Post-Redirect-Get).
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này.")

    inventory = get_object_or_404(
        Inventory.objects.select_related(
            'variant',
            'variant__product',
            'variant__product__category',
        ).prefetch_related(
            'variant__attribute_values__attribute',
            'variant__attribute_values__attribute_value',
        ),
        pk=inventory_id,
    )

    if request.method == 'GET':
        token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
        form = StocktakeAdjustmentForm(initial={'request_token': token})
        context = _build_inventory_form_context(inventory, form, token, request.user)
        return render(request, 'inventory/inventory_adjust.html', context)

    elif request.method == 'POST':
        form = StocktakeAdjustmentForm(request.POST)
        if form.is_valid():
            counted_quantity = form.cleaned_data['counted_quantity']
            reason = form.cleaned_data['reason']
            token = form.cleaned_data['request_token']
            try:
                adj, is_replay = adjust_inventory(
                    inventory_id=inventory.id,
                    counted_quantity=counted_quantity,
                    reason=reason,
                    request_token=token,
                    actor=request.user,
                )
                if is_replay:
                    messages.info(
                        request,
                        f"Yêu cầu điều chỉnh kiểm kê trước đó cho SKU '{inventory.variant.sku}' đã được ghi nhận "
                        f"(Tồn mới: {adj.new_quantity}, Tồn cũ: {adj.old_quantity}, Chênh lệch: {adj.quantity_delta:+d}, Mã giao dịch: {adj.transaction.request_key})."
                    )
                else:
                    messages.success(
                        request,
                        f"Đã điều chỉnh tồn kho cho biến thể '{inventory.variant.sku}': từ {adj.old_quantity} thành {adj.new_quantity} "
                        f"(Chênh lệch: {adj.quantity_delta:+d}, Khả dụng mới: {adj.new_quantity - inventory.reserved}). Lý do: {adj.reason}."
                    )
                next_url = request.POST.get('next')
                if next_url:
                    return redirect(next_url)
                return redirect('inventory:inventory_detail', inventory_id=inventory.id)
            except ValidationError as exc:
                # Kiểm tra an toàn exc.code qua getattr (tránh AttributeError khi exc chứa message_dict - QA-T053-02)
                exc_code = getattr(exc, 'code', None)
                if exc_code == 'stale_snapshot':
                    inventory.refresh_from_db()
                    fresh_token, _ = generate_adjustment_token(inventory=inventory, actor_id=request.user.id)
                    form.data = form.data.copy()
                    form.data['request_token'] = fresh_token

                _add_validation_error_to_form(form, exc)
            except PermissionDenied as exc:
                return HttpResponseForbidden(str(exc))

        next_url = request.POST.get('next')
        if next_url:
            err_msg = "; ".join([f"{', '.join(errs)}" for _, errs in form.errors.items()])
            messages.error(request, f"Lỗi điều chỉnh kiểm kê: {err_msg}")
            sep = '&' if '?' in next_url else '?'
            return redirect(f"{next_url}{sep}selected={inventory.id}")

        # Nếu form không hợp lệ hoặc validation error, render lại form với HTTP 200
        inventory.refresh_from_db()
        context = _build_inventory_form_context(
            inventory, form, form.data.get('request_token', ''), request.user
        )
        return render(request, 'inventory/inventory_adjust.html', context, status=200)

    else:
        return HttpResponseNotAllowed(['GET', 'POST'])


def returned_stock_list_view(request):
    """
    Giao diện hoàn tất đổi/trả dành cho Nhân viên kho & Admin.
    - Phân quyền: Nhân viên kho (Inventory Staff) và Quản trị viên (Admin).
    - Khách hàng và Sales Staff (không có quyền kho): Bị chặn 403 Forbidden.
    - Các yêu cầu đã duyệt được xác nhận hoàn tất trực tiếp.
    - Hàng nhận về không được kiểm định, không tạo lô hàng trả và không được nhập lại kho bán.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền truy cập chức năng này. Chỉ Nhân viên kho hoặc Quản trị viên mới được phép quản lý hàng đổi trả.")

    from orders.models import ReturnRequest, ReturnRequestStatus

    pending_receipt_requests = (
        ReturnRequest.objects
        .filter(status=ReturnRequestStatus.APPROVED)
        .select_related('order', 'order_item', 'customer', 'replacement_variant')
        .order_by('-updated_at')
    )

    completed_requests = (
        ReturnRequest.objects.filter(status=ReturnRequestStatus.COMPLETED)
        .select_related('order', 'order_item', 'customer', 'replacement_variant')
        .order_by('-updated_at')[:20]
    )

    context = {
        'pending_receipt_requests': pending_receipt_requests,
        'completed_requests': completed_requests,
        'active_nav': 'returned_stock',
        'is_inventory_staff': True,
        'title': "Hoàn tất đổi/trả — Quốc Hùng Mobile",
    }
    return render(request, 'inventory/returned_stock_list.html', context)


def confirm_receipt_action_view(request, pk):
    """
    Xác nhận hoàn tất đổi/trả. Hàng nhận về không được đưa vào kiểm định hoặc tồn kho bán.
    """
    if not request.user.is_authenticated:
        return redirect(f"{reverse('accounts:login')}?next={request.path}")

    if not is_inventory_staff(request.user):
        return HttpResponseForbidden("Bạn không có quyền tiếp nhận hàng đổi trả.")

    if request.method != 'POST':
        return HttpResponseNotAllowed(['POST'])

    try:
        confirm_return_receipt(return_request_id=pk, actor=request.user)
        messages.success(request, f"Đã hoàn tất yêu cầu đổi/trả #{pk}. Sản phẩm nhận về không được nhập lại vào kho bán.")
    except (ValidationError, PermissionDenied) as e:
        messages.error(request, str(e.message if hasattr(e, 'message') else e))

    return redirect('inventory:returned_stock_list')
