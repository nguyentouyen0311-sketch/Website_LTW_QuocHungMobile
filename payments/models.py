from decimal import Decimal
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models


def _is_finite_decimal(value):
    return isinstance(value, Decimal) and value.is_finite()


class PaymentMethod(models.TextChoices):
    COD = 'COD', 'Thanh toán khi nhận hàng (COD)'


class CODCollection(models.Model):

    payment = models.OneToOneField(
        'Payment', on_delete=models.PROTECT, related_name='cod_collection'
    )
    amount = models.DecimalField(max_digits=14, decimal_places=2)
    reference = models.CharField(max_length=100, unique=True)
    collected_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name='cod_collections'
    )
    collected_at = models.DateTimeField()
    note = models.CharField(max_length=500, blank=True)

    class Meta:
        verbose_name = 'Chứng từ thu COD'
        verbose_name_plural = 'Chứng từ thu COD'
        constraints = [
            models.CheckConstraint(condition=models.Q(amount__gt=0), name='cod_collection_positive'),
        ]

    def save(self, *args, **kwargs):
        if self.pk:
            raise ValidationError('Chứng từ thu tiền là bản ghi bất biến.')
        self.reference = str(self.reference or '').strip().upper()
        self.full_clean()
        super().save(*args, **kwargs)

    def delete(self, *args, **kwargs):
        raise ValidationError('Không được xóa chứng từ thu tiền.')


class PaymentStatus(models.TextChoices):
    UNPAID = 'UNPAID', 'Chưa thanh toán'
    PAID = 'PAID', 'Đã thanh toán'

    FAILED = 'FAILED', 'Thanh toán thất bại'
    PENDING_REFUND = 'PENDING_REFUND', 'Chờ hoàn tiền'
    REFUNDED = 'REFUNDED', 'Đã hoàn tiền'
    PARTIALLY_REFUNDED = 'PARTIALLY_REFUNDED', 'Hoàn tiền một phần'


class Payment(models.Model):
    order = models.OneToOneField(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='payment',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng gắn liền với bản ghi thanh toán (OneToOne, on_delete=PROTECT bảo toàn lịch sử tài chính).',
    )
    method = models.CharField(
        max_length=10,
        choices=PaymentMethod.choices,
        verbose_name='Phương thức thanh toán',
        help_text='Phương thức thanh toán khi nhận hàng (COD).',
    )
    status = models.CharField(
        max_length=30,
        choices=PaymentStatus.choices,
        verbose_name='Trạng thái thanh toán',
        help_text='Trạng thái thanh toán độc lập (yêu cầu gán tường minh, không gán mặc định ngầm định).',
    )
    expected_amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Số tiền dự kiến',
        help_text='Số tiền cần thanh toán snapshot tại thời điểm khởi tạo (>= 0, không tự động tính lại).',
    )
    notes = models.TextField(
        null=True,
        blank=True,
        verbose_name='Ghi chú thanh toán',
        help_text='Ghi chú thanh toán COD hoặc thông tin bổ sung.',
    )

    class Meta:
        verbose_name = 'Thanh toán'
        verbose_name_plural = 'Thanh toán'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(expected_amount__gte=0),
                name='payment_expected_amount_gte_0',
            ),
            models.CheckConstraint(
                condition=models.Q(method__in=PaymentMethod.values),
                name='payment_method_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=PaymentStatus.values),
                name='payment_status_valid',
            ),
        ]

    def __str__(self):
        try:
            order_ref = self.order.order_code if self.order_id else 'chưa gán'
        except Exception:
            order_ref = self.order_id or 'chưa gán'
        return f"Thanh toán {self.get_method_display()} - Đơn {order_ref} ({self.get_status_display()}: {self.expected_amount} VNĐ)"

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        errors = {}

        if 'expected_amount' not in exclude_set:
            raw_amount = getattr(self, 'expected_amount', None)
            if isinstance(raw_amount, bool):
                errors['expected_amount'] = [
                    ValidationError('Số tiền dự kiến không được sử dụng kiểu logic (boolean).', code='invalid')
                ]
            elif isinstance(raw_amount, float):
                errors['expected_amount'] = [
                    ValidationError('Số tiền dự kiến không được sử dụng kiểu số thực (float).', code='invalid')
                ]

        exclude_for_super = exclude_set | {'expected_amount'} if 'expected_amount' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}

        if self.order_id is None:
            errors['order'] = 'Đơn hàng không được để trống.'

        if not self.method:
            errors['method'] = 'Phương thức thanh toán không được để trống.'
        elif self.method not in PaymentMethod.values:
            errors['method'] = f'Phương thức thanh toán không hợp lệ: {self.method}.'

        if not self.status:
            errors['status'] = 'Trạng thái thanh toán không được để trống.'
        elif self.status not in PaymentStatus.values:
            errors['status'] = f'Trạng thái thanh toán không hợp lệ: {self.status}.'

        if self.expected_amount is None:
            errors['expected_amount'] = 'Số tiền dự kiến không được để trống.'
        elif isinstance(self.expected_amount, bool):
            errors['expected_amount'] = 'Số tiền dự kiến không được sử dụng kiểu logic (boolean).'
        elif isinstance(self.expected_amount, float):
            errors['expected_amount'] = 'Số tiền dự kiến không được sử dụng kiểu số thực (float).'
        elif not _is_finite_decimal(self.expected_amount):
            errors['expected_amount'] = 'Số tiền dự kiến không hợp lệ (phải là số Decimal hữu hạn).'
        elif self.expected_amount < Decimal('0.00'):
            errors['expected_amount'] = 'Số tiền dự kiến không được âm.'
        elif self.expected_amount.as_tuple().exponent < -2:
            errors['expected_amount'] = 'Số tiền dự kiến không được vượt quá 2 chữ số thập phân.'


        if errors:
            raise ValidationError(errors)


