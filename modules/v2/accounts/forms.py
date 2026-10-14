from django import forms
from django.contrib.auth import get_user_model
from django.contrib.auth.forms import AuthenticationForm, UserCreationForm

User = get_user_model()


class LoginForm(AuthenticationForm):
    username = forms.CharField(
        label="Логин",
        widget=forms.TextInput(attrs={"autofocus": True, "autocomplete": "username"}),
    )
    password = forms.CharField(
        label="Пароль",
        strip=False,
        widget=forms.PasswordInput(attrs={"autocomplete": "current-password"}),
    )


class RegisterForm(UserCreationForm):
    email = forms.EmailField(required=False, label="Email")

    class Meta:
        model = User
        fields = ("username", "email")

class SteamLoginConsentForm(forms.Form):
    """Both boxes are required before the Steam sign-in starts (the agreement and, separately, the
    personal-data consent required by 152-FZ)."""

    accept_agreement = forms.BooleanField(
        error_messages={"required": "Примите Пользовательское соглашение и подтвердите, что вам исполнилось 18 лет."},
    )
    accept_privacy = forms.BooleanField(
        error_messages={"required": "Подтвердите согласие на обработку персональных данных."},
    )
