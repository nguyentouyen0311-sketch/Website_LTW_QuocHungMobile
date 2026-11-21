# Bàn giao chung Voucher, Cart và Chatbot

Nhánh nhận code: **`feature/back_end-coupons`**.

Cả ba module nằm trên cùng nhánh, không cần tìm hoặc cherry-pick từng lần cập nhật. Các commit trước của voucher (`9eeff00`) và cart (`f278731`) được giữ nguyên, chatbot được bổ sung tiếp trên nhánh này.

## Nội dung bàn giao

| Module | Nội dung chính |
| --- | --- |
| Voucher — `coupons/` | Quản lý mã giảm giá, điều kiện, trạng thái công bố và giao diện quản trị. |
| Cart — `cart/` | Giao diện storefront, quản lý số lượng/biến thể, chọn mục để xóa và kết nối điều hướng. Checkout tiếp tục xử lý toàn giỏ theo quy trình hiện có. |
| Chatbot — `chat/` | Bộ tri thức/từ khóa admin quản lý, câu hỏi gợi ý, sản phẩm và giá, chuyển nhân viên, hộp thư tự cập nhật, xuất/nhập dữ liệu và cửa sổ chat nổi. Không cần dịch vụ AI hoặc Ollama. |

Có các tệp tích hợp ngoài thư mục app, gồm template storefront và launcher chung. **Lấy toàn bộ nhánh**, không chỉ chép riêng ba thư mục app.

## Lấy code về để kiểm tra

Trong repo của nhóm, đảm bảo đã lưu công việc riêng trước khi chuyển nhánh:

```powershell
git fetch origin
git switch feature/back_end-coupons
git pull --ff-only origin feature/back_end-coupons
```

Nếu máy chưa có nhánh cục bộ:

```powershell
git switch --track origin/feature/back_end-coupons
```

Sau khi đã có môi trường ảo và các thư viện trong `requirements.txt`:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py setup_roles
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge
.\.venv\Scripts\python.exe manage.py runserver
```

Database của từng máy vẫn riêng. Lệnh `migrate` tạo/cập nhật bảng; lệnh `sync_chat_knowledge` nạp bộ tri thức được chia sẻ trong `chat/data/knowledge.json`. Chạy lại không tạo trùng hoặc tự ghi đè nội dung khác. Nếu máy chưa có tài khoản admin, tạo bằng `manage.py createsuperuser` theo README dự án.

Sản phẩm, voucher, tài khoản và hội thoại thực tế không được sao chép theo database máy người bàn giao. Nếu danh sách trống, hãy dùng dữ liệu sẵn có trên máy hoặc tạo dữ liệu kiểm tra qua các màn hình quản lý.

## Tích hợp vào main một lần

Tạo Pull Request trên GitHub:

- Base: **`main`**.
- Compare: **`feature/back_end-coupons`**.
- Phạm vi: **Voucher + Cart + Chatbot**.

Reviewer kiểm tra thay đổi và chạy:

```powershell
.\.venv\Scripts\python.exe manage.py check
.\.venv\Scripts\python.exe manage.py test chat cart coupons --noinput
.\.venv\Scripts\python.exe manage.py makemigrations chat --check --dry-run
```

Tại thời điểm bàn giao, bộ kiểm thử có **67 tests đạt**, kiểm tra Chrome của chatbot có **34 mục đạt**. Việc Git gộp không xung đột chỉ phản ánh hai đầu nhánh tại thời điểm kiểm tra; nếu `main` thay đổi tiếp, GitHub cần kiểm tra lại trước khi merge.

## Các trang cần nghiệm thu

- Giỏ hàng: `/cart/`.
- Quản trị voucher: dùng mục **Mã giảm giá** trên sidebar.
- Chat khách: `/chat/`, hoặc nút chat nổi trên storefront/cart.
- Hộp thư nhân viên: `/chat/staff/`.
- Bộ tri thức admin: `/chat/knowledge/`.
- Thử câu hỏi: `/chat/knowledge/test/`.

Admin cần kiểm tra và bổ sung tri thức thực tế của cửa hàng trước khi công bố. Dữ liệu khởi tạo có 4 mục công bố và 1 mục thông tin liên hệ để nháp. Chi tiết cách xuất/nhập và đồng bộ: `chat/README.md`; kết quả nghiệm thu và giới hạn đối chiếu Figma: `chat/ACCEPTANCE.md`.
