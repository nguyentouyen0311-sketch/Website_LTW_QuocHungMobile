# Bàn giao nâng cấp chatbot — 21/11/2026

## Phạm vi đã chốt và triển khai

- Trả lời theo bộ tri thức và từ khóa admin công bố. Không yêu cầu mô hình AI, dịch vụ ngoài hoặc thư viện mới.
- Nhận diện tiếng Việt có/không dấu, chữ hoa/thường, nguyên từ/cụm từ. Mức ưu tiên giải quyết các mục cùng khớp; nếu cùng ưu tiên thì hỏi khách chọn chủ đề.
- Tra cứu sản phẩm đang công khai và giá từng biến thể từ catalog; không đọc hay trả số lượng tồn kho. Không truy cập đơn hàng riêng của khách.
- Admin thêm/sửa, lưu nháp/công bố, tìm/lọc, xuất JSON/CSV, nhập có xem trước và xác nhận ghi đè, thử câu hỏi không tạo lịch sử chat.
- Khách gửi tin, chọn câu hỏi, gặp nhân viên và kết thúc; nhân viên nhận/trả lời/kết thúc hội thoại. Tự cập nhật tin nhắn và hàng đợi. Giữ bản nháp theo tab/tài khoản/hội thoại; chỉ xóa bản nháp sau khi nhận diện tin đã được lưu.
- JSON có knowledge_id ổn định để chia sẻ qua GitHub. Nạp lại không tạo trùng, mặc định không ghi đè mục khác nội dung. CLI có tùy chọn cập nhật rõ ràng. Chưa cấu hình database chung.
- Giao diện admin giữ sidebar và bộ màu voucher; hàng đợi + hội thoại, bảng tri thức ở dưới và các tab quản lý. Storefront dùng header/footer cart; launcher mở cửa sổ chat nổi có thu nhỏ, hỗ trợ màn hình nhỏ và trang riêng.

## Phạm vi file

Chức năng mới nằm trong `chat/`: migration 0002, engine, dịch vụ tri thức, forms, workspace views, templates, CSS/JS, dữ liệu chuẩn, lệnh đồng bộ và kiểm thử.

Hai điểm nối giao diện ngoài module:

- `cart/templates/cart/base.html`: thêm block stylesheet/script/launcher để chat tái sử dụng header/footer; nối launcher dùng chung.
- `templates/includes/chat_launcher.html`: dùng launcher đỏ và mở cửa sổ nổi.

Không thay nghiệp vụ cart, catalog, kho, orders, checkout hoặc payments. Không thay requirements/settings, không reset database, không sửa migration cũ.

## Kết quả kiểm tra

- `manage.py test chat cart coupons --noinput`: 67 tests đạt; bao gồm phân quyền, cách ly hội thoại, CSRF/POST, giữ dữ liệu nhập sai, escape HTML, FAQ/giá, chuyển nhân viên, nhập/xuất, cập nhật có chủ đích và hồi quy cart/coupons.
- Ba bài kiểm tra liên quan HTML tin nhắn, chat nhúng và polling đã chạy lại sau thay đổi giữ bản nháp: đạt.
- Chrome headless với Django và database riêng: 34 mục đạt. Có gửi/nhận thật giữa khách và nhân viên, hỏi giá, chọn chủ đề, quản trị tri thức, nhập tệp qua xem trước/xác nhận, launcher trên cart, chat nhúng, giữ bản nháp và các viewport 320/375/768/1024/1280px.
- Migration được kiểm tra với hai FAQ cũ: giữ nội dung và tạo hai UUID riêng.
- Django system check, kiểm tra thiếu migration và kiểm tra khoảng trắng Git: đạt.
- Đã sao lưu database thực tế trước khi áp dụng `chat.0002`; số lượng hội thoại/tin nhắn giữ nguyên. Database hiện có 5 mục tri thức, 4 công bố, 1 nháp. Các đường dẫn khách/trang nhúng/polling trả 200 trong HTTP client trên database hiện tại.

## Tài liệu và giới hạn nghiệm thu

- Figma: https://www.figma.com/design/N0DC53E7PIedlpqd85399O/LTW?node-id=37-2
- Hai file docs được nêu không có nội dung trong working tree lúc triển khai. Đã tìm và đọc README tại `ae89e67` và DesignRule tại `15b93be` trong lịch sử Git, kết hợp giao diện voucher/cart hiện có.
- Figma MCP hết lượt đọc chi tiết. Đã đọc được cấu trúc bố cục, nhưng chưa xác nhận khớp hình thức từng pixel với Figma. Không quy đổi kết quả này thành phần trăm giống thiết kế.
- Bot phụ thuộc độ đầy đủ của từ khóa/nội dung admin cung cấp; không suy luận tự do. Giá lấy tại thời điểm trả lời; tin nhắn cũ giữ nguyên giá đã trả lời tại thời điểm đó.
- Thông tin liên hệ/giờ mở cửa đang ở bản nháp để admin điền dữ liệu xác minh. Cần duyệt và bổ sung bộ tri thức thực tế của cửa hàng trước khi vận hành chính thức.
- Đợt nâng cấp được bàn giao trên nhánh `feature/back_end-coupons`, cùng lịch sử voucher và cart. Xem `TEAM_HANDOFF_VOUCHER_CART_CHATBOT.md` ở thư mục gốc để tích hợp cả ba module.

## Mở để kiểm tra

- Khách: http://127.0.0.1:8000/chat/
- Hộp thư: http://127.0.0.1:8000/chat/staff/
- Tri thức: http://127.0.0.1:8000/chat/knowledge/
- Thử câu hỏi: http://127.0.0.1:8000/chat/knowledge/test/

Xem `chat/README.md` để biết cách thiết lập máy khác, đồng bộ dữ liệu và chạy kiểm thử.
