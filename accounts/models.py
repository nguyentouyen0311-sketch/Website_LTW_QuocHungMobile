from django.conf import settings
from django.db import models


class CustomerProfile(models.Model):

    class MembershipTier(models.TextChoices):
        SILVER = 'SILVER', 'Thành viên QH Silver'
        GOLD = 'GOLD', 'Thành viên QH Gold'
        PLATINUM = 'PLATINUM', 'Thành viên QH Platinum'

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name='customer_profile',
        verbose_name='Tài khoản người dùng',
    )
    phone = models.CharField(
        max_length=15,
        blank=True,
        null=True,
        verbose_name='Số điện thoại',
    )
    default_address = models.TextField(
        blank=True,
        null=True,
        verbose_name='Địa chỉ mặc định',
    )
    avatar = models.ImageField(upload_to='avatars/%Y/%m/', blank=True, null=True, verbose_name='Ảnh đại diện')
    date_of_birth = models.DateField(blank=True, null=True, verbose_name='Ngày sinh')
    gender = models.CharField(
        max_length=10, blank=True, choices=[('male', 'Nam'), ('female', 'Nữ'), ('other', 'Khác')],
        verbose_name='Giới tính'
    )
    customer_code = models.CharField(max_length=20, unique=True, blank=True, null=True, verbose_name='Mã khách hàng')
    membership_tier = models.CharField(
        max_length=12, choices=MembershipTier.choices, default=MembershipTier.GOLD,
        verbose_name='Hạng thành viên'
    )
    is_verified = models.BooleanField(default=True, verbose_name='Hồ sơ đã xác thực')

    class Meta:
        verbose_name = 'Hồ sơ khách hàng'
        verbose_name_plural = 'Hồ sơ khách hàng'

    def __str__(self):
        return f"Hồ sơ khách hàng: {self.user.username}"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.customer_code:
            self.customer_code = f'KH{self.user_id:08d}'
            type(self).objects.filter(pk=self.pk).update(customer_code=self.customer_code)
