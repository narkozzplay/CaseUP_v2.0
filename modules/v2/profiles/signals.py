from decimal import Decimal

from django.contrib.auth import get_user_model
from django.db.models.signals import post_save
from django.dispatch import receiver

from modules.v2.accounts.steam_sync import sync_profile_from_social_auth
from .models import UserProfile

try:
    from social_django.models import UserSocialAuth
    from social_django.signals import social_auth_authenticated
except Exception:  # pragma: no cover
    UserSocialAuth = None
    social_auth_authenticated = None

User = get_user_model()


@receiver(post_save, sender=User)
def ensure_profile_v2(sender, instance, created, **kwargs):
    if created:
        UserProfile.objects.create(user=instance, balance_coins=Decimal("0.00"))


if social_auth_authenticated is not None:
    @receiver(social_auth_authenticated)
    def sync_steam_profile(sender, request, user, *args, **kwargs):
        try:
            backend = kwargs.get("backend")
            if backend is not None and getattr(backend, "name", None) != "steam":
                return
            sync_profile_from_social_auth(user, save=True)
        except Exception:
            return


if UserSocialAuth is not None:
    @receiver(post_save, sender=UserSocialAuth)
    def sync_steam_profile_on_social_save(sender, instance, created, **kwargs):
        try:
            if getattr(instance, "provider", None) != "steam":
                return
            sync_profile_from_social_auth(instance.user, save=True)
        except Exception:
            return