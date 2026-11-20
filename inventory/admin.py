"""
Cấu hình Quản trị Django Admin cho phân hệ Tồn kho (Inventory) — Quốc Hùng Mobile.
Thực thi các ràng buộc an toàn nghiêm ngặt theo yêu cầu Task 05.1:
- Bắt buộc kiểm tra vai trò nghiệp vụ (is_inventory_staff) kết hợp is_staff.
- Khóa hoàn toàn thao tác thêm mới (has_add_permission = False); tồn kho sinh tự động theo variant.
- Khóa hoàn toàn thao tác xóa đơn lẻ và xóa hàng loạt (has_delete_permission = False, bỏ delete_selected).
- Khóa trường variant (chống reparenting), khóa physical_sellable và reserved (chỉ đọc).
- Chỉ duy nhất min_threshold được phép chỉnh sửa.
"""
from django.contrib import admin
from accounts.permissions import is_inventory_staff
from inventory.models import Inventory, InventoryTransaction, InventoryAdjustment



def parse_positive_id(val, max_digits=12):
    """
    Chuyển đổi an toàn chuỗi đầu vào thành số nguyên dương (ID).
    Chỉ chấp nhận chữ số ASCII thuần (0-9), chống tràn số và ký tự số Unicode.
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

@admin.register(Inventory)
class InventoryAdmin(admin.ModelAdmin):
    list_display = (
        'id',
        'variant_sku',
        'product_name',
        'physical_sellable',
        'reserved',
        'available_display',
        'min_threshold',
        'stock_status_display',
        'updated_at',
    )
    list_filter = (
        'variant__is_active',
        'variant__product__category',
        'variant__product__is_active',
    )
    search_fields = (
        'variant__sku',
        'variant__product__name',
    )
    readonly_fields = (
        'variant',
        'physical_sellable',
        'reserved',
        'available_display',
        'updated_at',
    )
    fields = (
        'variant',
        'physical_sellable',
        'reserved',
        'available_display',
        'min_threshold',
        'updated_at',
    )
    ordering = ('id',)

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.variant.sku if obj.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        return obj.variant.product.name if obj.variant and obj.variant.product else '-'

    @admin.display(description='Có thể bán (Available)')
    def available_display(self, obj):
        return obj.available

    @admin.display(description='Trạng thái kho')
    def stock_status_display(self, obj):
        return obj.stock_status_label

    def has_add_permission(self, request):
        # Chặn tạo thủ công từ Admin; Inventory luôn được tạo tự động gắn với ProductVariant
        return False

    def has_delete_permission(self, request, obj=None):
        # Chặn xóa cứng bản ghi tồn kho qua Django Admin
        return False

    def has_change_permission(self, request, obj=None):
        # Chỉ nhân viên kho hoặc Admin có cờ is_staff mới được phép chỉnh sửa ngưỡng
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def has_view_permission(self, request, obj=None):
        # Chỉ nhân viên kho hoặc Admin có cờ is_staff mới được phép xem danh sách/chi tiết
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions

    def get_search_results(self, request, queryset, search_term):
        base_queryset = queryset
        queryset, may_have_duplicates = super().get_search_results(request, queryset, search_term)
        term = (search_term or '').strip()
        if term:
            clean_code = term.upper()
            product_id = None
            if clean_code.startswith('SP'):
                product_id = parse_positive_id(clean_code[2:])
            else:
                product_id = parse_positive_id(term)
            if product_id is not None:
                queryset = queryset | base_queryset.filter(variant__product__id=product_id)
                may_have_duplicates = True
        return queryset, may_have_duplicates

    def save_model(self, request, obj, form, change):
        if change and obj.pk:
            # Phòng vệ đa tầng: Khóa cứng variant, physical_sellable và reserved ở tầng Admin save
            orig = Inventory.objects.get(pk=obj.pk)
            obj.variant = orig.variant
            obj.physical_sellable = orig.physical_sellable
            obj.reserved = orig.reserved
        super().save_model(request, obj, form, change)



@admin.register(InventoryTransaction)
class InventoryTransactionAdmin(admin.ModelAdmin):
    """
    Cấu hình Quản trị Django Admin cho Sổ cái biến động kho (InventoryTransaction).
    Thực thi quy tắc bảo mật nghiêm ngặt (Task 05.2):
    - Chỉ đọc tuyệt đối: has_add_permission = False, has_change_permission = False, has_delete_permission = False.
    - Loại bỏ action xóa hàng loạt delete_selected.
    - Tất cả các trường là readonly_fields.
    - Kiểm tra quyền: is_inventory_staff và is_staff.
    """
    list_display = (
        'id',
        'created_at',
        'variant_sku',
        'product_name',
        'transaction_type_badge',
        'quantity_delta_display',
        'physical_before',
        'physical_after',
        'performed_by_display',
        'request_key_short',
    )
    list_filter = (
        'transaction_type',
        'created_at',
    )
    search_fields = (
        'inventory__variant__sku',
        'inventory__variant__product__name',
        'performed_by__username',
        'request_key',
    )
    readonly_fields = (
        'inventory',
        'transaction_type',
        'quantity_delta',
        'physical_before',
        'physical_after',
        'performed_by',
        'created_at',
        'request_key',
    )
    fields = (
        'inventory',
        'transaction_type',
        'quantity_delta',
        'physical_before',
        'physical_after',
        'performed_by',
        'created_at',
        'request_key',
    )
    ordering = ('-created_at', '-id')

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.inventory.variant.sku if obj.inventory and obj.inventory.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        if obj.inventory and obj.inventory.variant and obj.inventory.variant.product:
            return obj.inventory.variant.product.name
        return '-'

    @admin.display(description='Loại giao dịch')
    def transaction_type_badge(self, obj):
        return obj.transaction_type_display

    @admin.display(description='Biến động')
    def quantity_delta_display(self, obj):
        return f"+{obj.quantity_delta}" if obj.quantity_delta > 0 else str(obj.quantity_delta)

    @admin.display(description='Người thực hiện')
    def performed_by_display(self, obj):
        return obj.performed_by.username if obj.performed_by else 'Hệ thống'

    @admin.display(description='Mã yêu cầu (UUID)')
    def request_key_short(self, obj):
        return str(obj.request_key)

    def has_add_permission(self, request):
        # Không cho phép tạo thủ công qua Django Admin; chỉ ghi qua service nhập hàng
        return False

    def has_change_permission(self, request, obj=None):
        # Sổ cái biến động kho là append-only, không thể sửa đổi
        return False

    def has_delete_permission(self, request, obj=None):
        # Sổ cái biến động kho là append-only, không thể xóa bỏ
        return False

    def has_view_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions



@admin.register(InventoryAdjustment)
class InventoryAdjustmentAdmin(admin.ModelAdmin):
    """
    Giao diện Django Admin cho Bảng 27: InventoryAdjustment (Chỉ đọc).
    - Toàn bộ dữ liệu kiểm kê là audit trail bất biến.
    - has_add_permission = False (chỉ ghi qua service kiểm kê).
    - has_change_permission = False (không cho phép sửa).
    - has_delete_permission = False (không cho phép xóa).
    - Bỏ action delete_selected.
    - Quyền truy cập: is_inventory_staff và is_staff.
    """
    list_display = (
        'id',
        'created_at',
        'variant_sku',
        'product_name',
        'old_quantity',
        'new_quantity',
        'delta_display',
        'reason_short',
        'adjusted_by_display',
        'transaction_key',
    )
    list_filter = (
        'created_at',
    )
    search_fields = (
        'inventory__variant__sku',
        'inventory__variant__product__name',
        'adjusted_by__username',
        'reason',
        'transaction__request_key',
    )
    readonly_fields = (
        'inventory',
        'old_quantity',
        'new_quantity',
        'reason',
        'adjusted_by',
        'transaction',
        'created_at',
    )
    fields = (
        'inventory',
        'old_quantity',
        'new_quantity',
        'reason',
        'adjusted_by',
        'transaction',
        'created_at',
    )
    ordering = ('-created_at', '-id')

    @admin.display(description='Mã SKU')
    def variant_sku(self, obj):
        return obj.inventory.variant.sku if obj.inventory and obj.inventory.variant else '-'

    @admin.display(description='Sản phẩm')
    def product_name(self, obj):
        if obj.inventory and obj.inventory.variant and obj.inventory.variant.product:
            return obj.inventory.variant.product.name
        return '-'

    @admin.display(description='Biến động')
    def delta_display(self, obj):
        delta = obj.quantity_delta
        return f"+{delta}" if delta > 0 else str(delta)

    @admin.display(description='Lý do')
    def reason_short(self, obj):
        if len(obj.reason) > 50:
            return obj.reason[:47] + '...'
        return obj.reason

    @admin.display(description='Người điều chỉnh')
    def adjusted_by_display(self, obj):
        return obj.adjusted_by.username if obj.adjusted_by else '-'

    @admin.display(description='Mã giao dịch')
    def transaction_key(self, obj):
        return str(obj.transaction.request_key) if obj.transaction else '-'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

    def has_view_permission(self, request, obj=None):
        if not (request.user and request.user.is_authenticated and request.user.is_staff):
            return False
        return is_inventory_staff(request.user)

    def get_actions(self, request):
        actions = super().get_actions(request)
        if 'delete_selected' in actions:
            del actions['delete_selected']
        return actions
