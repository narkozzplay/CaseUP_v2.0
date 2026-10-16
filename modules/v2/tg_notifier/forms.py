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

    def __init__(self, *args, **kwargs):
        instance: TelegramNotifierConfig | None = kwargs.get("instance")
        super().__init__(*args, **kwargs)

        self.fields["bot_username"].required = False
        self.fields["default_chat_id"].required = False
        self.fields["public_base_url"].required = False
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