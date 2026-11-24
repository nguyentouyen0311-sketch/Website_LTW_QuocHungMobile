from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('catalog', '0011_product_review_order_item'),
    ]

    operations = [
        migrations.AddField(
            model_name='productvariant',
            name='deleted_at',
            field=models.DateTimeField(blank=True, null=True, verbose_name='Thời điểm xóa mềm'),
        ),
        migrations.AddField(
            model_name='productvariant',
            name='is_deleted',
            field=models.BooleanField(default=False, help_text='Biến thể xóa mềm được giữ lại để bảo toàn lịch sử kho, đơn hàng và đổi trả.', verbose_name='Đã xóa mềm'),
        ),
    ]
