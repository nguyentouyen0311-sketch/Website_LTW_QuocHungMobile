# Yêu cầu giữ chức năng khi tích hợp code

Người dùng yêu cầu giữ tính năng cập nhật tồn kho hiện có khi pull hoặc merge; các phần khác tiếp tục dùng cấu trúc và giao diện mới của dự án.

- Giữ luồng cập nhật nhanh trên danh sách: chọn biến thể, cấp token cho từng dòng, nhập kho/kiểm kê rồi quay về danh sách với bộ lọc, trang và dòng đã chọn.
- Giữ các URL và import công khai của `inventory.views`, `inventory.forms`, `inventory.services` để những module khác tiếp tục hoạt động.
- Nhập kho cộng số lượng nguyên dương; kiểm kê yêu cầu lý do, không được thấp hơn lượng hàng đang giữ; thay đổi ngưỡng cảnh báo không được đổi số lượng tồn.
- Giữ transaction, token gắn với tài khoản và biến thể, chống gửi lặp, phát hiện snapshot cũ và lịch sử bất biến. Mỗi cập nhật hợp lệ chỉ ghi một giao dịch.
- Khi có conflict, tích hợp theo các hành vi trên; không chọn toàn bộ bản cũ cho cả file hoặc cấu hình Git tự bỏ qua thay đổi từ nhánh mới.
- Chạy `python manage.py test inventory --noinput` và các kiểm thử orders/cart liên quan trước khi hoàn tất. Dùng database kiểm thử hoặc bản sao để xác minh thao tác ghi kho.

Các kiểm thử trong `inventory/tests.py` là hợp đồng hành vi cần giữ khi thay đổi giao diện hoặc tách module.
