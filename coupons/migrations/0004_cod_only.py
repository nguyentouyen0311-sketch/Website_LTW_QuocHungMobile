from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('coupons', '0003_coupon_is_public'),
    ]

    operations = [
        migrations.AlterField(
            model_name='couponusage',
            name='status',
            field=models.CharField(choices=[('RESERVED', 'Giữ chỗ'), ('USED', 'Đã sử dụng'), ('RELEASED', 'Đã giải phóng')], help_text='RESERVED (giữ chỗ), USED (đã áp dụng thành công), RELEASED (đã giải phóng). Trạng thái cần được gán tường minh.', max_length=12, verbose_name='Trạng thái sử dụng'),
        ),
    ]
