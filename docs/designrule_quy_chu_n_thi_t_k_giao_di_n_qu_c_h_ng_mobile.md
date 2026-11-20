# QUỐC HÙNG MOBILE — UI/UX DESIGN SYSTEM & RULES (`DesignRule.md`)

> **Mục đích tài liệu:** Tài liệu này định nghĩa toàn bộ quy chuẩn thiết kế (Design Tokens, Typography, Layout, Components và UX Patterns) cho cả hai phân hệ **Frontend (Storefront - Khách hàng)** và **Backend (Admin Portal - Quản trị)** của dự án website thương mại điện tử **Quốc Hùng Mobile**.
> **Dành cho AI Agent:** Mọi tác vụ sinh mã nguồn giao diện (HTML/CSS/Tailwind/Django Templates) **BẮT BUỘC** phải tuân thủ nghiêm ngặt các thông số và quy tắc trong tài liệu này để đảm bảo tính đồng nhất 100% trên toàn hệ thống.

---

## 1. TRIẾT LÝ & PHONG CÁCH THIẾT KẾ (DESIGN PHILOSOPHY)

* **Phong cách chủ đạo:** Modern Clean Tech E-Commerce (Thương mại điện tử công nghệ hiện đại, tối giản, tập trung vào sản phẩm và hành động chuyển đổi).
* **Đặc trưng thị giác:**
  * Sử dụng nền sáng trung tính (`#F8F9FA`) kết hợp với các khối nội dung (Cards) nền trắng tinh khiết (`#FFFFFF`), bo góc lớn mềm mại (`12px` - `16px`) và viền mảnh tinh tế (`1px solid #E5E7EB`) thay vì đổ bóng quá đậm.
  * Tạo điểm nhấn tương phản cao bằng sắc **Đỏ Thương Hiệu (`#E62E2D`)** cho các nút hành động chính (CTA), giá bán, badge giảm giá và trạng thái active.
  * Sử dụng sắc **Xanh Rêu Đậm / Charcoal Green (`#1F2A24`)** làm màu nền nhận diện cho khu vực Footer (Frontend), Sidebar điều hướng (Backend) và các thẻ Banner/Chatbot Header để tạo cảm giác vững chãi, cao cấp.

---

## 2. HỆ THỐNG MÀU SẮC (COLOR PALETTE & TOKENS)

AI Agent phải sử dụng chính xác các mã màu dưới đây (khuyến khích khai báo qua CSS Variables hoặc Tailwind Config):

### 2.1. Màu thương hiệu & Điểm nhấn (Brand & Accent Colors)
* **Primary Red (Đỏ chủ đạo):** `#E62E2D` — Dùng cho nút CTA chính (`Mua ngay`, `Thêm vào giỏ hàng`, `Lưu danh mục`, `+ Thêm danh mục mới`), giá bán hiện tại, badge `% giảm giá`, mục menu đang active ở Admin Sidebar, số đếm thông báo.
* **Primary Red Hover:** `#C82322` — Trạng thái hover của các nút màu đỏ.
* **Primary Red Soft (Nền đỏ nhạt):** `#FEF2F2` — Dùng làm nền cho badge giảm giá phụ hoặc highlight nhẹ.
* **Brand Dark / Forest Charcoal:** `#1F2A24` — Dùng làm nền cho Footer (Frontend), Sidebar trái (Admin Portal), Header của cửa sổ Chatbot, và thẻ Banner phụ (`Săn Voucher độc quyền`).
* **Brand Dark Secondary:** `#2A3730` — Dùng cho các thành phần phụ trên nền tối (ô nhập liệu footer, nút social).

### 2.2. Màu trung tính (Neutral Colors)
* **App Background (Nền trang tổng thể):** `#F8F9FA` — Nền xám rất nhạt giúp làm nổi bật các thẻ Card màu trắng.
* **Surface / Card Background:** `#FFFFFF` — Nền của Header, Product Cards, Bảng dữ liệu Admin, Form nhập liệu, Modal/Chatbot body.
* **Surface Muted (Nền khu vực ảnh/input phụ):** `#F3F4F6` — Khung chứa ảnh sản phẩm, ô tìm kiếm, trường input ở trạng thái `readonly`/`disabled`.
* **Border Default (Viền chuẩn):** `#E5E7EB` — Đường viền bao quanh Card, Input, Table row.
* **Text Primary (Văn bản chính):** `#111827` — Tiêu đề trang, tên sản phẩm, nội dung chính.
* **Text Secondary (Văn bản phụ):** `#6B7280` — Mô tả ngắn dưới tiêu đề, thông số kỹ thuật tóm tắt, nhãn (label) phụ.
* **Text Muted / Strikethrough:** `#9CA3AF` — Giá gốc gạch ngang, placeholder trong ô tìm kiếm/input.

