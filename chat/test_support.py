"""Hàm dùng chung cho các file test của app chat (không chứa test)."""
from django.contrib.auth import get_user_model
from django.contrib.auth.models import Group

from accounts.permissions import (
    ROLE_ADMIN, ROLE_CUSTOMER, ROLE_INVENTORY_STAFF, ROLE_SALES_STAFF,
)

TEST_PASSWORD = 'Mk@Test12345'


def make_user(username, role=None):
    user = get_user_model().objects.create_user(username=username, password=TEST_PASSWORD)
    if role:
        group, _created = Group.objects.get_or_create(name=role)
        user.groups.add(group)
    return user


def make_customer(username='khach1'):
    return make_user(username, ROLE_CUSTOMER)


def make_sales(username='nvban1'):
    return make_user(username, ROLE_SALES_STAFF)


def make_inventory(username='nvkho1'):
    return make_user(username, ROLE_INVENTORY_STAFF)


def make_admin(username='admin1'):
    return make_user(username, ROLE_ADMIN)
