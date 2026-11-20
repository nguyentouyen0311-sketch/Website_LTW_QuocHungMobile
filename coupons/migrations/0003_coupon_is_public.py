from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('coupons', '0002_couponusage'),
    ]

    operations = [
        migrations.AddField(
            model_name='coupon',
            name='is_public',
            field=models.BooleanField(
                default=False,
                verbose_name='Công bố mã giảm giá',
                help_text='Cho phép giới thiệu mã công khai; độc lập với trạng thái kích hoạt.',
            ),
        ),
    ]
