from django.apps import AppConfig


class CeleryTasksConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.celery_tasks"
    label = "celery_tasks"
    verbose_name = "Celery Tasks"