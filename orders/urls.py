from django.urls import path
from orders import views

app_name = 'orders'

urlpatterns = [
    path('buy-now/', views.buy_now_view, name='buy_now'),
    path('checkout/', views.checkout_view, name='checkout'),
    path('', views.customer_orders_list_view, name='customer_orders_list'),
    path('management/returns/', views.return_request_management_view, name='return_request_management'),
    path('management/returns/<int:pk>/review/', views.return_request_review_view, name='return_request_review'),
    path('management/returns/<int:pk>/create-refund/', views.create_return_refund_view, name='create_return_refund'),
    path('management/refunds/<int:pk>/confirm/', views.confirm_return_refund_view, name='confirm_return_refund'),
    path('<int:pk>/', views.order_detail_view, name='order_detail'),
    path('<int:pk>/items/<int:item_pk>/review/', views.purchased_product_review_view, name='purchased_product_review'),
    path('<int:pk>/return-request/', views.return_request_create_view, name='return_request_create'),
    path('<int:pk>/cancel/', views.customer_order_cancel_view, name='cancel_order'),
    path('<int:pk>/notify-transfer/', views.order_notify_transfer_view, name='notify_transfer'),

    path('management/pending/', views.pending_orders_list_view, name='pending_orders_list'),
    path('management/<int:pk>/', views.order_management_detail_view, name='management_detail'),
    path('management/<int:pk>/confirm/', views.confirm_order_action_view, name='confirm_order'),
    path('management/<int:pk>/ship/', views.ship_order_action_view, name='ship_order'),
    path('management/<int:pk>/deliver/', views.deliver_order_action_view, name='deliver_order'),
    path('management/<int:pk>/collect-cod/', views.collect_cod_action_view, name='collect_cod'),
]