class RefundReasonType(models.TextChoices):
    CANCEL = 'CANCEL', 'Khách hàng hủy đơn hàng'
    RETURN = 'RETURN', 'Trả hàng hoàn tiền'
    EXCHANGE = 'EXCHANGE', 'Đổi hàng chênh lệch'
    FAILED_DELIVERY = 'FAILED_DELIVERY', 'Giao hàng thất bại sau khi xuất kho'


class RefundStatus(models.TextChoices):
    PENDING = 'PENDING', 'Chờ xử lý'
    PROCESSING = 'PROCESSING', 'Đang xử lý'
    COMPLETED = 'COMPLETED', 'Đã hoàn tất'
    REJECTED = 'REJECTED', 'Từ chối'


class Refund(models.Model):
    return_request = models.OneToOneField(
        'orders.ReturnRequest',
        on_delete=models.PROTECT,
        related_name='refund_record',
        null=True,
        blank=True,
        verbose_name='Yêu cầu trả hàng',
        help_text='Yêu cầu đổi/trả tạo ra hồ sơ hoàn tiền này (nếu có).',
    )
    order = models.ForeignKey(
        'orders.Order',
        on_delete=models.PROTECT,
        related_name='refunds',
        verbose_name='Đơn hàng',
        help_text='Đơn hàng phát sinh hoàn tiền (NOT NULL -> Order, on_delete=PROTECT bảo toàn chứng từ kế toán).',
    )
    reason_type = models.CharField(
        max_length=20,
        choices=RefundReasonType.choices,
        verbose_name='Lý do hoàn tiền',
        help_text='Lý do hoàn tiền: hủy đơn, trả hàng, đổi hàng chênh lệch hoặc giao hàng thất bại.',
    )
    amount = models.DecimalField(
        max_digits=14,
        decimal_places=2,
        verbose_name='Số tiền hoàn',
        help_text='Số tiền hoàn thực tế (NOT NULL, > 0, tối đa 14 chữ số và 2 chữ số thập phân).',
    )
    status = models.CharField(
        max_length=25,
        choices=RefundStatus.choices,
        default=RefundStatus.PENDING,
        verbose_name='Trạng thái hoàn tiền',
        help_text='Trạng thái xử lý hoàn tiền theo Bảng 20 (PENDING, PROCESSING, COMPLETED, REJECTED).',
    )
    bank_reference = models.CharField(
        max_length=100,
        null=True,
        blank=True,
        unique=True,
        verbose_name='Mã tham chiếu ngân hàng',
        help_text='Mã giao dịch ngân hàng chuyển tiền hoàn thực tế (UNIQUE nếu có, cho phép NULL).',
    )
    confirmed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name='confirmed_refunds',
        verbose_name='Quản trị viên xác nhận',
        help_text='Tài khoản Admin xác nhận việc hoàn tiền (SRS v2.3 BR-PAY-09: chỉ Admin có thẩm quyền).',
    )
    completed_at = models.DateTimeField(
        null=True,
        blank=True,
        verbose_name='Thời điểm hoàn tất',
        help_text='Thời điểm giao dịch chuyển tiền hoàn thành công theo xác nhận ngân hàng (NULL khi chưa hoàn).',
    )
    audit_note = models.TextField(
        blank=True,
        max_length=1000,
        verbose_name='Ghi chú kiểm toán',
        help_text='Ghi chú xác nhận nội bộ cho giao dịch hoàn tiền.',
    )

    class Meta:
        verbose_name = 'Bản ghi hoàn tiền'
        verbose_name_plural = 'Danh sách hoàn tiền'
        ordering = ['-id']
        constraints = [
            models.CheckConstraint(
                condition=models.Q(amount__gt=0),
                name='refund_amount_gt_0',
            ),
            models.CheckConstraint(
                condition=models.Q(reason_type__in=RefundReasonType.values),
                name='refund_reason_type_valid',
            ),
            models.CheckConstraint(
                condition=models.Q(status__in=RefundStatus.values),
                name='refund_status_valid',
            ),
            models.UniqueConstraint(
                models.functions.Upper('bank_reference'),
                condition=models.Q(bank_reference__isnull=False),
                name='unique_refund_bank_ref_upper',
            ),
        ]

    def __str__(self):
        ref = self.bank_reference or 'chưa có mã'
        return f"Hoàn tiền #{self.pk or 'mới'} [Mã GD: {ref}] - Đơn #{self.order_id} ({self.amount:,.0f} VNĐ - {self.get_reason_type_display()} - {self.get_status_display()})"

    def clean_fields(self, exclude=None):
        if exclude is None:
            exclude_set = set()
        else:
            exclude_set = set(exclude)

        errors = {}

        if 'amount' not in exclude_set:
            raw_amount = getattr(self, 'amount', None)
            if isinstance(raw_amount, bool):
                errors['amount'] = [
                    ValidationError('Số tiền hoàn không được sử dụng kiểu logic (boolean).', code='invalid')
                ]
            elif isinstance(raw_amount, float):
                errors['amount'] = [
                    ValidationError('Số tiền hoàn không được sử dụng kiểu số thực (float).', code='invalid')
                ]

        exclude_for_super = exclude_set | {'amount'} if 'amount' in errors else exclude_set
        try:
            super().clean_fields(exclude=exclude_for_super)
        except ValidationError as e:
            errors = e.update_error_dict(errors)

        if errors:
            raise ValidationError(errors)

    def clean(self):
        super().clean()
        errors = {}

        if self.order_id is None:
            errors['order'] = 'Đơn hàng không được để trống.'

        if not self.reason_type:
            errors['reason_type'] = 'Lý do hoàn tiền không được để trống.'
        elif self.reason_type not in RefundReasonType.values:
            errors['reason_type'] = f'Lý do hoàn tiền không hợp lệ: {self.reason_type}.'

        if self.amount is None:
            errors['amount'] = 'Số tiền hoàn không được để trống.'
        elif isinstance(self.amount, bool):
            errors['amount'] = 'Số tiền hoàn không được sử dụng kiểu logic (boolean).'
        elif isinstance(self.amount, float):
            errors['amount'] = 'Số tiền hoàn không được sử dụng kiểu số thực (float).'
        elif not _is_finite_decimal(self.amount):
            errors['amount'] = 'Số tiền hoàn không hợp lệ (phải là số Decimal hữu hạn).'
        elif self.amount <= Decimal('0.00'):
            errors['amount'] = 'Số tiền hoàn phải lớn hơn 0.'
        elif self.amount.as_tuple().exponent < -2:
            errors['amount'] = 'Số tiền hoàn không được vượt quá 2 chữ số thập phân.'

        if not self.status:
            errors['status'] = 'Trạng thái hoàn tiền không được để trống.'
        elif self.status not in RefundStatus.values:
            errors['status'] = f'Trạng thái hoàn tiền không hợp lệ: {self.status}.'

        if self.bank_reference is not None:
            cleaned_ref = str(self.bank_reference).strip()
            if cleaned_ref:
                self.bank_reference = cleaned_ref.upper()
                if len(self.bank_reference) > 100:
                    errors['bank_reference'] = 'Mã tham chiếu ngân hàng không được vượt quá 100 ký tự.'
            else:
                self.bank_reference = None

        if errors:
            raise ValidationError(errors)

    def save(self, *args, **kwargs):
        if self.bank_reference is not None:
            cleaned_ref = str(self.bank_reference).strip()
            self.bank_reference = cleaned_ref.upper() if cleaned_ref else None
        super().save(*args, **kwargs)
