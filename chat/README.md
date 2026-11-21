# Chatbot và chăm sóc khách hàng

Module trả lời bằng nội dung và từ khóa do admin quản lý, không cần AI, API key hoặc Ollama. Không cung cấp tồn kho hay tra cứu đơn hàng riêng. Sản phẩm, biến thể và giá được đọc từ catalog đang công khai; không sửa dữ liệu catalog, cart, orders hoặc payments.

## Khởi tạo trên mỗi máy

Sau khi cài các thư viện sẵn có của project:

```powershell
.\.venv\Scripts\python.exe manage.py migrate
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge
```

Giữ nguyên lệnh `setup_roles` và cách tạo tài khoản của README dự án. Admin quản lý tri thức; Admin/Sales Staff hỗ trợ hội thoại; khách vãng lai được nhận diện bằng session; khách đăng nhập chỉ xem hội thoại của mình.

## Sử dụng

- Khách: `/chat/`.
- Nút hỗ trợ trên storefront/cart mở cửa sổ nổi rộng 360px; có thể thu nhỏ bằng nút đóng hoặc phím Escape. Bản trang riêng vẫn hoạt động khi không có JavaScript.
- Hộp thư nhân viên: `/chat/staff/`.
- Bộ tri thức admin: `/chat/knowledge/`.
- Thử câu hỏi: `/chat/knowledge/test/`. Không tạo hội thoại và chỉ dùng nội dung đã công bố.
- Từ khóa ngăn cách bằng dấu phẩy; nhận diện nguyên từ/cụm từ, không phân biệt hoa thường và dấu tiếng Việt.
- Nội dung có ưu tiên cao hơn thắng khi nhiều mục cùng khớp. Nếu cùng mức cao nhất, bot hỏi khách chọn chủ đề.
- Không có nguồn phù hợp hoặc khách yêu cầu gặp người thật: chuyển nhân viên. Một hội thoại do một nhân viên phụ trách; admin có thể kết thúc khi cần.
- Tin nhắn tự cập nhật định kỳ; khi lỗi mạng, bản nháp được giữ trong tab hiện tại và có nút thử lại.
- Mọi nội dung nhập là văn bản thuần, không thực thi HTML hoặc script.

## Đồng bộ bộ tri thức qua GitHub

1. Admin chỉnh nội dung trên giao diện, xuất JSON và dùng tệp đã duyệt thay `chat/data/knowledge.json`.
2. Chỉ commit code, migration và bộ tri thức đã kiểm tra. Không commit database, tài khoản, lịch sử chat hay thông tin khách hàng.
3. Thành viên kéo code rồi chạy `migrate` và `sync_chat_knowledge`.

Lần đầu tạo mục mới theo `knowledge_id` ổn định. Chạy lại không tạo trùng. Mặc định bỏ qua mục khác nội dung hiện tại. Để xem trước và chủ động cập nhật:

```powershell
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge --dry-run
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge --update-existing
```

Giao diện nhập JSON/CSV có xem trước và lựa chọn ghi đè. Bản xem trước hết hạn sau 15 phút; nếu dữ liệu thay đổi trong lúc xem trước thì phải kiểm tra lại. Không xóa các mục vắng trong tệp; hãy xuất trạng thái `is_active=false` để ngừng công bố đồng bộ. Tệp tối đa 2 MB và 200 mục/lần. Giữ `knowledge_id` khi sửa nội dung; nếu bỏ trống ID trong CSV, một ID mới được tạo và phải xuất lại để chia sẻ.

```powershell
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge duong-dan\knowledge.json
.\.venv\Scripts\python.exe manage.py sync_chat_knowledge --export knowledge-export.json
```

JSON là định dạng đồng bộ đầy đủ, giữ nguyên văn bản. CSV UTF-8 hỗ trợ Excel; khi xuất có bảo vệ các ô bắt đầu bằng ký tự công thức. JSON/CSV không chứa hội thoại hoặc tài khoản.

Giới hạn 2 MB / 200 mục áp dụng cho tải tệp trên web. Lệnh `sync_chat_knowledge` đọc tệp cục bộ không áp dụng giới hạn này, để có thể đồng bộ toàn bộ bộ tri thức khi dữ liệu lớn hơn.

Lệnh `seed_chat_faq` cũ được giữ tương thích và chuyển sang nạp bộ tri thức mới. Không phát hành địa chỉ hoặc chính sách mẫu chưa được xác minh. Khi cả nhóm chuyển sang database dùng chung, dùng cùng các màn hình này; không cần đổi cơ chế trả lời.

## Kiểm thử

```powershell
.\.venv\Scripts\python.exe manage.py test chat cart coupons
.\.venv\Scripts\python.exe manage.py makemigrations chat --check --dry-run
```

Kiểm tra Chrome tương tác trên Windows (cần Node.js và Chrome):

```powershell
node chat/tests/browser_review.cjs
```

Script tự tạo database, tài khoản và sản phẩm kiểm thử trong thư mục tạm mới, mở Chrome chạy nền, lưu báo cáo/ảnh rồi dừng các tiến trình của bài kiểm thử. Không dùng database hiện tại. Có thể chỉ định thư mục mới qua `CHAT_REVIEW_ROOT` và vị trí Chrome qua `CHROME_PATH`.

Giao diện tái sử dụng bộ màu và sidebar của voucher, bố cục hàng đợi + hội thoại + quản lý kịch bản theo hướng Figma node 37:3. Không có điểm tin cậy AI giả lập; thay bằng từ khóa và mức ưu tiên. Cần đối chiếu hình thức trực tiếp khi có quyền đọc đầy đủ Figma.

Hai tài liệu `docs` không có nội dung trong working tree khi triển khai. Đã đọc README từ Git commit `ae89e67` và DesignRule từ `15b93be`, cùng giao diện voucher/cart hiện có. Figma MCP giới hạn lượt đọc chi tiết, nên chưa xác nhận khớp từng pixel. Có bổ sung các block template ở `cart/base.html` để tái sử dụng header/footer và kết nối launcher chung; không sửa nghiệp vụ cart hoặc orders/checkout.
