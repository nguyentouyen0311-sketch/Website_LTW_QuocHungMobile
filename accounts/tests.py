import io
from datetime import date, timedelta
from decimal import Decimal
from PIL import Image

from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from accounts.models import CustomerProfile
from accounts.permissions import ROLE_CUSTOMER, ROLE_SALES_STAFF
from orders.models import Order, OrderStatus


class CustomerProfilePageTests(TestCase):

    @classmethod
    def setUpTestData(cls):
        User = get_user_model()
        cls.customer_group = Group.objects.create(name=ROLE_CUSTOMER)
        cls.staff_group = Group.objects.create(name=ROLE_SALES_STAFF)

        # Customer 1
        cls.customer = User.objects.create_user(
            username='kh_nguyenan',
            email='nguyenan@example.com',
            first_name='Nguyễn Văn',
            last_name='An',
            password='OldP@ssw0rd123',
        )
        cls.customer.groups.add(cls.customer_group)
        cls.profile = CustomerProfile.objects.create(
            user=cls.customer,
            phone='0905123456',
            default_address='71 Ngũ Hành Sơn, Q. Ngũ Hành Sơn, Đà Nẵng',
            date_of_birth=date(1998, 5, 15),
            gender='MALE',
            is_verified=True,
        )

        # Customer 2 (for collision testing)
        cls.other_customer = User.objects.create_user(
            username='kh_other',
            email='other@example.com',
            first_name='Bình',
            last_name='Trần',
        )
        cls.other_customer.groups.add(cls.customer_group)
        cls.other_profile = CustomerProfile.objects.create(
            user=cls.other_customer,
            phone='0987654321',
            default_address='100 Điện Biên Phủ, Đà Nẵng',
        )

        # Staff user (non-customer)
        cls.staff_user = User.objects.create_user(
            username='staff_sales',
            email='staff@example.com',
        )
        cls.staff_user.groups.add(cls.staff_group)

        # Orders for Customer 1
        for i in range(3):
            Order.objects.create(
                customer=cls.customer,
                order_code=f'ORD-CUST-{i+1}',
                recipient_name='Nguyễn Văn An',
                recipient_phone='0905123456',
                shipping_address='71 Ngũ Hành Sơn',
                order_status=OrderStatus.PENDING_CONFIRMATION,
                subtotal=Decimal('5000000'),
                discount_total=Decimal('0'),
                grand_total=Decimal('5000000'),
            )

        # Orders for Other Customer
        Order.objects.create(
            customer=cls.other_customer,
            order_code='ORD-OTHER-1',
            recipient_name='Trần Bình',
            recipient_phone='0987654321',
            shipping_address='100 Điện Biên Phủ',
            order_status=OrderStatus.CONFIRMED,
            subtotal=Decimal('10000000'),
            discount_total=Decimal('0'),
            grand_total=Decimal('10000000'),
        )

    @staticmethod
    def create_test_image(filename='avatar.png', size=(100, 100), color='red', format='PNG'):
        file_obj = io.BytesIO()
        image = Image.new('RGB', size, color=color)
        image.save(file_obj, format=format)
        file_obj.seek(0)
        return SimpleUploadedFile(filename, file_obj.read(), content_type=f'image/{format.lower()}')

    # 1. Anonymous Access Redirects to Login
    def test_anonymous_is_redirected_to_login(self):
        url_profile = reverse('accounts:profile')
        resp_profile = self.client.get(url_profile)
        self.assertRedirects(resp_profile, f"{reverse('accounts:login')}?next={url_profile}")

        url_edit = reverse('accounts:profile_edit')
        resp_edit = self.client.get(url_edit)
        self.assertRedirects(resp_edit, f"{reverse('accounts:login')}?next={url_edit}")

    # 2. Non-Customer Role is Forbidden (403)
    def test_non_customer_is_forbidden(self):
        self.client.force_login(self.staff_user)
        self.assertEqual(self.client.get(reverse('accounts:profile')).status_code, 403)
        self.assertEqual(self.client.get(reverse('accounts:profile_edit')).status_code, 403)

    # 3. Customer Profile View Renders Real Data and Templates
    def test_customer_profile_renders_real_data(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/profile.html')

        # Real customer details
        self.assertContains(response, 'Nguyễn Văn An')
        self.assertContains(response, 'nguyenan@example.com')
        self.assertContains(response, '0905123456')
        self.assertContains(response, '71 Ngũ Hành Sơn, Q. Ngũ Hành Sơn, Đà Nẵng')
        self.assertContains(response, 'kh_nguyenan')
        self.assertContains(response, 'Đã xác thực')

        # Sidebar navigation items
        self.assertContains(response, 'Thông tin cá nhân')
        self.assertContains(response, reverse('orders:my_orders'))
        self.assertContains(response, reverse('accounts:password_change'))
        self.assertContains(response, reverse('accounts:logout'))

        # Check profile_edit template is also wired
        edit_response = self.client.get(reverse('accounts:profile_edit'))
        self.assertEqual(edit_response.status_code, 200)
        self.assertTemplateUsed(edit_response, 'accounts/profile_edit.html')

    # 4. Order Count Reflects Real Orders
    def test_order_count_reflects_only_customer_orders(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('accounts:profile'))
        self.assertEqual(response.context['total_orders_count'], 3)
        self.assertContains(response, '3 đơn')

    # 5. Successful Profile Update Saves Atomically
    def test_successful_profile_update(self):
        self.client.force_login(self.customer)
        post_data = {
            'full_name': 'Nguyễn Văn Hoàng',
            'email': 'hoang.nguyen@example.com',
            'phone': '0905999888',
            'date_of_birth': '1995-12-25',
            'gender': 'MALE',
            'default_address': '88 Bạch Đằng, Hải Châu, Đà Nẵng',
        }
        response = self.client.post(reverse('accounts:profile'), post_data)
        self.assertRedirects(response, reverse('accounts:profile'))

        self.customer.refresh_from_db()
        self.profile.refresh_from_db()

        self.assertEqual(self.customer.get_full_name(), 'Nguyễn Văn Hoàng')
        self.assertEqual(self.customer.email, 'hoang.nguyen@example.com')
        self.assertEqual(self.profile.phone, '0905999888')
        self.assertEqual(self.profile.date_of_birth, date(1995, 12, 25))
        self.assertEqual(self.profile.gender, 'MALE')
        self.assertEqual(self.profile.default_address, '88 Bạch Đằng, Hải Châu, Đà Nẵng')

    # 6. Phone Validation: format & collision
    def test_phone_validation(self):
        self.client.force_login(self.customer)

        # Invalid format
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '123456789',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Số điện thoại không hợp lệ')

        # Collision with another user
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '0987654321',  # other_customer's phone
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'đã được đăng ký cho một tài khoản khác')

    # 7. Email Validation: format & collision
    def test_email_validation(self):
        self.client.force_login(self.customer)

        # Invalid email format
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'invalid-email-format',
            'phone': '0905123456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Địa chỉ email không hợp lệ')

        # Collision with another user's email
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'other@example.com',  # other_customer's email
            'phone': '0905123456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'đã được sử dụng bởi một tài khoản khác')

    # 8. Date of Birth Validation: Cannot be in future
    def test_date_of_birth_future_rejected(self):
        self.client.force_login(self.customer)
        tomorrow = date.today() + timedelta(days=1)
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '0905123456',
            'date_of_birth': tomorrow.strftime('%Y-%m-%d'),
        })
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Ngày sinh không thể lớn hơn ngày hiện tại')

    # 9. Avatar Upload Validation (Size <= 2MB, format check)
    def test_avatar_upload_valid_and_size_limit(self):
        self.client.force_login(self.customer)

        # Valid PNG avatar
        valid_img = self.create_test_image('avatar.png')
        response = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '0905123456',
            'avatar': valid_img,
        })
        self.assertRedirects(response, reverse('accounts:profile'))
        self.profile.refresh_from_db()
        self.assertTrue(bool(self.profile.avatar))

        # Avatar > 2MB rejected (valid image but 4MB uncompressed)
        big_io = io.BytesIO()
        big_img = Image.new('RGB', (1200, 1200), color='green')
        big_img.save(big_io, format='PNG', compress_level=0)
        big_io.seek(0)
        big_file = SimpleUploadedFile('big_avatar.png', big_io.read(), content_type='image/png')

        response_big = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '0905123456',
            'avatar': big_file,
        })
        self.assertEqual(response_big.status_code, 200)
        self.assertContains(response_big, 'Dung lượng ảnh đại diện không được vượt quá 2MB')

        # Invalid file extension/format
        bad_file = SimpleUploadedFile('script.exe', b'bad_bytes', content_type='application/x-msdownload')
        response_bad = self.client.post(reverse('accounts:profile'), {
            'full_name': 'Nguyễn Văn An',
            'email': 'nguyenan@example.com',
            'phone': '0905123456',
            'avatar': bad_file,
        })
        self.assertEqual(response_bad.status_code, 200)
        self.assertContains(response_bad, 'Định dạng ảnh không hợp lệ')

    # 10. Customer Sidebar Navigation Items (Excludes 'Sổ địa chỉ giao hàng')
    def test_customer_sidebar_navigation_items_consistency(self):
        self.client.force_login(self.customer)

        # Profile page
        resp_profile = self.client.get(reverse('accounts:profile'))
        self.assertEqual(resp_profile.status_code, 200)
        self.assertContains(resp_profile, 'Thông tin cá nhân')
        self.assertContains(resp_profile, 'Đơn hàng của tôi')
        self.assertContains(resp_profile, 'Đổi mật khẩu')
        self.assertContains(resp_profile, 'Đăng xuất tài khoản')
        self.assertNotContains(resp_profile, 'Sổ địa chỉ giao hàng')

        # Orders page
        resp_orders = self.client.get(reverse('orders:my_orders'))
        self.assertEqual(resp_orders.status_code, 200)
        self.assertContains(resp_orders, 'Thông tin cá nhân')
        self.assertContains(resp_orders, 'Đơn hàng của tôi')
        self.assertContains(resp_orders, 'Đổi mật khẩu')
        self.assertContains(resp_orders, 'Đăng xuất tài khoản')
        self.assertNotContains(resp_orders, 'Sổ địa chỉ giao hàng')

    # 11. Form Retains Invalid Input on Profile Edit
    def test_profile_edit_template_and_invalid_form_retention(self):
        self.client.force_login(self.customer)
        url = reverse('accounts:profile_edit')
        response = self.client.post(url, {
            'full_name': 'Tên giữ lại',
            'email': 'email-invalid',
            'phone': '0905123456',
            'default_address': 'Địa chỉ giữ lại',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/profile_edit.html')
        self.assertContains(response, 'Tên giữ lại')
        self.assertContains(response, 'Địa chỉ giữ lại')
        self.assertContains(response, 'Địa chỉ email không hợp lệ')

    # 12. Password Change: Anonymous Redirect
    def test_password_change_anonymous_redirect(self):
        url = reverse('accounts:password_change')
        response = self.client.get(url)
        self.assertRedirects(response, f"{reverse('accounts:login')}?next={url}")

    # 13. Password Change Page: Layout, Sidebar, Active State
    def test_password_change_page_renders_sidebar_and_active_state(self):
        self.client.force_login(self.customer)
        response = self.client.get(reverse('accounts:password_change'))
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(response, 'accounts/password_change.html')
        self.assertTemplateUsed(response, 'includes/customer_sidebar.html')

        # Check navigation items
        self.assertContains(response, 'Thông tin cá nhân')
        self.assertContains(response, 'Đơn hàng của tôi')
        self.assertContains(response, 'Đổi mật khẩu')
        self.assertContains(response, 'Đăng xuất tài khoản')
        self.assertNotContains(response, 'Sổ địa chỉ giao hàng')

        # Check "Đổi mật khẩu" is active
        self.assertContains(response, 'customer-nav-item active')

        # Check form fields are present
        self.assertContains(response, 'id_old_password')
        self.assertContains(response, 'id_new_password1')
        self.assertContains(response, 'id_new_password2')

    # 14. Password Change: Invalid Old Password
    def test_password_change_invalid_old_password(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': 'WrongPassword999',
            'new_password1': 'NewP@ssw0rd456',
            'new_password2': 'NewP@ssw0rd456',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].has_error('old_password'))

    # 15. Password Change: Mismatched New Passwords
    def test_password_change_mismatched_passwords(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': 'OldP@ssw0rd123',
            'new_password1': 'NewP@ssw0rd456',
            'new_password2': 'DifferentP@ss789',
        })
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context['form'].has_error('new_password2'))

    # 16. Password Change: Success Preserves Session
    def test_password_change_success_preserves_session(self):
        self.client.force_login(self.customer)
        response = self.client.post(reverse('accounts:password_change'), {
            'old_password': 'OldP@ssw0rd123',
            'new_password1': 'NewValidP@ssw0rd2026',
            'new_password2': 'NewValidP@ssw0rd2026',
        }, follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Đổi mật khẩu thành công')

        # Customer remains logged in
        self.assertTrue(response.context['user'].is_authenticated)

        # Authenticate with new password
        from django.contrib.auth import authenticate
        self.assertIsNotNone(authenticate(username='kh_nguyenan', password='NewValidP@ssw0rd2026'))
