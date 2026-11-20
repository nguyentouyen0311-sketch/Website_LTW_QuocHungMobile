from django.conf import settings
from django.db import models


class CustomerProfile(models.Model):

    GENDER_CHOICES = [
        ('MALE', 'Nam'),
        ('FEMALE', 'Nữ'),
        ('OTHER', 'Khác'),
    ]

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
    avatar = models.ImageField(
        upload_to='avatars/',
        blank=True,
        null=True,
        verbose_name='Ảnh đại diện',
    )
    date_of_birth = models.DateField(
        blank=True,
        null=True,
        verbose_name='Ngày sinh',
    )
    gender = models.CharField(
        max_length=10,
        blank=True,
        null=True,
        choices=GENDER_CHOICES,
        verbose_name='Giới tính',
    )
    is_verified = models.BooleanField(
        default=False,
        verbose_name='Đã xác thực',
    )

    class Meta:
        verbose_name = 'Hồ sơ khách hàng'
        verbose_name_plural = 'Hồ sơ khách hàng'

    def __str__(self):
        return f"Hồ sơ khách hàng: {self.user.username}"
