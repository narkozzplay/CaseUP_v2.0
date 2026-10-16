from django.urls import path

from . import views

app_name = "webpush"

urlpatterns = [
    path("key/", views.public_key, name="key"),
    path("subscribe/", views.subscribe, name="subscribe"),
    path("unsubscribe/", views.unsubscribe, name="unsubscribe"),
    path("test/", views.send_test, name="test"),
    path("categories/", views.save_categories, name="categories"),
    path("prompt/dismiss/", views.dismiss_prompt, name="dismiss_prompt"),
]
