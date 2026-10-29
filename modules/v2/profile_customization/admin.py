from django import forms
from django.contrib import admin

from .effects import get_effect_by_ref, reward_choices

from .models import EffectGrant, ShowcaseItem, SteamAvatar, SteamBackground, UserCustomization


@admin.register(SteamBackground)
class SteamBackgroundAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "source_item_id", "is_active", "added_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title", "source_item_id")
    raw_id_fields = ("added_by",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(SteamAvatar)
class SteamAvatarAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "is_active", "added_by", "created_at")
    list_filter = ("is_active",)
    search_fields = ("title",)
    raw_id_fields = ("added_by",)
    readonly_fields = ("created_at",)
    ordering = ("-created_at",)


@admin.register(UserCustomization)
class UserCustomizationAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "selected_background", "selected_avatar", "avatar_frame", "name_effect", "title_effect", "updated_at")
    raw_id_fields = ("user", "selected_background", "selected_avatar")
    readonly_fields = ("updated_at",)


class EffectGrantForm(forms.ModelForm):
    effect = forms.ChoiceField(label="Эффект", choices=())

    class Meta:
        model = EffectGrant
        fields = ("user", "effect", "note")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["effect"].choices = reward_choices()
        if self.instance.pk:
            self.initial["effect"] = f"{self.instance.slot}:{self.instance.key}"

    def clean_effect(self):
        effect = get_effect_by_ref(self.cleaned_data["effect"])
        if effect is None:
            raise forms.ValidationError("Эффект не найден.")
        self.instance.slot, self.instance.key = effect.slot, effect.key
        return effect.ref

    def save(self, commit=True):
        if not self.instance.source:
            self.instance.source = "admin"
        return super().save(commit)


@admin.register(EffectGrant)
class EffectGrantAdmin(admin.ModelAdmin):
    form = EffectGrantForm
    list_display = ("id", "user", "slot", "key", "source", "created_at", "seen_at")
    list_filter = ("slot", "key")
    search_fields = ("user__username", "key", "source")
    raw_id_fields = ("user",)
    readonly_fields = ("created_at",)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        if not change:
            from .notifications import notify_effect_unlocked

            notify_effect_unlocked(obj)


@admin.register(ShowcaseItem)
class ShowcaseItemAdmin(admin.ModelAdmin):
    list_display = ("id", "user", "item", "position", "created_at")
    raw_id_fields = ("user", "item")
