"""Emby tag API urls, /api/emby/"""

from django.urls import path

from emby import views

urlpatterns = [
    path("tags/", views.EmbyTagsView.as_view(), name="api-emby-tags"),
]
