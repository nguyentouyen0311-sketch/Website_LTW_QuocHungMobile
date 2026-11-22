from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [('catalog', '0006_productimage_variant')]

    operations = [
        migrations.CreateModel(
            name='SiteBranding',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('logo', models.FileField(blank=True, help_text='Tải logo PNG, JPG, WEBP hoặc SVG. Bỏ chọn tệp hiện tại để xóa logo.', null=True, upload_to='branding/', verbose_name='Tệp logo')),
                ('logo_alt', models.CharField(default='Quốc Hùng Mobile', max_length=150, verbose_name='Mô tả logo')),
                ('updated_at', models.DateTimeField(auto_now=True, verbose_name='Cập nhật lần cuối')),
            ],
            options={'verbose_name': 'Logo thương hiệu', 'verbose_name_plural': 'Logo thương hiệu', 'db_table': 'catalog_sitebranding'},
        ),
    ]
