from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):
    dependencies = [
        ('catalog', '0008_productpresentation'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductReview',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reviewer_name', models.CharField(max_length=120, verbose_name='Tên người đánh giá')),
                ('rating', models.PositiveSmallIntegerField(verbose_name='Số sao')),
                ('variant_text', models.CharField(blank=True, max_length=160, verbose_name='Phân loại đã mua')),
                ('content', models.TextField(verbose_name='Nội dung đánh giá')),
                ('is_verified_purchase', models.BooleanField(default=False, verbose_name='Đã mua hàng')),
                ('store_reply', models.TextField(blank=True, verbose_name='Phản hồi của cửa hàng')),
                ('helpful_count', models.PositiveIntegerField(default=0, verbose_name='Lượt hữu ích')),
                ('is_visible', models.BooleanField(default=True, verbose_name='Hiển thị công khai')),
                ('created_at', models.DateTimeField(auto_now_add=True, verbose_name='Ngày đăng')),
                ('product', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='reviews', to='catalog.product', verbose_name='Sản phẩm')),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='product_reviews', to=settings.AUTH_USER_MODEL, verbose_name='Khách hàng')),
            ],
            options={'verbose_name': 'Đánh giá sản phẩm', 'verbose_name_plural': 'Đánh giá sản phẩm', 'ordering': ['-created_at', '-id']},
        ),
        migrations.CreateModel(
            name='ProductReviewMedia',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('file', models.FileField(upload_to='reviews/%Y/%m/', verbose_name='Ảnh hoặc video')),
                ('sort_order', models.PositiveIntegerField(default=0, verbose_name='Thứ tự')),
                ('review', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='media', to='catalog.productreview', verbose_name='Đánh giá')),
            ],
            options={'verbose_name': 'Tệp đính kèm đánh giá', 'verbose_name_plural': 'Tệp đính kèm đánh giá', 'ordering': ['sort_order', 'id']},
        ),
    ]
