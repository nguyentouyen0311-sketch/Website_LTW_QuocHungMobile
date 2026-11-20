from django.apps import AppConfig


class InventoryConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'inventory'
    verbose_name = 'Quản lý Kho hàng'

    def ready(self):
        # Kết nối signals khi ứng dụng đã sẵn sàng
        # Tuyệt đối không thực thi câu lệnh truy vấn CSDL tại đây để bảo đảm an toàn khi chạy migrations
        import inventory.signals  # noqa: F401

