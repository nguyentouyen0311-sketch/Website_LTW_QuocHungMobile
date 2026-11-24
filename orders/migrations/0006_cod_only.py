from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('orders', '0005_returnrequest_completion_audit'),
    ]

    operations = [
        migrations.AlterField(
            model_name='order',
            name='order_code',
            field=models.CharField(help_text='Mã đơn hàng duy nhất hệ thống (NOT NULL, UNIQUE; tối đa 30 ký tự).', max_length=30, unique=True, verbose_name='Mã đơn hàng'),
        ),
        migrations.AlterField(
            model_name='order',
            name='order_status',
            field=models.CharField(choices=[('PENDING_CONFIRMATION', 'Chờ xác nhận'), ('CONFIRMED', 'Đã xác nhận'), ('SHIPPING', 'Đang giao hàng'), ('DELIVERED', 'Giao hàng thành công'), ('CANCELLED', 'Đã hủy'), ('DELIVERY_FAILED', 'Giao hàng thất bại'), ('RETURNING_TO_WAREHOUSE', 'Đang chuyển hoàn về kho'), ('FAILED_CLOSED', 'Đóng đơn giao thất bại')], help_text='Trạng thái đơn hàng theo quy trình xử lý đơn COD; cần gán tường minh.', max_length=30, verbose_name='Trạng thái đơn hàng'),
        ),
    ]
