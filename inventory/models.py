"""
Mô hình dữ liệu Quản lý Kho hàng (Inventory) — Quốc Hùng Mobile
Tuân thủ đặc tả SRS v2.3 Mục XI, XIV Bảng 24 và Từ điển dữ liệu v2.3 Bảng 24.
"""
from decimal import Decimal
import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Inventory(models.Model):
    """
    Bảng 24. Inventory — Bản ghi tồn kho duy nhất cho từng biến thể sản phẩm (ProductVariant).
    Hệ thống áp dụng mô hình Một kho duy nhất (không có bảng kho chi nhánh).

    Các trường nghiệp vụ:
    - variant: OneToOneField liên kết với catalog.ProductVariant (bắt buộc, duy nhất).
      Sử dụng on_delete=models.PROTECT nhằm bảo vệ toàn vẹn lịch sử kho, chống xóa cứng
      biến thể khi đã tồn tại bản ghi quản lý tồn kho.
    - physical_sellable: Tồn thực tế đủ điều kiện bán (không âm, default 0).
    - reserved: Tồn đã giữ cho các đơn hàng hợp lệ chưa xuất (không âm, <= physical_sellable, default 0).
    - min_threshold: Ngưỡng cảnh báo tồn kho tối thiểu do NV kho thiết lập (không âm, default 0).
    - updated_at: Thời điểm cập nhật trạng thái kho gần nhất (tự động theo dõi).
    - available: Giá trị suy ra = physical_sellable - reserved (KHÔNG lưu trùng cột trong CSDL).
    """
    variant = models.OneToOneField(
        'catalog.ProductVariant',
        on_delete=models.PROTECT,
        related_name='inventory',
        verbose_name='Biến thể sản phẩm',
        help_text='Biến thể sản phẩm được quản lý tồn kho (quan hệ 1-1).',
    )
    physical_sellable = models.IntegerField(
        default=0,
        verbose_name='Tồn thực tế',
        help_text='Số lượng hàng thực tế trong kho đủ điều kiện bán (không âm).',
    )
    reserved = models.IntegerField(
        default=0,
        verbose_name='Tồn đã giữ',
        help_text='Số lượng hàng đã được giữ cho các đơn hàng chưa xuất kho (0 <= reserved <= physical_sellable).',
    )
    min_threshold = models.IntegerField(
        default=0,
        verbose_name='Ngưỡng cảnh báo tối thiểu',
        help_text='Ngưỡng số lượng kích hoạt cảnh báo tồn kho thấp / cần nhập hàng (>= 0).',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Cập nhật lúc',
    )

    class Meta:
        verbose_name = 'Tồn kho'
        verbose_name_plural = 'Tồn kho'
        ordering = ['id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(physical_sellable__gte=0),
                name='inventory_physical_sellable_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__gte=0),
                name='inventory_reserved_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(min_threshold__gte=0),
                name='inventory_min_threshold_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(reserved__lte=models.F('physical_sellable')),
                name='inventory_reserved_lte_physical_sellable',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.physical_sellable is not None and self.physical_sellable < 0:
            errors['physical_sellable'] = 'Tồn thực tế không được là số âm.'
        if self.reserved is not None and self.reserved < 0:
            errors['reserved'] = 'Tồn đã giữ không được là số âm.'
        if self.min_threshold is not None and self.min_threshold < 0:
            errors['min_threshold'] = 'Ngưỡng cảnh báo không được là số âm.'
        if (
            self.physical_sellable is not None
            and self.reserved is not None
            and self.reserved > self.physical_sellable
        ):
            errors['reserved'] = 'Tồn đã giữ không được vượt quá tồn thực tế.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        self.full_clean()
        super().save(*args, **kwargs)

    @property
    def available(self):
        """
        Tồn có thể bán (Available) = Tồn thực tế (physical_sellable) - Tồn đã giữ (reserved).
        Giá trị suy biến theo SRS v2.3 Mục XI.1 và XIV Bảng 24.
        """
        p = self.physical_sellable if self.physical_sellable is not None else 0
        r = self.reserved if self.reserved is not None else 0
        return p - r

    @property
    def is_low_stock(self):
        """
        Cảnh báo tồn kho: kích hoạt khi available <= min_threshold (bao gồm cả trường hợp bằng và bằng 0).
        Tuân thủ BR-I01–I17: alert khi available <= threshold.
        """
        threshold = self.min_threshold if self.min_threshold is not None else 0
        return self.available <= threshold

    @property
    def is_out_of_stock(self):
        """Kiểm tra hết hàng: available <= 0."""
        return self.available <= 0

    @property
    def stock_status_label(self):
        """Nhãn hiển thị trạng thái tồn kho thân thiện cho giao diện UI-29/UI-30."""
        if self.is_out_of_stock:
            return 'Hết hàng'
        elif self.is_low_stock:
            return 'Cần nhập hàng'
        return 'Đủ hàng'

    @property
    def stock_status_badge_class(self):
        """Lớp CSS tương ứng với trạng thái kho để hiển thị huy hiệu (Badge)."""
        if self.is_out_of_stock:
            return 'badge-danger'
        elif self.is_low_stock:
            return 'badge-warning'
        return 'badge-success'

    def __str__(self):
        sku = self.variant.sku if self.variant else 'N/A'
        return (
            f"Tồn kho [{sku}] — Thực tế: {self.physical_sellable}, "
            f"Đã giữ: {self.reserved}, Khả dụng: {self.available}, "
            f"Ngưỡng: {self.min_threshold}"
        )


class InventoryTransaction(models.Model):
    """
    Bảng 26. InventoryTransaction (Sổ cái lịch sử biến động kho) — Quốc Hùng Mobile.
    Tuân thủ đặc tả SRS v2.3 Mục XI, XIV Bảng 26 và Từ điển dữ liệu v2.3 Bảng 26.

    Đặc tả phân kỳ Task 05.2 (Staged Schema — Partial Table 26):
    - Triển khai phần cốt lõi (core) phục vụ nghiệp vụ Nhập hàng (PURCHASE_IN).
    - Các trường nguồn phụ thuộc (Deferred source FKs):
      * related_reservation: Chưa thêm tại Task 05.2 do bảng InventoryReservation
        chưa tồn tại (sẽ bổ sung dạng nullable FK trong tích hợp Đơn hàng/Đổi trả Phase 08/12).
      * failed_delivery_return_item: Chưa thêm tại Task 05.2 do bảng FailedDeliveryReturnItem
        chưa tồn tại (sẽ bổ sung dạng nullable OneToOne FK trong Phase 11).
      * Không tạo bảng giả lập, không dùng FK dạng string chưa resolve, không dùng raw integer.

    Các trường nghiệp vụ cốt lõi:
    - inventory: ForeignKey tới inventory.Inventory (bắt buộc, on_delete=models.PROTECT).
    - transaction_type: CharField(max_length=25) (Loại giao dịch, tại Task 05.2 là 'PURCHASE_IN').
    - quantity_delta: IntegerField (Số lượng biến động có dấu; nonzero, dương cho PURCHASE_IN).
    - physical_before: IntegerField (Tồn thực tế trước biến động, >= 0).
    - physical_after: IntegerField (Tồn thực tế sau biến động, >= 0, after = before + delta cho PURCHASE_IN).
    - performed_by: ForeignKey tới AUTH_USER_MODEL (on_delete=models.PROTECT, nullable theo SRS;
      service nghiệp vụ bắt buộc phải gán nhân viên/admin thực hiện hợp lệ).
    - created_at: DateTimeField(auto_now_add=True) (Thời điểm ghi nhận giao dịch).
    - request_key: UUIDField(unique=True, editable=False) (Khóa kỹ thuật chống ghi lặp / idempotency).
    
    Tính chất bất biến (Append-only Ledger):
    - Không cho phép cập nhật (UPDATE) hay xóa (DELETE) các dòng lịch sử biến động kho đã ghi.
    """
    TYPE_PURCHASE_IN = 'PURCHASE_IN'
    TYPE_ADJUSTMENT = 'ADJUSTMENT'
    TYPE_SHIP = 'SHIP'
    TYPE_RETURN_RESTOCK = 'RETURN_RESTOCK'
    TRANSACTION_TYPE_CHOICES = [
        (TYPE_PURCHASE_IN, 'Nhập hàng'),
        (TYPE_ADJUSTMENT, 'Kiểm kê kho'),
        (TYPE_SHIP, 'Xuất kho giao hàng'),
        (TYPE_RETURN_RESTOCK, 'Nhập hàng đổi trả'),
    ]

    inventory = models.ForeignKey(
        'inventory.Inventory',
        on_delete=models.PROTECT,
        related_name='transactions',
        verbose_name='Tồn kho biến thể',
        help_text='Bản ghi tồn kho của biến thể sản phẩm phát sinh giao dịch.',
    )
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_transactions',
        verbose_name='Đơn hàng liên kết',
        help_text='Đơn hàng phát sinh giao dịch xuất kho (nếu có).',
    )
    return_request = models.ForeignKey(
        'orders.ReturnRequest',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_transactions',
        verbose_name='Yêu cầu đổi trả liên kết',
        help_text='Yêu cầu đổi trả phát sinh giao dịch nhập kho lại (nếu có).',
    )
    transaction_type = models.CharField(
        max_length=25,
        choices=TRANSACTION_TYPE_CHOICES,
        default=TYPE_PURCHASE_IN,
        verbose_name='Loại giao dịch',
        help_text='Loại biến động kho (PURCHASE_IN).',
    )
    quantity_delta = models.IntegerField(
        verbose_name='Số lượng biến động',
        help_text='Số lượng thay đổi tồn kho có dấu (dương khi nhập, khác 0).',
    )
    physical_before = models.IntegerField(
        verbose_name='Tồn thực tế trước',
        help_text='Số lượng tồn thực tế trong kho trước thời điểm giao dịch (>= 0).',
    )
    physical_after = models.IntegerField(
        verbose_name='Tồn thực tế sau',
        help_text='Số lượng tồn thực tế trong kho sau thời điểm giao dịch (>= 0).',
    )
    performed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inventory_transactions',
        verbose_name='Người thực hiện',
        help_text='Tài khoản nhân viên kho hoặc quản trị viên thực hiện thao tác.',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời gian tạo',
        help_text='Thời điểm ghi nhận giao dịch vào sổ cái kho.',
    )
    request_key = models.UUIDField(
        unique=True,
        editable=False,
        verbose_name='Khóa yêu cầu',
        help_text='Mã UUID kỹ thuật định danh duy nhất yêu cầu nhập hàng (Idempotency Key).',
    )

    class Meta:
        verbose_name = 'Biến động tồn kho'
        verbose_name_plural = 'Sổ cái biến động kho'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=~models.Q(quantity_delta=0),
                name='inv_tx_quantity_delta_nonzero',
            ),
            models.CheckConstraint(
                condition=models.Q(physical_before__gte=0),
                name='inv_tx_physical_before_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(physical_after__gte=0),
                name='inv_tx_physical_after_gte_0',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.quantity_delta == 0:
            errors['quantity_delta'] = 'Số lượng biến động không được bằng 0.'
        if self.transaction_type == self.TYPE_PURCHASE_IN:
            if self.quantity_delta is not None and self.quantity_delta <= 0:
                errors['quantity_delta'] = 'Giao dịch nhập hàng (PURCHASE_IN) yêu cầu số lượng biến động dương (> 0).'
            if (
                self.physical_before is not None
                and self.physical_after is not None
                and self.quantity_delta is not None
                and self.physical_after != self.physical_before + self.quantity_delta
            ):
                errors['physical_after'] = 'Tồn thực tế sau giao dịch phải bằng tồn thực tế trước cộng với số lượng nhập.'
        elif self.transaction_type == self.TYPE_ADJUSTMENT:
            if (
                self.physical_before is not None
                and self.physical_after is not None
                and self.quantity_delta is not None
                and self.physical_after != self.physical_before + self.quantity_delta
            ):
                errors['physical_after'] = 'Tồn thực tế sau giao dịch phải bằng tồn thực tế trước cộng với số lượng biến động.'
        elif self.transaction_type == self.TYPE_SHIP:
            if self.quantity_delta is not None and self.quantity_delta >= 0:
                errors['quantity_delta'] = 'Giao dịch xuất kho giao hàng (SHIP) yêu cầu số lượng biến động âm (< 0).'
            if (
                self.physical_before is not None
                and self.physical_after is not None
                and self.quantity_delta is not None
                and self.physical_after != self.physical_before + self.quantity_delta
            ):
                errors['physical_after'] = 'Tồn thực tế sau giao dịch phải bằng tồn thực tế trước cộng với số lượng biến động (giảm).'
            order_pk = self.order_id or (self.order.pk if hasattr(self, 'order') and self.order else None)
            if not order_pk:
                errors['order'] = 'Giao dịch xuất kho giao hàng (SHIP) bắt buộc phải liên kết với đơn hàng.'
        elif self.transaction_type == self.TYPE_RETURN_RESTOCK:
            if self.quantity_delta is not None and self.quantity_delta <= 0:
                errors['quantity_delta'] = 'Giao dịch nhập hàng đổi trả (RETURN_RESTOCK) yêu cầu số lượng biến động dương (> 0).'
            if (
                self.physical_before is not None
                and self.physical_after is not None
                and self.quantity_delta is not None
                and self.physical_after != self.physical_before + self.quantity_delta
            ):
                errors['physical_after'] = 'Tồn thực tế sau giao dịch phải bằng tồn thực tế trước cộng với số lượng nhập lại.'
        if self.physical_before is not None and self.physical_before < 0:
            errors['physical_before'] = 'Tồn thực tế trước giao dịch không được âm.'
        if self.physical_after is not None and self.physical_after < 0:
            errors['physical_after'] = 'Tồn thực tế sau giao dịch không được âm.'
        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Lịch sử biến động kho là bản ghi bất biến (append-only), không thể chỉnh sửa.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Lịch sử biến động kho là bản ghi bất biến (append-only), không thể xóa.')

    @property
    def transaction_type_display(self):
        """Tên hiển thị tiếng Việt cho loại giao dịch."""
        return dict(self.TRANSACTION_TYPE_CHOICES).get(self.transaction_type, self.transaction_type)

    def __str__(self):
        sku = self.inventory.variant.sku if self.inventory and self.inventory.variant else 'N/A'
        delta_str = f"+{self.quantity_delta}" if self.quantity_delta > 0 else f"{self.quantity_delta}"
        return (
            f"[{self.transaction_type_display}] {sku}: {delta_str} "
            f"({self.physical_before} -> {self.physical_after}) lúc {self.created_at}"
        )


class InventoryAdjustment(models.Model):
    """
    Bảng 27. InventoryAdjustment — Kiểm kê chênh lệch tồn kho thực tế (Quốc Hùng Mobile).
    Tuân thủ đặc tả SRS v2.3 Mục XI, XIV Bảng 27 và Từ điển dữ liệu v2.3 Bảng 27.

    Các trường nghiệp vụ:
    - inventory: ForeignKey tới inventory.Inventory (bắt buộc, on_delete=models.PROTECT).
    - old_quantity: IntegerField (Tồn thực tế trước kiểm kê, >= 0).
    - new_quantity: IntegerField (Tồn thực tế sau kiểm kê, >= 0, new_quantity >= reserved).
    - reason: TextField (Lý do điều chỉnh kiểm kê bắt buộc, không được để trống hoặc chỉ có khoảng trắng).
    - adjusted_by: ForeignKey tới AUTH_USER_MODEL (on_delete=models.PROTECT, người thực hiện kiểm kê).
    - transaction: OneToOneField tới InventoryTransaction (on_delete=models.PROTECT, nullable theo schema,
      nhưng trong luồng nghiệp vụ thực thi Task 05.3 luôn liên kết đúng 1 giao dịch sổ cái loại ADJUSTMENT).
    - created_at: DateTimeField(auto_now_add=True) (Thời điểm ghi nhận điều chỉnh).

    Tính chất bất biến (Audit Record):
    - Bản ghi điều chỉnh kiểm kê là bất biến, không thể sửa đổi hoặc xóa bỏ qua UI hay Admin.
    """
    inventory = models.ForeignKey(
        'inventory.Inventory',
        on_delete=models.PROTECT,
        related_name='adjustments',
        verbose_name='Tồn kho biến thể',
        help_text='Bản ghi tồn kho của biến thể sản phẩm được kiểm kê điều chỉnh.',
    )
    old_quantity = models.IntegerField(
        verbose_name='Tồn thực tế trước',
        help_text='Số lượng tồn thực tế trong kho trước khi kiểm kê điều chỉnh (>= 0).',
    )
    new_quantity = models.IntegerField(
        verbose_name='Tồn thực tế sau',
        help_text='Số lượng tồn thực tế sau khi kiểm kê điều chỉnh (>= 0, >= reserved).',
    )
    reason = models.TextField(
        verbose_name='Lý do điều chỉnh',
        help_text='Lý do thực hiện điều chỉnh tồn kho (bắt buộc).',
    )
    adjusted_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name='inventory_adjustments',
        verbose_name='Người điều chỉnh',
        help_text='Tài khoản nhân viên kho hoặc quản trị viên thực hiện điều chỉnh.',
    )
    transaction = models.OneToOneField(
        'inventory.InventoryTransaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='adjustment',
        verbose_name='Giao dịch sổ cái liên kết',
        help_text='Bản ghi giao dịch sổ cái tương ứng phát sinh từ lần kiểm kê này (1-1).',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời gian tạo',
        help_text='Thời điểm ghi nhận bản ghi điều chỉnh kiểm kê.',
    )

    class Meta:
        verbose_name = 'Điều chỉnh kiểm kê'
        verbose_name_plural = 'Lịch sử điều chỉnh kiểm kê'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(old_quantity__gte=0),
                name='inv_adj_old_quantity_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(new_quantity__gte=0),
                name='inv_adj_new_quantity_gte_0',
            ),
        ]

    def clean(self):
        super().clean()
        errors = {}
        if self.old_quantity is not None and self.old_quantity < 0:
            errors['old_quantity'] = 'Tồn thực tế trước kiểm kê không được là số âm.'
        if self.new_quantity is not None and self.new_quantity < 0:
            errors['new_quantity'] = 'Tồn thực tế sau kiểm kê không được là số âm.'
        if self.old_quantity is not None and self.new_quantity is not None:
            if self.old_quantity == self.new_quantity:
                errors['new_quantity'] = 'Số lượng kiểm kê mới phải khác số lượng hiện tại (không có chênh lệch).'

        if not self.reason or not self.reason.strip():
            errors['reason'] = 'Lý do điều chỉnh không được để trống hoặc chỉ chứa khoảng trắng.'

        if self.inventory_id and hasattr(self, 'inventory') and self.inventory:
            if self.new_quantity is not None and self.inventory.reserved is not None:
                if self.new_quantity < self.inventory.reserved:
                    errors['new_quantity'] = (
                        f'Số lượng kiểm kê ({self.new_quantity}) không được nhỏ hơn số lượng hàng đang giữ cho đơn ({self.inventory.reserved}).'
                    )

        if self.transaction_id and hasattr(self, 'transaction') and self.transaction:
            tx = self.transaction
            if tx.inventory_id != self.inventory_id:
                errors['transaction'] = 'Giao dịch liên kết phải thuộc về cùng bản ghi tồn kho.'
            if self.old_quantity is not None and tx.physical_before != self.old_quantity:
                errors['transaction'] = 'Tồn trước của giao dịch liên kết phải khớp với tồn trước của bản ghi điều chỉnh.'
            if self.new_quantity is not None and tx.physical_after != self.new_quantity:
                errors['transaction'] = 'Tồn sau của giao dịch liên kết phải khớp với tồn sau của bản ghi điều chỉnh.'
            if self.old_quantity is not None and self.new_quantity is not None:
                expected_delta = self.new_quantity - self.old_quantity
                if tx.quantity_delta != expected_delta:
                    errors['transaction'] = 'Biến động của giao dịch liên kết phải bằng số mới trừ số cũ.'
            if self.adjusted_by_id and tx.performed_by_id != self.adjusted_by_id:
                errors['transaction'] = 'Người thực hiện của giao dịch liên kết phải khớp với người điều chỉnh.'
            if tx.transaction_type != InventoryTransaction.TYPE_ADJUSTMENT:
                errors['transaction'] = 'Giao dịch liên kết phải có loại là ADJUSTMENT.'

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.pk is not None:
            raise ValidationError('Bản ghi điều chỉnh kiểm kê là bất biến, không thể chỉnh sửa.')
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Bản ghi điều chỉnh kiểm kê là bất biến, không thể xóa.')

    @property
    def quantity_delta(self):
        """Chênh lệch kiểm kê có dấu = new_quantity - old_quantity."""
        if self.old_quantity is not None and self.new_quantity is not None:
            return self.new_quantity - self.old_quantity
        return 0

    def __str__(self):
        sku = self.inventory.variant.sku if self.inventory and self.inventory.variant else 'N/A'
        delta = self.quantity_delta
        delta_str = f"+{delta}" if delta > 0 else f"{delta}"
        return f"Điều chỉnh tồn [{sku}]: {self.old_quantity} -> {self.new_quantity} ({delta_str}) lúc {self.created_at}"


