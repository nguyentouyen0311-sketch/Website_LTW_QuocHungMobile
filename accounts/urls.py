from django.urls import path
from django.contrib.auth import views as auth_views
from django.urls import reverse_lazy
from accounts.views import (
    register_view,
    login_view,
    logout_view,
    landing_view,
    profile_view,
    profile_edit_view,
    staff_list_view,
    staff_create_view,
    staff_edit_view,
    staff_change_status_view,
)

app_name = 'accounts'

urlpatterns = [
    path('register/', register_view, name='register'),
    path('login/', login_view, name='login'),
    path(
        'password-reset/',
        auth_views.PasswordResetView.as_view(
            template_name='accounts/password_reset.html',
            email_template_name='accounts/password_reset_email.html',
            subject_template_name='accounts/password_reset_subject.txt',
            success_url=reverse_lazy('accounts:password_reset_done'),
        ),
        name='password_reset',
    ),
    path('password-reset/done/', auth_views.PasswordResetDoneView.as_view(template_name='accounts/password_reset_done.html'), name='password_reset_done'),
    path(
        'password-reset/confirm/<uidb64>/<token>/',
        auth_views.PasswordResetConfirmView.as_view(
            template_name='accounts/password_reset_confirm.html',
            success_url=reverse_lazy('accounts:password_reset_complete'),
        ),
        name='password_reset_confirm',
    ),
    path('password-reset/complete/', auth_views.PasswordResetCompleteView.as_view(template_name='accounts/password_reset_complete.html'), name='password_reset_complete'),
    path('logout/', logout_view, name='logout'),
    path('landing/', landing_view, name='landing'),
    path('profile/', profile_view, name='profile'),
    path('profile/edit/', profile_edit_view, name='profile_edit'),
    path('staff/', staff_list_view, name='staff_list'),
    path('staff/new/', staff_create_view, name='staff_create'),
    path('staff/<int:user_id>/edit/', staff_edit_view, name='staff_edit'),
    path('staff/<int:user_id>/status/', staff_change_status_view, name='staff_change_status'),
]


