from django.urls import path

from . import views

app_name = "celery_tasks"

urlpatterns = [
    path("", views.control_center, name="control_center"),
    path("<int:rule_id>/toggle/", views.toggle_rule, name="toggle_rule"),
    path("<int:rule_id>/run-now/", views.run_rule_now, name="run_rule_now"),
]
