from decimal import Decimal
import math

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


class Inventory(models.Model):
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
        p = self.physical_sellable if self.physical_sellable is not None else 0
        r = self.reserved if self.reserved is not None else 0
        return p - r

    @property
    def is_low_stock(self):
        threshold = self.min_threshold if self.min_threshold is not None else 0
        return self.available <= threshold

    @property
    def is_out_of_stock(self):
        return self.available <= 0

    @property
    def stock_status_label(self):
        if self.is_out_of_stock:
            return 'Hết hàng'
        elif self.is_low_stock:
            return 'Cần nhập hàng'
        return 'Đủ hàng'

    @property
    def stock_status_badge_class(self):
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
        return dict(self.TRANSACTION_TYPE_CHOICES).get(self.transaction_type, self.transaction_type)

    def __str__(self):
        sku = self.inventory.variant.sku if self.inventory and self.inventory.variant else 'N/A'
        delta_str = f"+{self.quantity_delta}" if self.quantity_delta > 0 else f"{self.quantity_delta}"
        return (
            f"[{self.transaction_type_display}] {sku}: {delta_str} "
            f"({self.physical_before} -> {self.physical_after}) lúc {self.created_at}"
        )


class InventoryAdjustment(models.Model):
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
        if self.old_quantity is not None and self.new_quantity is not None:
            return self.new_quantity - self.old_quantity
        return 0

    def __str__(self):
        sku = self.inventory.variant.sku if self.inventory and self.inventory.variant else 'N/A'
        delta = self.quantity_delta
        delta_str = f"+{delta}" if delta > 0 else f"{delta}"
        return f"Điều chỉnh tồn [{sku}]: {self.old_quantity} -> {self.new_quantity} ({delta_str}) lúc {self.created_at}"


class InventoryReservationStatus(models.TextChoices):
    ACTIVE = 'ACTIVE', 'Đang giữ'
    RELEASED = 'RELEASED', 'Đã giải phóng'
    SHIPPED = 'SHIPPED', 'Đã xuất giao'


class InventoryReservation(models.Model):
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
            if exclude is None or 'quantity' not in exclude:
                if self.quantity is not None:
                    if isinstance(self.quantity, int) and not isinstance(self.quantity, bool):
                        if self.quantity <= 0:
                            errors['quantity'] = 'Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.'
                    elif exclude is None:
                        if not isinstance(self.quantity, int) or isinstance(self.quantity, bool) or self.quantity <= 0:
                            errors['quantity'] = 'Số lượng giữ hàng phải là số nguyên dương lớn hơn 0.'
                else:
                    if exclude is None:
                        errors['quantity'] = 'Số lượng giữ hàng không được để trống.'

            if not self.status:
                errors['status'] = 'Trạng thái giữ hàng không được để trống.'
            elif self.status not in InventoryReservationStatus.values:
                errors['status'] = f"Trạng thái giữ hàng không hợp lệ. Phải thuộc {InventoryReservationStatus.values}."

            if self.status == InventoryReservationStatus.ACTIVE and self.finished_at is not None:
                errors['finished_at'] = 'finished_at phải là NULL khi trạng thái còn là ACTIVE.'

            has_order_item = bool(self.order_item_id or (hasattr(self, 'order_item') and self.order_item))
            has_return_request = bool(self.return_request_id or (hasattr(self, 'return_request') and self.return_request))
            if not has_order_item and not has_return_request:
                errors['order_item'] = 'Bản ghi giữ hàng bắt buộc phải gắn với dòng đơn hàng hoặc yêu cầu đổi trả.'
            elif has_order_item and has_return_request:
                errors['order_item'] = 'Bản ghi giữ hàng không thể đồng thời gắn với cả dòng đơn hàng và yêu cầu đổi trả.'

            variant_pk = self.variant_id or (self.variant.pk if hasattr(self, 'variant') and self.variant else None)
            try:
                order_item = self.order_item if hasattr(self, 'order_item') else None
            except Exception:
                order_item = None

            if has_order_item and order_item is not None and variant_pk is not None:
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
        update_fields = kwargs.get('update_fields')
        if update_fields is None and len(args) >= 4:
            update_fields = args[3]

        if update_fields is not None:
            field_set = set(update_fields)
            if 'update_fields' in kwargs:
                kwargs['update_fields'] = field_set

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
    return_request = models.OneToOneField('orders.ReturnRequest', on_delete=models.PROTECT, related_name='returned_stock', verbose_name='Yêu cầu đổi trả', help_text='Yêu cầu đổi trả liên kết với lô hàng nhận về.')
    variant = models.ForeignKey('catalog.ProductVariant', on_delete=models.PROTECT, related_name='returned_stocks', verbose_name='Biến thể sản phẩm', help_text='Biến thể của sản phẩm nhận về.')
    quantity = models.PositiveIntegerField(verbose_name='Số lượng nhận về', help_text='Số lượng sản phẩm khách hàng đã hoàn trả.')
    condition = models.CharField(max_length=30, choices=ReturnedStockCondition.choices, default=ReturnedStockCondition.WAITING_INSPECTION, verbose_name='Tình trạng sản phẩm', help_text='Phân loại tình trạng hàng trả: Chờ kiểm tra, Đủ điều kiện bán lại, hoặc Không đủ điều kiện bán lại.')
    received_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='received_returned_stocks', verbose_name='Nhân viên kho nhận hàng')
    received_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm nhận hàng')
    inspected_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='inspected_returned_stocks', verbose_name='Nhân viên kiểm tra tình trạng')
    inspected_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm kiểm tra')
    inspection_notes = models.TextField(null=True, blank=True, verbose_name='Ghi chú kiểm tra tình trạng', help_text='Mô tả chi tiết tình trạng hàng thực tế, lỗi hỏng hoặc phụ kiện kèm theo.')
    is_restocked = models.BooleanField(default=False, verbose_name='Đã nhập kho bán', help_text='Đã cộng vào tồn thực tế đủ điều kiện bán (physical_sellable) hay chưa.')
    restocked_at = models.DateTimeField(null=True, blank=True, verbose_name='Thời điểm nhập kho bán')
    restocked_by = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, null=True, blank=True, related_name='restocked_returned_stocks', verbose_name='Người xác nhận nhập kho')
    transaction = models.OneToOneField('inventory.InventoryTransaction', on_delete=models.PROTECT, null=True, blank=True, related_name='returned_stock', verbose_name='Giao dịch nhập kho liên kết')
    created_at = models.DateTimeField(auto_now_add=True, verbose_name='Thời điểm tạo')
    updated_at = models.DateTimeField(auto_now=True, verbose_name='Thời điểm cập nhật')

    class Meta:
        verbose_name = 'Hàng đổi trả'
        verbose_name_plural = 'Quản lý hàng đổi trả'
        ordering = ['-created_at', '-id']

    def clean(self):
        super().clean()
        errors = {}
        if not self.quantity or self.quantity <= 0:
            errors['quantity'] = 'Số lượng nhận về phải lớn hơn 0.'
        if self.is_restocked and self.condition != ReturnedStockCondition.ELIGIBLE_FOR_RESALE:
            errors['is_restocked'] = 'Chỉ có thể nhập kho bán đối với sản phẩm đủ điều kiện bán lại.'
        if errors:
            raise ValidationError(errors)

    def __str__(self):
        sku = self.variant.sku if self.variant_id else 'N/A'
        return f"Hàng trả [{sku}] x {self.quantity} ({self.get_condition_display()})"


