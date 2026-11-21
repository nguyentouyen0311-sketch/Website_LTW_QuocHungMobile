import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ('payments', '0005_refund'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='CODCollection',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('amount', models.DecimalField(decimal_places=2, max_digits=14)),
                ('reference', models.CharField(max_length=100, unique=True)),
                ('collected_at', models.DateTimeField()),
                ('note', models.CharField(blank=True, max_length=500)),
                ('collected_by', models.ForeignKey(on_delete=django.db.models.deletion.PROTECT, related_name='cod_collections', to=settings.AUTH_USER_MODEL)),
                ('payment', models.OneToOneField(on_delete=django.db.models.deletion.PROTECT, related_name='cod_collection', to='payments.payment')),
            ],
            options={
                'verbose_name': 'Chứng từ thu COD',
                'verbose_name_plural': 'Chứng từ thu COD',
                'constraints': [models.CheckConstraint(condition=models.Q(('amount__gt', 0)), name='cod_collection_positive')],
            },
        ),
    ]