class InventoryReservationStatus(models.TextChoices):
    """
    Trạng thái dòng giữ hàng theo SRS v2.3 Mục XIV Bảng 25.
    Chỉ mô tả lưu trữ (storage description), không triển khai chuyển đổi trạng thái (no transitions).
    """
    ACTIVE = 'ACTIVE', 'Đang giữ'
    RELEASED = 'RELEASED', 'Đã giải phóng'
    SHIPPED = 'SHIPPED', 'Đã xuất giao'


class InventoryReservation(models.Model):
    """
    Bảng 25. InventoryReservation — Dòng giữ hàng theo đơn hoặc theo hàng đổi.
    Tuân thủ đặc tả SRS v2.3 Mục XI, XIV Bảng 25 và Từ điển dữ liệu v2.3 Bảng 25.

    Kế hoạch phân kỳ 2 giai đoạn (Two-Stage Migration Plan):
    - Giai đoạn 1 (Task 08.3): Triển khai nhánh giữ hàng cho đơn hàng (order-item path).
      Khóa ngoại order_item bắt buộc (NOT NULL) bảo đảm chính xác một nguồn giữ hàng khả dụng
      tại thời điểm này. Không áp đặt ràng buộc UNIQUE trên OrderItem để hỗ trợ cấu trúc mở.
    - Giai đoạn 2 (Phase 11 - Phân hệ Đổi trả): Khi model ReturnRequestItem chính thức tồn tại,
      sẽ tạo migration mới: bổ sung return_item (nullable FK), chuyển order_item thành nullable,
      và thiết lập ràng buộc CSDL XOR (chính xác một trong hai order_item hoặc return_item có giá trị).

    Ghi chú kỹ thuật về Ranh giới Kiểm tra Model vs CSDL:
    - Kiểm tra khớp biến thể (self.variant_id == self.order_item.variant_id) được thực thi
      tại tầng Model Validation (clean()) trong ứng dụng Python/Django.
    - Ở tầng CSDL quan hệ (SQLite/PostgreSQL), không thể áp đặt CheckConstraint xuyên bảng
      (cross-table check constraint) giữa bảng inventory_inventoryreservation và bảng orders_orderitem
      mà không sử dụng Trigger CSDL phức tạp hoặc khóa ngoại phức hợp (composite FK).
    - Do đó, tính toàn vẹn phụ thuộc vào:
      (1) Khóa ngoại CSDL PROTECT tới variant và order_item;
      (2) Model clean() của Django và tầng service trong tương lai trước khi commit giao dịch.
    """
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.PROTECT,
        related_name='reservations',
        verbose_name='Biến thể sản phẩm',
        help_text='Biến thể sản phẩm được giữ hàng (on_delete=PROTECT bảo toàn liên kết giữ hàng).',
    )
    order_item = models.ForeignKey(
        'orders.OrderItem',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reservations',
        verbose_name='Dòng đơn hàng',
        help_text='Dòng đơn hàng nguồn giữ hàng (on_delete=PROTECT bảo toàn liên kết giữ hàng).',
    )
    return_request = models.ForeignKey(
        'orders.ReturnRequest',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='reservations',
        verbose_name='Yêu cầu đổi trả',
        help_text='Yêu cầu đổi trả nguồn giữ hàng thay thế (on_delete=PROTECT).',
    )
    quantity = models.IntegerField(
        verbose_name='Số lượng giữ',
        help_text='Số lượng sản phẩm được giữ (> 0).',
    )
    status = models.CharField(
        max_length=15,
        choices=InventoryReservationStatus.choices,
        verbose_name='Trạng thái giữ hàng',
        help_text='Trạng thái giữ hàng (ACTIVE, RELEASED, SHIPPED).',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời gian tạo',
        help_text='Thời điểm tạo bản ghi giữ hàng.',
    )
    finished_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời gian kết thúc',
        help_text='Thời điểm kết thúc giữ hàng (bắt buộc NULL khi còn ACTIVE).',
    )

    class Meta:
        verbose_name = 'Giữ hàng tồn kho'
        verbose_name_plural = 'Dòng giữ hàng tồn kho'
        ordering = ['-created_at', '-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(quantity__gt=0),
                name='inv_reservation_quantity_gt_0',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=['ACTIVE', 'RELEASED', 'SHIPPED']),
                name='inv_reservation_status_valid',
            ),
        ]

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        self._validation_exclude = exclude_set
        errors = {}

        # QA-T083-01: Thẩm định giá trị thô của quantity trước khi Django ép kiểu (coercion).
        # IntegerField.to_python() của Django tự động ép 1.5 -> 1 và True -> 1 qua int(value).
        # Bắt buộc từ chối boolean và giá trị phân số (fractional float, Decimal) với ValidationError của quantity.
        # Tách biệt kiểm tra Decimal và float:
        # - Decimal: dùng is_finite() bản địa để loại trừ an toàn sNaN, NaN, Infinity trước khi so sánh;
        #   kiểm tra tính nguyên qua so sánh với to_integral_value() mà không phụ thuộc phép chia lấy dư (%) nhạy cảm ngữ cảnh.
        # - float: kiểm tra qua math.isfinite() và phần dư chia cho 1.
        if 'quantity' not in exclude_set:
            raw_quantity = getattr(self, 'quantity', None)
            if isinstance(raw_quantity, bool):
                errors['quantity'] = [
                    ValidationError('Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.', code='invalid')
                ]
            elif isinstance(raw_quantity, float):
                if not math.isfinite(raw_quantity) or raw_quantity % 1 != 0:
                    errors['quantity'] = [
                        ValidationError('Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.', code='invalid')
                    ]
            elif isinstance(raw_quantity, Decimal):
                if not raw_quantity.is_finite() or raw_quantity != raw_quantity.to_integral_value():
                    errors['quantity'] = [
                        ValidationError('Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.', code='invalid')
                    ]

        # Nếu quantity đã bị từ chối ở bước kiểm tra tiền ép kiểu, loại trừ nó khỏi super().clean_fields()
        # để Django không ghi đè giá trị thô thành số nguyên bị ép kiểu (coerced int).
        exclude_for_super = exclude_set | {'quantity'} if 'quantity' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}
        exclude = getattr(self, '_validation_exclude', None)

        try:
            # 1. Thẩm định số lượng giữ hàng (positive integer)
            if exclude is None or 'quantity' not in exclude:
                if self.quantity is not None:
                    if isinstance(self.quantity, int) and not isinstance(self.quantity, bool):
                        if self.quantity <= 0:
                            errors['quantity'] = 'Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.'
                    elif exclude is None:
                        # Khi clean() được gọi trực tiếp mà không qua clean_fields()
                        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
                            errors['quantity'] = 'Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.'
                else:
                    if exclude is None:
                        errors['quantity'] = 'Số lượng giữ hàng không được để trống.'

            # 2. Thẩm định trạng thái (explicit status)
            if not self.status:
                errors['status'] = 'Trạng thái giữ hàng không được để trống.'
            elif self.status not in InventoryReservationStatus.values:
                errors['status'] = f"Trạng thái giữ hàng không hợp lệ. Phải thuộc {InventoryReservationStatus.values}."

            # 3. Quy tắc timestamp Bảng 25: finished_at bắt buộc NULL khi còn ACTIVE
            # Để ngỏ yêu cầu timestamp khi RELEASED/SHIPPED cho workflow task.
            # Để Django DateTimeField tự thẩm định định dạng, không so sánh custom time.
            if self.status == InventoryReservationStatus.ACTIVE and self.finished_at is not None:
                errors['finished_at'] = 'finished_at phải là NULL khi trạng thái còn là ACTIVE.'

            # 4. Thẩm định nguồn giữ hàng (order_item hoặc return_request)
            has_order_item = bool(self.order_item_id or (hasattr(self, 'order_item') and self.order_item))
            has_return_request = bool(self.return_request_id or (hasattr(self, 'return_request') and self.return_request))
            if not has_order_item and not has_return_request:
                errors['order_item'] = 'Bản ghi giữ hàng bắt buộc phải gắn với dòng đơn hàng hoặc yêu cầu đổi trả.'
            elif has_order_item and has_return_request:
                errors['order_item'] = 'Bản ghi giữ hàng không thể đồng thời gắn với cả dòng đơn hàng và yêu cầu đổi trả.'

            # 5. Thẩm định khớp biến thể (variant match)
            variant_pk = self.variant_id or (self.variant.pk if hasattr(self, 'variant') and self.variant else None)
            if has_order_item:
                try:
                    order_item = self.order_item if hasattr(self, 'order_item') else None
                except Exception:
                    order_item = None
                if order_item is not None and variant_pk is not None:
                    oi_variant_pk = order_item.variant_id or (order_item.variant.pk if hasattr(order_item, 'variant') and order_item.variant else None)
                    if oi_variant_pk and oi_variant_pk != variant_pk:
                        errors['variant'] = (
                            f"Biến thể giữ hàng (ID {variant_pk}) không khớp với biến thể của dòng đơn hàng (ID {oi_variant_pk})."
                        )
            elif has_return_request:
                try:
                    return_request = self.return_request if hasattr(self, 'return_request') else None
                except Exception:
                    return_request = None
                if return_request is not None and variant_pk is not None:
                    rr_variant_pk = return_request.replacement_variant_id or (return_request.replacement_variant.pk if hasattr(return_request, 'replacement_variant') and return_request.replacement_variant else None)
                    if rr_variant_pk and rr_variant_pk != variant_pk:
                        errors['variant'] = (
                            f"Biến thể giữ hàng (ID {variant_pk}) không khớp với biến thể thay thế của yêu cầu đổi trả (ID {rr_variant_pk})."
                        )

            if errors:
                raise ValidationError(errors)
        finally:
            if hasattr(self, '_validation_exclude'):
                del self._validation_exclude

    def save(self, *args, **kwargs):
        """
        Lưu bản ghi giữ hàng tồn kho với cơ chế bảo vệ tính bất biến của các cặp trường phụ thuộc:

        Chính sách lưu trữ (QA-T083-03):
        - Lưu hoàn chỉnh thông thường (complete save: update_fields=None):
          Bảo toàn hành vi chuẩn, gọi full_clean() kiểm tra toàn diện thực thể và lưu toàn bộ trường.
        - Lưu một phần (partial save: update_fields is not None):
          Yêu cầu các lần lưu một phần chạm vào bất kỳ thành viên nào của một cặp trường phụ thuộc
          phải bao gồm đầy đủ cả hai thành viên của cặp đó:
          1. Cặp phụ thuộc biến thể - dòng đơn: (variant, order_item)
             Bao gồm cả tên trường ('variant', 'order_item') và attnames ('variant_id', 'order_item_id').
             Chặn việc chỉ lưu variant mà bỏ lại order_item (hoặc ngược lại), ngăn ngừa lệch biến thể trong CSDL.
          2. Cặp phụ thuộc trạng thái - thời gian kết thúc: (status, finished_at)
             Chặn việc chỉ lưu finished_at mà bỏ lại status (hoặc ngược lại), ngăn ngừa trạng thái ACTIVE có finished_at.
          3. Cho phép lưu chỉ riêng trường số lượng (quantity-only save: update_fields=['quantity']).
          4. Tuyệt đối không tự ý mở rộng ngầm (silently broaden) update_fields của caller sang các trường không liên quan.

        Ghi chú kiến trúc về Bulk ORM & Raw SQL:
        - Các thao tác QuerySet.update() và Raw SQL trong Django ORM bỏ qua các phương thức của Model
          (save, full_clean, clean). Ràng buộc CSDL (CheckConstraint inv_reservation_quantity_gt_0 và
          inv_reservation_status_valid, khóa ngoại Foreign Key SQLite) vẫn được SQLite thực thi ở tầng CSDL.
          Các bất biến phụ thuộc giữa các trường được thực thi bảo vệ tại tầng Model save() của Django.
        """
        update_fields = kwargs.get('update_fields')
        if update_fields is None and len(args) >= 4:
            update_fields = args[3]

        if update_fields is not None:
            field_set = set(update_fields)
            if 'update_fields' in kwargs:
                kwargs['update_fields'] = field_set

            # 1. Bảo vệ cặp phụ thuộc (variant, order_item) hoặc (variant, return_request)
            has_variant = bool(field_set & {'variant', 'variant_id'})
            has_order_item = bool(field_set & {'order_item', 'order_item_id'})
            has_return_request = bool(field_set & {'return_request', 'return_request_id'})
            if has_variant and not (has_order_item or has_return_request):
                err_msg = (
                    'Khi cập nhật một phần (update_fields) với variant, '
                    'bắt buộc phải bao gồm order_item hoặc return_request '
                    'để bảo đảm tính nhất quán nguồn giữ hàng.'
                )
                raise ValidationError({
                    'variant': err_msg,
                })

            # 2. Bảo vệ cặp phụ thuộc (status, finished_at)
            has_status = 'status' in field_set
            has_finished_at = 'finished_at' in field_set
            if has_status != has_finished_at:
                err_msg = (
                    'Khi cập nhật một phần (update_fields) với status hoặc finished_at, '
                    'bắt buộc phải bao gồm cả hai trường (status và finished_at) '
                    'để bảo đảm ràng buộc trạng thái kết thúc.'
                )
                raise ValidationError({
                    'status': err_msg,
                    'finished_at': err_msg,
                })

        self.full_clean()
        super().save(*args, **kwargs)

    def __str__(self):
        sku = self.variant.sku if self.variant_id and hasattr(self, 'variant') and self.variant else 'N/A'
        return f"Giữ hàng #{self.pk or 'Mới'} [{self.status}]: {self.quantity} x {sku}"