### 2.3. Màu trạng thái (Semantic Colors)
* **Success / In-Stock (Còn hàng / Đang hiện):**
  * Text: `#059669` (hoặc `#15803D`)
  * Background: `#ECFDF5` (hoặc `#DCFCE7`)
  * Icon / Toggle Active: `#10B981`
* **Neutral Status / Hidden (Đang ẩn / Hết hàng):**
  * Text: `#6B7280`
  * Background: `#F3F4F6`
* **Warning / Rating Star (Đánh giá sao):** `#F59E0B`

---

## 3. HỆ THỐNG CHỮ VIẾT (TYPOGRAPHY)

* **Font Family:** Sử dụng bộ font không chân hiện đại, hỗ trợ tiếng Việt hoàn hảo: `'Inter', 'Plus Jakarta Sans', system-ui, -apple-system, sans-serif`.
* **Quy tắc phân cấp chữ (Type Scale):**
  * **Page Title (H1 - Admin / Hero Title):** `24px` - `32px`, `font-weight: 700` (Bold), `line-height: 1.25`, màu `#111827`.
  * **Section Heading (H2 - Trang chủ):** `20px` - `22px`, `font-weight: 700`, đi kèm dòng mô tả phụ `13px` màu `#6B7280` ngay bên dưới.
  * **Card / Product Title:** `14px` - `15px`, `font-weight: 600` (SemiBold), giới hạn tối đa 1-2 dòng (`line-clamp-1` hoặc `line-clamp-2`).
  * **Product Price (Giá bán):** `16px` - `18px`, `font-weight: 700`, màu `#E62E2D`. Giá cũ gạch ngang: `12px` - `13px`, `font-weight: 400`, màu `#9CA3AF`, `text-decoration: line-through`.
  * **Body & Form Input:** `14px`, `font-weight: 400`, màu `#111827`.
  * **Table Header / Micro Labels / Badges:** `11px` - `12px`, `font-weight: 600`, viết hoa (`uppercase`) đối với tiêu đề cột bảng Admin và các tag nhỏ trên Banner.

---

## 4. HÌNH KHỐI, BO GÓC & ĐỔ BÓNG (SHAPES, RADIUS & ELEVATION)

* **Border Radius (Bo góc):**
  * **Containers / Cards lớn / Bảng Admin / Form Panel:** `16px` (`rounded-2xl`).
  * **Product Cards / Category Box:** `12px` (`rounded-xl`).
  * **Buttons / Inputs / Select:** `8px` (`rounded-lg`).
  * **Filter Pills / Status Badges / Search Bar:** `9999px` (`rounded-full`) cho các thẻ trạng thái (`Còn hàng`, `Đang hiện`, `-12%`, bộ lọc danh mục ngang) và thanh tìm kiếm Header.
* **Borders & Shadows:**
  * Ưu tiên dùng viền mảnh `1px solid #E5E7EB` kết hợp bóng đổ siêu nhẹ `0 1px 3px rgba(0, 0, 0, 0.04)`.
  * Khi hover vào Product Card: nâng nhẹ thẻ (`transform: translateY(-2px)`), đổi màu viền sang `#D1D5DB` và tăng bóng đổ lên `0 10px 20px rgba(0, 0, 0, 0.06)`.
  * Cửa sổ nổi (Floating Chatbot): sử dụng bóng đổ sâu `0 20px 25px -5px rgba(0, 0, 0, 0.15)` để tách biệt hoàn toàn khỏi nội dung nền.

---

## 5. QUY CHUẨN GIAO DIỆN FRONTEND (STOREFRONT - KHÁCH HÀNG)

