from django.conf import settings
from django.db import migrations, models


def ensure_cod_compatible_data(apps, schema_editor):
    database = schema_editor.connection.alias
    payment = apps.get_model('payments', 'Payment')
    order = apps.get_model('orders', 'Order')
    refund = apps.get_model('payments', 'Refund')
    transaction = apps.get_model('payments', 'PaymentTransaction')
    reconciliation = apps.get_model('payments', 'PaymentReconciliation')
    if (
        payment.objects.using(database).exclude(method='COD').exists()
        or payment.objects.using(database).filter(status__in=['PENDING_VERIFY', 'EXPIRED']).exists()
        or order.objects.using(database).filter(
            order_status__in=['PENDING_PAYMENT', 'PENDING_RECONCILIATION', 'EXPIRED'],
        ).exists()
        or refund.objects.using(database).filter(reason_type='LATE_PAYMENT').exists()
        or transaction.objects.using(database).exists()
        or reconciliation.objects.using(database).exists()
    ):
        raise RuntimeError(
            'Database còn lịch sử thanh toán chuyển khoản. '
            'Hãy lưu trữ và xử lý dữ liệu này trước khi chuyển sang COD; '
            'migration đã dừng để bảo toàn dữ liệu.',
        )


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0006_cod_only'),
        ('payments', '0008_refund_audit_note'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.RunPython(ensure_cod_compatible_data, migrations.RunPython.noop),
        migrations.RemoveConstraint(
            model_name='paymentreconciliation',
            name='reconciliation_reason_valid',
        ),
        migrations.RemoveConstraint(
            model_name='paymentreconciliation',
            name='reconciliation_status_valid',
        ),
        migrations.RemoveConstraint(
            model_name='paymentreconciliation',
            name='unique_unresolved_reconciliation_per_payment',
        ),
        migrations.RemoveConstraint(
            model_name='paymenttransaction',
            name='payment_transaction_received_amount_gte_0',
        ),
        migrations.RemoveConstraint(
            model_name='paymenttransaction',
            name='unique_payment_tx_bank_ref_upper',
        ),
        migrations.RemoveConstraint(
            model_name='payment',
            name='payment_method_valid',
        ),
        migrations.RemoveConstraint(
            model_name='payment',
            name='payment_status_valid',
        ),
        migrations.RemoveConstraint(
            model_name='refund',
            name='refund_reason_type_valid',
        ),
        migrations.AlterField(
            model_name='payment',
            name='method',
            field=models.CharField(choices=[('COD', 'Thanh toán khi nhận hàng (COD)')], help_text='Phương thức thanh toán khi nhận hàng (COD).', max_length=10, verbose_name='Phương thức thanh toán'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='notes',
            field=models.TextField(blank=True, help_text='Ghi chú thanh toán COD hoặc thông tin bổ sung.', null=True, verbose_name='Ghi chú thanh toán'),
        ),
        migrations.AlterField(
            model_name='payment',
            name='status',
            field=models.CharField(choices=[('UNPAID', 'Chưa thanh toán'), ('PAID', 'Đã thanh toán'), ('FAILED', 'Thanh toán thất bại'), ('PENDING_REFUND', 'Chờ hoàn tiền'), ('REFUNDED', 'Đã hoàn tiền'), ('PARTIALLY_REFUNDED', 'Hoàn tiền một phần')], help_text='Trạng thái thanh toán độc lập (yêu cầu gán tường minh, không gán mặc định ngầm định).', max_length=30, verbose_name='Trạng thái thanh toán'),
        ),
        migrations.AlterField(
            model_name='refund',
            name='reason_type',
            field=models.CharField(choices=[('CANCEL', 'Khách hàng hủy đơn hàng'), ('RETURN', 'Trả hàng hoàn tiền'), ('EXCHANGE', 'Đổi hàng chênh lệch'), ('FAILED_DELIVERY', 'Giao hàng thất bại sau khi xuất kho')], help_text='Lý do hoàn tiền: hủy đơn, trả hàng, đổi hàng chênh lệch hoặc giao hàng thất bại.', max_length=20, verbose_name='Lý do hoàn tiền'),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(condition=models.Q(('method__in', ['COD'])), name='payment_method_valid'),
        ),
        migrations.AddConstraint(
            model_name='payment',
            constraint=models.CheckConstraint(condition=models.Q(('status__in', ['UNPAID', 'PAID', 'FAILED', 'PENDING_REFUND', 'REFUNDED', 'PARTIALLY_REFUNDED'])), name='payment_status_valid'),
        ),
        migrations.AddConstraint(
            model_name='refund',
            constraint=models.CheckConstraint(condition=models.Q(('reason_type__in', ['CANCEL', 'RETURN', 'EXCHANGE', 'FAILED_DELIVERY'])), name='refund_reason_type_valid'),
        ),
        migrations.RemoveField(
            model_name='paymenttransaction',
            name='payment',
        ),
        migrations.RemoveField(
            model_name='paymenttransaction',
            name='verified_by',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='customer_notified_at',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='expires_at',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='qr_reference',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='verified_at',
        ),
        migrations.RemoveField(
            model_name='payment',
            name='verified_by',
        ),
        migrations.DeleteModel(
            name='PaymentReconciliation',
        ),
        migrations.DeleteModel(
            name='PaymentTransaction',
        ),
    ]
