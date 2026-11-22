from django.db import migrations, models
from django.db.models import Q


class Migration(migrations.Migration):
    dependencies = [('cart', '0002_cart_unique_constraints')]

    operations = [
        migrations.AddField(model_name='cart', name='is_direct_checkout', field=models.BooleanField(db_index=True, default=False)),
        migrations.RemoveConstraint(model_name='cart', name='unique_active_user_cart'),
        migrations.AddConstraint(
            model_name='cart',
            constraint=models.UniqueConstraint(condition=Q(is_direct_checkout=False, user__isnull=False), fields=('user',), name='unique_active_user_cart'),
        ),
    ]