### 5.1. Cấu trúc Layout tổng thể
* **Container chính:** Căn giữa màn hình, `max-width: 1280px` (hoặc `1320px`), padding hai bên `16px` - `24px`.
* **Header (Thanh điều hướng trên cùng):**
  * Nền trắng `#FFFFFF`, dính cố định trên cùng (`sticky top-0 z-40`), có viền dưới `#E5E7EB`.
  * **Cột trái:** Logo thương hiệu (Biểu tượng khối chữ QH cách điệu nền đỏ `#E62E2D` + chữ `QUỐC HÙNG MOBILE` đậm).
  * **Cột giữa:** Thanh tìm kiếm bo tròn (`rounded-full`), nền `#F3F4F6`, nút kính lúp màu đỏ `#E62E2D` nằm gọn bên phải trong ô tìm kiếm. Ngay dưới thanh tìm kiếm là dải từ khóa gợi ý nhanh (`Điện thoại`, `Laptop`, `Tablet`, `Âm thanh`, `Phụ kiện`, `Khuyến mãi`), từ khóa đang chọn có màu đỏ đậm.
  * **Cột phải:** Các nút chức năng dạng icon + text ngang hàng (`Đơn hàng của tôi`, `Giỏ hàng` có badge đếm số, `Tài khoản`).

### 5.2. Hero Banner & Danh mục
* **Bento Grid Hero Section:** Chia tỷ lệ **2/3 bên trái** (Banner chính nền xám bạc/gradient sáng, tiêu đề sản phẩm Flagship lớn, giá ưu đãi, 2 nút CTA: `Mua ngay` nền đỏ và `Xem chi tiết` nền trắng viền xám, phía dưới có cam kết bảo hành) và **1/3 bên phải** (xếp chồng 2 thẻ ngang bằng nhau: Thẻ trên nền trắng cho *Sản phẩm giảm giá sốc*, Thẻ dưới nền tối `#1F2A24` chữ trắng cho *Săn Voucher độc quyền*).
* **Danh Mục Sản Phẩm:** Lưới 8 cột ngang (`grid-cols-8` trên desktop), mỗi danh mục là một thẻ vuông trắng bo góc `12px`, viền `#E5E7EB`, chứa icon đường nét tối giản ở giữa và tên danh mục bên dưới.

### 5.3. Quy chuẩn Thẻ Sản Phẩm (Product Card)
Mỗi Product Card phải tuân thủ cấu trúc dọc thống nhất từ trên xuống dưới:
1. **Top Badge:** Góc trên bên trái đặt pill badge nhỏ (`-12%` nền đỏ `#E62E2D` chữ trắng cho khu vực Flash Sale; hoặc `Còn hàng` nền xanh nhạt `#ECFDF5` chữ xanh `#059669` cho danh sách sản phẩm thường).
2. **Khung ảnh sản phẩm:** Tỷ lệ vuông `1:1`, nền `#F3F4F6`, bo góc `10px`, ảnh sản phẩm đặt chính giữa có padding hợp lý.
3. **Dòng đánh giá & thông số phụ:** Icon sao vàng `#F59E0B` + điểm số (`4.9`) + số lượt đánh giá `(231 đánh giá)` bên trái; phiên bản dung lượng/màu sắc (`256GB • 512GB`) căn phải, chữ nhỏ `11px` màu `#6B7280`.
4. **Tên sản phẩm & Mô tả kỹ thuật ngắn:** Tên sản phẩm in đậm `#111827`, ngay dưới là 1-2 dòng tóm tắt cấu hình (`Chip A18 Pro • Màn hình 6.9" Super Retina`) màu `#6B7280` cỡ `12px`.
5. **Khu vực giá:** Giá bán chính màu đỏ `#E62E2D` cỡ lớn (`17px` Bold) nằm cùng hàng với giá gốc gạch ngang màu xám `#9CA3AF`.
6. **Nút hành động (Bottom CTA):** Chiếm `100%` chiều rộng thẻ (`w-full`), bo góc `8px`, cao `38px`:
   * Đối với khu vực *Ưu đãi nổi bật*: Nút nền đỏ `#E62E2D`, chữ trắng, kèm icon giỏ hàng (`Thêm vào giỏ hàng`).
   * Đối với khu vực *Sản phẩm nổi bật*: Nút nền trắng `#FFFFFF`, viền `#E5E7EB`, chữ đen `#111827` (`Xem chi tiết >`).

