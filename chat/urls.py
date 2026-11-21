from django.urls import path

from . import workspace as views

app_name = 'chat'

urlpatterns = [
    path('', views.chat_view, name='chat'),
    path('faq/<int:faq_id>/', views.chat_ask_faq_view, name='ask_faq'),
    path('send/', views.chat_send_view, name='send'),
    path('close/', views.chat_close_view, name='close'),
    path('handoff/', views.chat_handoff_view, name='handoff'),
    path('updates/', views.customer_snapshot_view, name='updates'),
    path('staff/updates/', views.staff_snapshot_view, name='staff_updates'),
    path('staff/', views.staff_inbox_view, name='staff_inbox'),
    path('staff/<int:conversation_id>/', views.staff_conversation_view, name='staff_conversation'),
    path('staff/<int:conversation_id>/close/', views.staff_close_view, name='staff_close'),
    path('knowledge/', views.knowledge_list_view, name='knowledge_list'),
    path('knowledge/new/', views.knowledge_edit_view, name='knowledge_create'),
    path('knowledge/<int:faq_id>/edit/', views.knowledge_edit_view, name='knowledge_edit'),
    path('knowledge/<int:faq_id>/state/', views.knowledge_toggle_view, name='knowledge_toggle'),
    path('knowledge/import/', views.knowledge_import_view, name='knowledge_import'),
    path('knowledge/export/', views.knowledge_export_view, name='knowledge_export'),
    path('knowledge/test/', views.knowledge_test_view, name='knowledge_test'),
]
