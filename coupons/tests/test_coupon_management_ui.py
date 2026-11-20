from decimal import Decimal
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group
from django.test import TestCase, Client
from django.urls import reverse
from django.utils import timezone
from datetime import timedelta

from accounts.permissions import ROLE_ADMIN, ROLE_CUSTOMER
from catalog.models import Category, Product
from coupons.models import Coupon, CouponProduct, DiscountType

User = get_user_model()


class CouponManagementUITest(TestCase):
    def setUp(self):
        self.client = Client()

        self.admin_group, _ = Group.objects.get_or_create(name=ROLE_ADMIN)
        self.customer_group, _ = Group.objects.get_or_create(name=ROLE_CUSTOMER)

        self.admin_user = User.objects.create_user(
            username='admin_test',
            email='admin@quochungmobile.vn',
            password='Password123!',
            first_name='Admin',
            last_name='QuocHung'
        )
        self.admin_user.groups.add(self.admin_group)

        self.customer_user = User.objects.create_user(
            username='customer_test',
            email='customer@example.com',
            password='Password123!',
            first_name='Khach',
            last_name='Hang'
        )
        self.customer_user.groups.add(self.customer_group)

        self.category = Category.objects.create(name='Điện thoại')
        self.product1 = Product.objects.create(name='iPhone 16 Pro Max', category=self.category, is_active=True)
        self.product2 = Product.objects.create(name='Samsung Galaxy S24 Ultra', category=self.category, is_active=True)

        self.now = timezone.now()
        self.starts_at = self.now - timedelta(days=1)
        self.ends_at = self.now + timedelta(days=10)

        self.coupon_active = Coupon.objects.create(
            code='ACTIVE50',
            discount_type=DiscountType.PERCENT,
            value=Decimal('10.00'),
            max_discount=Decimal('500000.00'),
            min_order_amount=Decimal('1000000.00'),
            max_uses=100,
            used_count=20,
            reserved_count=5,
            starts_at=self.starts_at,
            ends_at=self.ends_at,
            is_active=True,
            applies_to_all=True
        )

        self.coupon_expired = Coupon.objects.create(
            code='EXPIRED100',
            discount_type=DiscountType.FIXED,
            value=Decimal('100000.00'),
            max_uses=50,
            used_count=10,
            reserved_count=0,
            starts_at=self.now - timedelta(days=20),
            ends_at=self.now - timedelta(days=5),
            is_active=True,
            applies_to_all=True
        )

    def test_permission_access_control(self):
        """Anonymous and non-admin users must not access coupon management."""
        url = reverse('coupons:coupon_list')

        res_anon = self.client.get(url)
        self.assertEqual(res_anon.status_code, 403)

        self.client.force_login(self.customer_user)
        res_cust = self.client.get(url)
        self.assertEqual(res_cust.status_code, 403)

        self.client.force_login(self.admin_user)
        res_admin = self.client.get(url)
        self.assertEqual(res_admin.status_code, 200)

    def test_kpi_calculations_and_display(self):
        """KPI totals must accurately reflect the backend dataset."""
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('coupons:coupon_list'))
        self.assertEqual(res.status_code, 200)

        metrics = res.context['metrics']
        self.assertEqual(metrics['total_coupons'], 2)
        self.assertEqual(metrics['active_count'], 1)
        self.assertEqual(metrics['total_used'], 30)
        self.assertEqual(metrics['total_reserved'], 5)

        content = res.content.decode('utf-8')
        self.assertIn('ACTIVE50', content)
        self.assertIn('EXPIRED100', content)
        self.assertIn('Đang diễn ra', content)
        self.assertIn('Hết hạn', content)

    def test_filter_functionality(self):
        """Filters by search query, status, discount type, and product scope."""
        self.client.force_login(self.admin_user)

        res_search = self.client.get(reverse('coupons:coupon_list'), {'q': 'ACTIVE'})
        self.assertEqual(len(res_search.context['coupons']), 1)
        self.assertEqual(res_search.context['coupons'][0].code, 'ACTIVE50')

        res_st_active = self.client.get(reverse('coupons:coupon_list'), {'status': 'active'})
        self.assertEqual(len(res_st_active.context['coupons']), 1)
        self.assertEqual(res_st_active.context['coupons'][0].code, 'ACTIVE50')

        res_st_exp = self.client.get(reverse('coupons:coupon_list'), {'status': 'expired'})
        self.assertEqual(len(res_st_exp.context['coupons']), 1)
        self.assertEqual(res_st_exp.context['coupons'][0].code, 'EXPIRED100')

        res_type_fixed = self.client.get(reverse('coupons:coupon_list'), {'type': 'FIXED'})
        self.assertEqual(len(res_type_fixed.context['coupons']), 1)
        self.assertEqual(res_type_fixed.context['coupons'][0].code, 'EXPIRED100')

    def test_create_valid_coupon_and_redirect(self):
        """Creating a coupon via POST should normalize code, set counters to 0, and redirect."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': '  newvoucher2026  ',
            'discount_type': 'PERCENT',
            'value': '15',
            'max_discount': '300000',
            'min_order_amount': '500000',
            'max_uses': '200',
            'starts_at': (self.now + timedelta(days=1)).strftime('%Y-%m-%dT%H:%M'),
            'ends_at': (self.now + timedelta(days=20)).strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertRedirects(res, reverse('coupons:coupon_list'))

        coupon = Coupon.objects.get(code='NEWVOUCHER2026')
        self.assertEqual(coupon.used_count, 0)
        self.assertEqual(coupon.reserved_count, 0)
        self.assertEqual(coupon.value, Decimal('15.00'))
        self.assertEqual(coupon.max_discount, Decimal('300000.00'))

    def test_create_duplicate_code_validation_error(self):
        """Duplicate code must return validation error and preserve bound form."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': 'ACTIVE50',
            'discount_type': 'PERCENT',
            'value': '20',
            'max_uses': '100',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'applies_to_all': 'True',
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertFormError(res.context['form'], 'code', "Mã voucher 'ACTIVE50' đã tồn tại trong hệ thống.")

    def test_percent_value_bounds_validation(self):
        """PERCENT discount value must be > 0 and <= 100."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': 'OVER100',
            'discount_type': 'PERCENT',
            'value': '150',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'applies_to_all': 'True',
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertTrue('value' in res.context['form'].errors)

    def test_time_ordering_validation(self):
        """Ends_at must be strictly after starts_at."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': 'TIMETEST',
            'discount_type': 'FIXED',
            'value': '50000',
            'max_uses': '50',
            'starts_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'applies_to_all': 'True',
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertTrue('ends_at' in res.context['form'].errors)

    def test_specific_product_scope_requires_products(self):
        """When applies_to_all is False, at least one product must be selected."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': 'SPECIFICNOPROD',
            'discount_type': 'FIXED',
            'value': '50000',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'applies_to_all': 'False',
            'products': [],
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertEqual(res.status_code, 200)
        self.assertTrue('products' in res.context['form'].errors)

    def test_edit_coupon_selected_highlight_and_quota_protection(self):
        """Editing coupon highlights row and prevents lowering max_uses below consumed count."""
        self.client.force_login(self.admin_user)
        edit_url = reverse('coupons:coupon_edit', args=[self.coupon_active.id])

        res_get = self.client.get(edit_url)
        self.assertEqual(res_get.status_code, 200)
        self.assertEqual(res_get.context['is_edit'], True)
        self.assertEqual(res_get.context['editing_coupon'], self.coupon_active)
        content = res_get.content.decode('utf-8')
        self.assertIn('qhm-row-selected', content)

        post_data = {
            'code': self.coupon_active.code,
            'discount_type': self.coupon_active.discount_type,
            'value': str(self.coupon_active.value),
            'max_discount': str(self.coupon_active.max_discount),
            'min_order_amount': str(self.coupon_active.min_order_amount),
            'max_uses': '15',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res_post = self.client.post(edit_url, post_data)
        self.assertEqual(res_post.status_code, 200)
        self.assertTrue('max_uses' in res_post.context['form'].errors)

    def test_edit_fixed_discount_preserves_cap_without_overwriting(self):
        """If max_discount is not in POST (disabled on client for FIXED), stored cap is preserved."""
        self.client.force_login(self.admin_user)
        edit_url = reverse('coupons:coupon_edit', args=[self.coupon_active.id])

        post_data = {
            'code': self.coupon_active.code,
            'discount_type': 'FIXED',
            'value': '100000',
            'min_order_amount': '0',
            'max_uses': '100',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res = self.client.post(edit_url, post_data)
        self.assertRedirects(res, reverse('coupons:coupon_list'))

        self.coupon_active.refresh_from_db()
        self.assertEqual(self.coupon_active.discount_type, DiscountType.FIXED)
        self.assertEqual(self.coupon_active.max_discount, Decimal('500000.00'))

    def test_sidebar_structure_and_logout_form(self):
        """Sidebar contains full navigation items, active status, and real POST logout."""
        self.client.force_login(self.admin_user)
        res = self.client.get(reverse('coupons:coupon_list'))
        content = res.content.decode('utf-8')

        self.assertIn('QUỐC HÙNG MOBILE', content)
        self.assertIn('ADMIN PORTAL', content)
        self.assertIn('TỔNG QUAN', content)
        self.assertIn('QUẢN LÝ HÀNG HÓA', content)
        self.assertIn('BÁN HÀNG & CSKH', content)
        self.assertIn('HỆ THỐNG', content)

        self.assertIn('class="qhm-nav-item active"', content)
        self.assertIn('Mã giảm giá', content)

        self.assertIn(reverse('catalog:category_list'), content)
        self.assertIn(reverse('catalog:product_list'), content)
        self.assertIn(reverse('catalog:attribute_list'), content)
        self.assertIn(reverse('inventory:inventory_list'), content)
        self.assertIn(reverse('orders:pending_orders_list'), content)
        self.assertIn(reverse('chat:staff_inbox'), content)
        self.assertIn(reverse('accounts:staff_list'), content)

        self.assertIn(reverse('accounts:logout'), content)
        self.assertIn('name="csrfmiddlewaretoken"', content)

    def test_blank_vs_zero_max_discount(self):
        """Blank max_discount means unlimited (None), while 0 means 0 VND cap."""
        self.client.force_login(self.admin_user)

        post_blank = {
            'code': 'CAPBLANK',
            'discount_type': 'PERCENT',
            'value': '10',
            'max_discount': '',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res_blank = self.client.post(reverse('coupons:coupon_create'), post_blank)
        self.assertRedirects(res_blank, reverse('coupons:coupon_list'))
        c_blank = Coupon.objects.get(code='CAPBLANK')
        self.assertIsNone(c_blank.max_discount)

        post_zero = {
            'code': 'CAPZERO',
            'discount_type': 'PERCENT',
            'value': '10',
            'max_discount': '0',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res_zero = self.client.post(reverse('coupons:coupon_create'), post_zero)
        self.assertRedirects(res_zero, reverse('coupons:coupon_list'))
        c_zero = Coupon.objects.get(code='CAPZERO')
        self.assertEqual(c_zero.max_discount, Decimal('0.00'))

    def test_server_counters_untampered_on_edit(self):
        """Fabricated used_count or reserved_count in POST cannot modify server-managed counters."""
        self.client.force_login(self.admin_user)
        edit_url = reverse('coupons:coupon_edit', args=[self.coupon_active.id])

        post_data = {
            'code': self.coupon_active.code,
            'discount_type': self.coupon_active.discount_type,
            'value': str(self.coupon_active.value),
            'max_discount': str(self.coupon_active.max_discount),
            'max_uses': '150',
            'used_count': '999',
            'reserved_count': '888',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res = self.client.post(edit_url, post_data)
        self.assertRedirects(res, reverse('coupons:coupon_list'))

        self.coupon_active.refresh_from_db()
        self.assertEqual(self.coupon_active.used_count, 20)
        self.assertEqual(self.coupon_active.reserved_count, 5)

    def test_product_scope_mapping_creation_and_removal(self):
        """Testing CouponProduct mapping creation when applies_to_all=False and cleanup when switched to True."""
        self.client.force_login(self.admin_user)

        post_specific = {
            'code': 'SPECIFICPRODUCTS',
            'discount_type': 'PERCENT',
            'value': '10',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'False',
            'products': [str(self.product1.id), str(self.product2.id)],
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_specific)
        self.assertRedirects(res, reverse('coupons:coupon_list'))

        c_spec = Coupon.objects.get(code='SPECIFICPRODUCTS')
        self.assertFalse(c_spec.applies_to_all)
        self.assertEqual(c_spec.coupon_products.count(), 2)

        edit_url = reverse('coupons:coupon_edit', args=[c_spec.id])
        post_all = {
            'code': c_spec.code,
            'discount_type': c_spec.discount_type,
            'value': str(c_spec.value),
            'max_uses': str(c_spec.max_uses),
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'is_active': 'on',
            'applies_to_all': 'True',
        }
        res_edit = self.client.post(edit_url, post_all)
        self.assertRedirects(res_edit, reverse('coupons:coupon_list'))

        c_spec.refresh_from_db()
        self.assertTrue(c_spec.applies_to_all)
        self.assertEqual(c_spec.coupon_products.count(), 0)

    def test_bound_form_retention_on_validation_failure(self):
        """Invalid POST retains submitted values on bound form without JS."""
        self.client.force_login(self.admin_user)
        post_data = {
            'code': 'TESTINVALID',
            'discount_type': 'PERCENT',
            'value': '120',
            'min_order_amount': '1500000',
            'max_uses': '50',
            'starts_at': self.starts_at.strftime('%Y-%m-%dT%H:%M'),
            'ends_at': self.ends_at.strftime('%Y-%m-%dT%H:%M'),
            'applies_to_all': 'False',
            'products': [str(self.product1.id)],
        }
        res = self.client.post(reverse('coupons:coupon_create'), post_data)
        self.assertEqual(res.status_code, 200)

        form = res.context['form']
        self.assertEqual(form['code'].value(), 'TESTINVALID')
        self.assertEqual(form['value'].value(), '120')
        self.assertEqual(form['min_order_amount'].value(), '1500000')
        self.assertEqual(form['products'].value(), [str(self.product1.id)])
        self.assertTrue(form.has_error('value'))

