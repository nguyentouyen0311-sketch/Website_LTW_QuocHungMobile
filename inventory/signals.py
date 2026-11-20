"""
Bộ xử lý tín hiệu (Signals) cho phân hệ Quản lý Kho hàng (Inventory) — Quốc Hùng Mobile.
Tự động đảm bảo mỗi biến thể (ProductVariant) luôn có đúng một bản ghi tồn kho (Inventory)
ngay khi được khởi tạo thành công.
"""
from django.db.models.signals import post_save
from django.dispatch import receiver


@receiver(post_save, sender='catalog.ProductVariant')
def auto_create_variant_inventory(sender, instance, created, raw=False, **kwargs):
    """
    Tự động khởi tạo đúng một bản ghi Inventory với giá trị mặc định bằng 0
    cho từng biến thể sản phẩm mới được tạo trong hệ thống.

    Nguyên tắc kỹ thuật:
    1. raw=True: Bỏ qua khi thực hiện loaddata hoặc khôi phục dữ liệu thô.
    2. created=False: Bỏ qua khi cập nhật biến thể cũ (không tạo thêm hoặc ghi đè).
    3. get_or_create: Đảm bảo tính lũy thừa (idempotent), không gây lỗi trùng lặp OneToOne.
    4. Giao dịch nguyên tử: Khi variant được tạo trong transaction (qua ProductVariantForm
       hoặc ProductVariantAdmin), tín hiệu thực thi trong cùng transaction đó; nếu việc
       tạo variant gặp lỗi/rollback, bản ghi Inventory cũng được rollback tự động.
    """
    if raw:
        return

    if created:
        from inventory.models import Inventory
        Inventory.objects.get_or_create(
            variant=instance,
            defaults={
                'physical_sellable': 0,
                'reserved': 0,
                'min_threshold': 0,
            },
        )

