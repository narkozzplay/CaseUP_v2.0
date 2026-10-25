from __future__ import annotations

from django import forms

from .models import DEFAULT_INVESTMENT_MESSAGE_TEMPLATE, TelegramNotifierConfig


class TelegramNotifierSettingsForm(forms.ModelForm):
    bot_token = forms.CharField(
        required=False,
        widget=forms.PasswordInput(
            render_value=False,
            attrs={
                "class": "form-control",
                "autocomplete": "new-password",
                "placeholder": "Вставьте новый токен из @BotFather — или оставьте пустым",
            },
        ),
        help_text="",
    )

    class Meta:
        model = TelegramNotifierConfig
        fields = [
            "is_enabled",
            "bot_username",
            "default_chat_id",
            "public_base_url",
            "updates_mode",
            "investment_message_template",
        ]
        widgets = {
            "is_enabled": forms.CheckboxInput(
                attrs={
                    "class": "toggle-control__input",
                }
            ),
            "bot_username": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: my_caseup_bot",
                }
            ),
            "default_chat_id": forms.TextInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "Например: 123456789 или -1001234567890",
                }
            ),
            "updates_mode": forms.Select(attrs={"class": "form-control"}),
            "public_base_url": forms.URLInput(
                attrs={
                    "class": "form-control",
                    "placeholder": "https://case-up.ru",
                }
            ),
            "investment_message_template": forms.Textarea(
                attrs={
                    "class": "form-control",
                    "rows": 16,
                    "spellcheck": "false",
                }
            ),
        }

    def clean_updates_mode(self):
        # Older forms/clients don't send the field: keep what is stored.
        return self.cleaned_data.get("updates_mode") or self.instance.updates_mode or TelegramNotifierConfig.UpdatesMode.POLLING

    def __init__(self, *args, **kwargs):
        instance: TelegramNotifierConfig | None = kwargs.get("instance")
        super().__init__(*args, **kwargs)

        self.fields["bot_username"].required = False
        self.fields["default_chat_id"].required = False
        self.fields["public_base_url"].required = False
        self.fields["updates_mode"].required = False
        self.fields["investment_message_template"].required = True

        if instance is not None:
            self.fields["bot_token"].help_text = (
                f"Текущий токен: {instance.masked_bot_token}. "
                "Оставь поле пустым, чтобы не менять."
            )

        if not self.instance.pk and not self.initial.get("investment_message_template"):
            self.initial["investment_message_template"] = DEFAULT_INVESTMENT_MESSAGE_TEMPLATE

    def clean_bot_username(self):
        value = (self.cleaned_data.get("bot_username") or "").strip().lstrip("@")
        return value

    def clean_public_base_url(self):
        value = (self.cleaned_data.get("public_base_url") or "").strip().rstrip("/")
        return value

    def clean_investment_message_template(self):
        value = (self.cleaned_data.get("investment_message_template") or "").strip()
        if not value:
            raise forms.ValidationError("Шаблон уведомления не может быть пустым.")
        return value

    def save(self, commit=True):
        instance: TelegramNotifierConfig = super().save(commit=False)
        new_token = (self.cleaned_data.get("bot_token") or "").strip()
        if new_token:
            instance.bot_token = new_token

        if commit:
            instance.save()
        return instance


class TelegramTestSendForm(forms.Form):
    chat_id = forms.CharField(
        required=False,
        widget=forms.TextInput(
            attrs={
                "class": "form-control",
                "placeholder": "Пусто = использовать default chat id",
            }
        ),
    )
    text = forms.CharField(
        required=True,
        widget=forms.Textarea(
            attrs={
                "class": "form-control",
                "rows": 6,
                "placeholder": "Текст тестового сообщения",
            }
        ),
    )

    def clean_text(self):
        value = (self.cleaned_data.get("text") or "").strip()
        if not value:
            raise forms.ValidationError("Текст тестового сообщения не может быть пустым.")
        return value

class TelegramChannelDigestForm(forms.ModelForm):
    class Meta:
        model = TelegramNotifierConfig
        fields = [
            "channel_digest_enabled",
            "channel_chat_id",
            "channel_digest_times",
            "channel_digest_top_n",
            "channel_digest_min_price_rub",
            "channel_digest_fresh_hours",
        ]
        widgets = {
            "channel_digest_enabled": forms.CheckboxInput(attrs={"class": "toggle-control__input"}),
            "channel_chat_id": forms.TextInput(attrs={"class": "form-control", "placeholder": "@caseup_pro_anons или -1001234567890"}),
            "channel_digest_times": forms.TextInput(attrs={"class": "form-control", "placeholder": "20:00 или 10:00, 20:00"}),
            "channel_digest_top_n": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 10}),
            "channel_digest_min_price_rub": forms.NumberInput(attrs={"class": "form-control", "min": 0, "step": "0.01"}),
            "channel_digest_fresh_hours": forms.NumberInput(attrs={"class": "form-control", "min": 1, "max": 72}),
        }

    def clean_channel_chat_id(self):
        value = (self.cleaned_data.get("channel_chat_id") or "").strip()
        if value and not (value.startswith("@") or value.lstrip("-").isdigit()):
            # A bare username or a t.me link is what people usually paste.
            value = "@" + value.rsplit("/", 1)[-1].lstrip("@")
        return value

    def clean_channel_digest_times(self):
        from .channel_digest import parse_times

        value = self.cleaned_data.get("channel_digest_times") or ""
        try:
            times = parse_times(value)
        except ValueError:
            raise forms.ValidationError("Время в формате ЧЧ:ММ через запятую, например «10:00, 20:00».")
        return ", ".join(slot.strftime("%H:%M") for slot in times)

    def clean_channel_digest_top_n(self):
        value = self.cleaned_data.get("channel_digest_top_n") or 0
        if not 1 <= value <= 10:
            raise forms.ValidationError("От 1 до 10 предметов.")
        return value

    def clean_channel_digest_fresh_hours(self):
        value = self.cleaned_data.get("channel_digest_fresh_hours") or 0
        if not 1 <= value <= 72:
            raise forms.ValidationError("От 1 до 72 часов.")
        return value

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("channel_digest_enabled"):
            if not cleaned.get("channel_chat_id"):
                self.add_error("channel_chat_id", "Укажите канал, куда постить.")
            if not cleaned.get("channel_digest_times"):
                self.add_error("channel_digest_times", "Укажите хотя бы одно время.")
        return cleaned