class ReturnedStockCondition(models.TextChoices):
    WAITING_INSPECTION = 'WAITING_INSPECTION', 'Chờ kiểm tra'
    ELIGIBLE_FOR_RESALE = 'ELIGIBLE_FOR_RESALE', 'Đủ điều kiện bán lại'
    NOT_ELIGIBLE_FOR_RESALE = 'NOT_ELIGIBLE_FOR_RESALE', 'Không đủ điều kiện bán lại'


class ReturnedStock(models.Model):
    """
    Quản lý phân loại hàng đổi trả theo tình trạng (Returned-stock classification).
    Tuân thủ các quy tắc nghiệp vụ:
    - Hàng chờ kiểm tra (WAITING_INSPECTION) KHÔNG làm tăng tồn khả dụng (available).
    - Hàng không đủ điều kiện bán lại (NOT_ELIGIBLE_FOR_RESALE) KHÔNG làm tăng tồn khả dụng.
    - Chỉ sau khi Nhân viên kho xác nhận đủ điều kiện bán lại (ELIGIBLE_FOR_RESALE)
      và thực hiện nhập kho, hệ thống mới cộng vào tồn thực tế (physical_sellable).
    - Ghi nhận lịch sử biến động kho (InventoryTransaction TYPE_RETURN_RESTOCK) khi nhập lại vào kho bán.
    - Tuyệt đối không gộp hàng lỗi, hàng chờ kiểm tra và hàng bán được vào chung một số lượng bán được.
    """
    return_request = models.OneToOneField(
        'orders.ReturnRequest',
        on_delete=models.PROTECT,
        related_name='returned_stock',
        verbose_name='Yêu cầu đổi trả',
        help_text='Yêu cầu đổi trả liên kết với lô hàng nhận về.',
    )
    variant = models.ForeignKey(
        'catalog.ProductVariant',
        on_delete=models.PROTECT,
        related_name='returned_stocks',
        verbose_name='Biến thể sản phẩm',
        help_text='Biến thể của sản phẩm nhận về.',
    )
    quantity = models.PositiveIntegerField(
        verbose_name='Số lượng nhận về',
        help_text='Số lượng sản phẩm khách hàng đã hoàn trả.',
    )
    condition = models.CharField(
        max_length=30,
        choices=ReturnedStockCondition.choices,
        default=ReturnedStockCondition.WAITING_INSPECTION,
        verbose_name='Tình trạng sản phẩm',
        help_text='Phân loại tình trạng hàng trả: Chờ kiểm tra, Đủ điều kiện bán lại, hoặc Không đủ điều kiện bán lại.',
    )
    received_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='received_returned_stocks',
        verbose_name='Nhân viên kho nhận hàng',
    )
    received_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm nhận hàng',
    )
    inspected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='inspected_returned_stocks',
        verbose_name='Nhân viên kiểm tra tình trạng',
    )
    inspected_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm kiểm tra',
    )
    inspection_notes = models.TextField(
        null=True,
        blank=True,
        verbose_name='Ghi chú kiểm tra tình trạng',
        help_text='Mô tả chi tiết tình trạng hàng thực tế, lỗi hỏng hoặc phụ kiện kèm theo.',
    )
    is_restocked = models.BooleanField(
        default=False,
        verbose_name='Đã nhập kho bán',
        help_text='Đã cộng vào tồn thực tế đủ điều kiện bán (physical_sellable) hay chưa.',
    )
    restocked_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm nhập kho bán',
    )
    restocked_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='restocked_returned_stocks',
        verbose_name='Người xác nhận nhập kho',
    )
    transaction = models.OneToOneField(
        'inventory.InventoryTransaction',
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name='returned_stock',
        verbose_name='Giao dịch nhập kho liên kết',
    )
    created_at = models.DateTimeField(
        auto_now_add=True,
        verbose_name='Thời điểm tạo',
    )
    updated_at = models.DateTimeField(
        auto_now=True,
        verbose_name='Thời điểm cập nhật',
    )

    class Meta:
        verbose_name = 'Hàng đổi trả'
        verbose_name_plural = 'Quản lý hàng đổi trả'
        ordering = ['-created_at', '-id']

    def __str__(self):
        sku = self.variant.sku if self.variant else 'N/A'
        return f"Hàng trả [{sku}] x {self.quantity} ({self.get_condition_display()})"

    def clean(self):
        super().clean()
        errors = {}
        if not self.quantity or self.quantity <= 0:
            errors['quantity'] = 'Số lượng nhận về phải lớn hơn 0.'
        if self.is_restocked and self.condition != ReturnedStockCondition.ELIGIBLE_FOR_RESALE:
            errors['is_restocked'] = 'Chỉ có thể nhập kho bán đối với sản phẩm đủ điều kiện bán lại.'
        if errors:
            raise ValidationError(errors)




