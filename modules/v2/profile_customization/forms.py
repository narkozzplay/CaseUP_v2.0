from django import forms


class AddBackgroundForm(forms.Form):
    title = forms.CharField(
        max_length=160,
        label="Название",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    static_url = forms.URLField(
        label="URL изображения (.jpg)",
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://shared.fastly.steamstatic.com/...jpg"}),
    )
    video_url = forms.URLField(
        label="URL видео (.webm)",
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://shared.fastly.steamstatic.com/...webm"}),
    )


class AddAvatarForm(forms.Form):
    title = forms.CharField(
        max_length=160,
        label="Название",
        widget=forms.TextInput(attrs={"class": "form-control"}),
    )
    avatar_url = forms.URLField(
        label="URL аватарки",
        widget=forms.URLInput(attrs={"class": "form-control", "placeholder": "https://avatars.steamstatic.com/..._full.jpg"}),
    )