### 5.4. Trợ lý ảo AI (Floating Chatbot Widget)
* **Nút kích hoạt:** Nằm cố định góc dưới bên phải (`fixed bottom-6 right-6`), hình tròn/bo góc màu đỏ `#E62E2D` với icon hội thoại trắng.
* **Cửa sổ chat:** Rộng `360px`, bo góc `16px`, đổ bóng lớn.
  * **Header:** Nền xanh rêu đậm `#1F2A24`, logo QH, tên `Trợ lý Quốc Hùng Mobile`, nút thu nhỏ/đóng.
  * **Body:** Tin nhắn của Bot nền xám nhạt `#F3F4F6` căn trái (kèm avatar QH nhỏ); tin nhắn của Khách nền đỏ `#E62E2D` chữ trắng căn phải. Hỗ trợ nhúng thẻ gợi ý sản phẩm mini (ảnh + tên + nút `Xem chi tiết` đỏ) và các chip gợi ý câu hỏi nhanh phía trên ô nhập liệu.

### 5.5. Service Strip & Footer
* **Thanh cam kết dịch vụ (Service Strip):** 4 cột ngang nền trắng bo góc `12px`, viền `#E5E7EB` nằm ngay trên Footer (Icon khung vuông xám nhạt bên trái + Tiêu đề đậm & mô tả ngắn bên phải).
* **Footer:** Nền `#1F2A24` toàn chiều rộng, chia 4 cột (Thông tin thương hiệu & Hotline/Địa chỉ Đà Nẵng; Hỗ trợ khách hàng; Danh mục & Thông tin; Kết nối mạng xã hội & Chứng nhận). Dòng bản quyền dưới cùng ngăn cách bởi đường kẻ mờ `#2E3B34`.

---

## 6. QUY CHUẨN GIAO DIỆN BACKEND (ADMIN PORTAL - QUẢN TRỊ)

### 6.1. Cấu trúc Layout 2 Vùng (Fixed Sidebar + Fluid Workspace)
* **Sidebar trái:** Chiều rộng cố định `260px` (`w-64`), chiều cao `100vh` (`fixed left-0 top-0`), nền `#1F2A24`.
  * **Đỉnh Sidebar:** Logo QH + chữ `QUỐC HÙNG MOBILE` trắng và dòng phụ đề `ADMIN PORTAL` màu xám nhạt.
  * **Menu điều hướng:** Chia thành các nhóm rõ ràng với tiêu đề nhóm chữ in hoa nhỏ màu xám (`TỔNG QUAN`, `QUẢN LÝ HÀNG HÓA`, `BÁN HÀNG & CSKH`, `HỆ THỐNG`).
  * **Trạng thái Active:** Mục đang được chọn có nền đỏ `#E62E2D`, chữ trắng đậm, bo góc `8px` (`rounded-lg`), tạo điểm nhấn thị giác cực kỳ rõ ràng. Các mục có thông báo mới (như `Quản lý đơn hàng`) hiển thị badge tròn màu đỏ bên phải.
  * **Đáy Sidebar:** Thông tin tài khoản đang đăng nhập (Avatar tròn chữ cái viết tắt, Họ tên, Vai trò `Administrator` và nút icon Đăng xuất).
* **Khu vực làm việc chính (Bên phải Sidebar):**
  * **Top Header Bar:** Nền trắng `#FFFFFF`, cao `64px`, viền dưới `#E5E7EB`. Bên trái có nút thu gọn menu + Breadcrumb (`Quản lý hàng hóa / Quản lý danh mục sản phẩm`). Bên phải có ô `Tìm kiếm nhanh...` bo tròn và badge vai trò `ADMINISTRATOR` nền đen `#111827` chữ trắng.
  * **Vùng nội dung (Main Content):** Nền `#F8F9FA`, padding `24px - 32px`.

### 6.2. Tiêu đề trang & Khu vực công cụ (Page Header)
* Bố cục Flex ngang (`justify-between items-center`):
  * **Bên trái:** Tiêu đề trang lớn (`24px` Bold) + mô tả ngắn chức năng bên dưới (`14px` màu `#6B7280`).
  * **Bên phải:** Nút hành động tạo mới chính nền đỏ `#E62E2D`, chữ trắng, có icon dấu `+` (ví dụ: `+ Thêm danh mục mới`).

