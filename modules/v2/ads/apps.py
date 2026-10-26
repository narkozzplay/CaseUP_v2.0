from django.apps import AppConfig


class AdsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "modules.v2.ads"
    label = "ads"
    verbose_name = "Реклама"

    def ready(self):
        from django.db.models.signals import m2m_changed, post_delete, post_save

        from .models import AdPlacement, HouseBanner
        from .services import drop_banner_cache

        for model in (HouseBanner, AdPlacement):
            post_save.connect(drop_banner_cache, sender=model, dispatch_uid=f"ads_drop_banner_cache_save_{model.__name__}")
            post_delete.connect(drop_banner_cache, sender=model, dispatch_uid=f"ads_drop_banner_cache_delete_{model.__name__}")
        m2m_changed.connect(drop_banner_cache, sender=HouseBanner.placements.through, dispatch_uid="ads_drop_banner_cache_m2m")
