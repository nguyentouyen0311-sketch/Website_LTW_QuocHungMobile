# Cập nhật giao diện giỏ hàng riêng

Nhánh: `codex/cart-interface-update`. Dựa trên `main` tại commit `a1a0c36`; không mang theo cập nhật voucher hoặc backend chatbot.

Giao diện /cart/ dùng template app `cart/detail.html`, có chọn tất cả/từng mục để xóa, cập nhật số lượng, đổi phiên bản, tổng tiền và thanh toán toàn giỏ. Không có ô nhập voucher trên Cart. Dữ liệu sản phẩm, giá và ảnh tiếp tục lấy từ database/media của project nhận.

Phạm vi thay đổi là `cart/` và tệp nối `templates/storefront/cart_detail.html`. Không sửa model/migration của app khác, không ghi đè cấu hình hoặc database. Nút hỗ trợ mở trang chat hiện có, không yêu cầu cài bản chatbot mới.

## Lấy và tích hợp

Tạo PR từ `codex/cart-interface-update` vào `main`. Sau khi review và merge, máy nhận phải cập nhật nhánh chứa PR này và khởi động lại đúng server. Kết nối GitHub trong ChatGPT/Codex không tự đưa commit vào mọi nhánh đang chạy.

Nếu project nhận vẫn ở `feature/storefront-admin-sync`, cần tích hợp bản cập nhật này vào nhánh đó, giữ phần việc riêng và giải quyết xung đột ở `cart/views.py` và template nếu xuất hiện. Không chọn mù toàn bộ một bên.

## Kiểm tra sau cập nhật

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test cart --noinput
.\.venv\Scripts\python.exe manage.py runserver
```

Mở `/cart/`, Ctrl+F5. View phải render `cart/detail.html`, tải `cart/css/cart.css` và `cart/js/cart.js`. Khác sản phẩm/giá/ảnh giữa các máy là bình thường nếu dữ liệu riêng. Không cần xóa hoặc tạo lại database để đổi giao diện.

Nhánh gộp đủ ba module vẫn là `feature/back_end-coupons`; nhánh này chỉ phục vụ tích hợp Cart riêng.