### 6.3. Bố cục Master-Detail / Split-View (Danh sách kết hợp Form nhanh)
Đối với các màn hình quản lý danh mục, thuộc tính, mã giảm giá..., áp dụng bố cục chia 2 cột trên nền `#F8F9FA`:
* **Cột trái (Chiếm ~65% - 70% chiều rộng): Khối Bảng Dữ Liệu (Data Table Card)**
  * Bọc trong Card trắng `#FFFFFF`, bo góc `16px`, viền `#E5E7EB`, padding `20px`.
  * **Thanh công cụ trên bảng:** Ô tìm kiếm theo tên (`rounded-lg`, nền `#F8F9FA`), Dropdown lọc trạng thái (`Trạng thái: Tất cả`), và dòng thống kê tổng số bản ghi bên phải (`Tổng cộng: 7 danh mục`).
  * **Bảng dữ liệu (Table):**
    * Header bảng: Chữ in hoa cỡ `11px`, `font-weight: 600`, màu `#9CA3AF`, không dùng nền màu đậm, ngăn cách với nội dung bằng đường kẻ ngang mảnh `#F3F4F6`.
    * Các dòng dữ liệu (`tr`): Padding trên dưới thoáng (`16px`), ngăn cách bởi viền dưới `#F3F4F6`. Mã định danh (`DM01`, `SP01`...) dùng font monospace hoặc đậm vừa; Tên chính in đậm `#111827` kèm dòng mô tả nhỏ `#6B7280` ngay bên dưới.
    * Cột trạng thái: Dùng pill badge bo tròn (`Đang hiện` nền xanh nhạt `#ECFDF5` chữ xanh `#059669`; `Đang ẩn` nền xám `#F3F4F6` chữ xám `#6B7280`).
    * Cột thao tác: Các icon tối giản xếp ngang (Icon trạng thái/tích xanh, icon Sửa hình cây bút, icon Ẩn/Hiện hình con mắt) màu `#6B7280`, đổi màu khi hover.
* **Cột phải (Chiếm ~30% - 35% chiều rộng): Khối Form Nhập Liệu (Action Form Card)**
  * Bọc trong Card trắng `#FFFFFF`, bo góc `16px`, viền `#E5E7EB`, padding `24px`.
  * **Header Form:** Tiêu đề động (`Thêm / Cập nhật danh mục`) kèm badge chỉ báo chế độ góc phải (`CHẾ ĐỘ THÊM MỚI` viền xám chữ nhỏ in hoa).
  * **Các trường nhập liệu (Form Fields):**
    * Nhãn (Label) nằm trên ô nhập, chữ `13px` `font-weight: 600`, dấu sao đỏ `*` cho trường bắt buộc.
    * Input/Textarea: Bo góc `8px`, viền `#E5E7EB`, padding `10px 12px`, khi focus chuyển viền sang `#E62E2D` hoặc `#111827`. Trường mã tự động sinh đặt nền `#F3F4F6` và khóa chỉnh sửa.
    * Khối bật/tắt trạng thái: Bọc trong khung viền `#E5E7EB` bo góc `8px`, nhãn bên trái và nút gạt **Toggle Switch** màu xanh lá `#10B981` bên phải.
  * **Nút hành động cuối Form:** Chia 2 nút ngang hàng: Nút phụ bên trái (`Làm mới` nền trắng viền xám) và Nút chính bên phải (`✓ Lưu danh mục` nền đỏ `#E62E2D` chữ trắng).

---

## 7. CHECKLIST KIỂM TRA DÀNH CHO AI AGENT TRƯỚC KHI XUẤT CODE

1. **Đúng mã màu chưa?** Có sử dụng đúng đỏ `#E62E2D` cho CTA/Active và xanh rêu đen `#1F2A24` cho Sidebar/Footer không?
2. **Đúng độ bo góc chưa?** Card lớn bo `16px`, Product Card bo `12px`, Button/Input bo `8px`, Badge/Pill bo `9999px`?
3. **Tránh lạm dụng đổ bóng:** Đã sử dụng nền `#F8F9FA` kết hợp Card `#FFFFFF` và viền `1px solid #E5E7EB` thay vì đổ bóng đen nặng nề chưa?
4. **Tính nhất quán tiếng Việt & Dữ liệu mẫu:** Mọi nhãn giao diện, thông báo, định dạng tiền tệ (`32.490.000đ` - dấu chấm phân cách hàng nghìn, chữ `đ` viết thường liền sau số) đã chuẩn hóa theo thị trường Việt Nam chưa?
5. **Responsive:** Trên màn hình nhỏ (Mobile/Tablet), lưới sản phẩm tự động co về `2 cột` (Mobile) / `3 cột` (Tablet), và bố cục Split-View ở Admin tự động xếp chồng dọc (`flex-col`) không bị vỡ giao diện?
