"""Control Center forms: majors, stages with their teams and matches, teams and Pick'em rewards."""
from __future__ import annotations

import re

from django import forms
from django.utils.text import slugify

from .models import Major, Match, MatchPost, RewardRule, Stage, StageTeam, Team

_DT_FORMAT = "%Y-%m-%dT%H:%M"
_DT_INPUTS = [_DT_FORMAT, "%Y-%m-%d %H:%M", "%Y-%m-%d"]


def _dt_widget():
    return forms.DateTimeInput(attrs={"type": "datetime-local", "class": "form-control"}, format=_DT_FORMAT)


def _date_widget():
    return forms.DateInput(attrs={"type": "date", "class": "form-control"}, format="%Y-%m-%d")


def _text(**attrs):
    return forms.TextInput(attrs={"class": "form-control", **attrs})


def _number(**attrs):
    return forms.NumberInput(attrs={"class": "form-control", **attrs})


def _select():
    return forms.Select(attrs={"class": "form-control"})


def _unique_slug(model, raw: str, *, instance=None) -> str:
    base = slugify(raw)[:70] or "major"
    slug, index = base, 2
    others = model.objects.exclude(pk=instance.pk) if instance is not None and instance.pk else model.objects.all()
    while others.filter(slug=slug).exists():
        slug, index = f"{base}-{index}", index + 1
    return slug


class _SlugFromNameMixin:
    """An empty slug is made from the name, so the admin does not have to think up a URL."""

    def clean_slug(self):
        raw = (self.cleaned_data.get("slug") or "").strip()
        if raw:
            return raw
        return _unique_slug(self._meta.model, self.cleaned_data.get("name") or self.data.get("name") or "", instance=self.instance)


class MajorForm(_SlugFromNameMixin, forms.ModelForm):
    class Meta:
        model = Major
        fields = (
            "name", "slug", "location", "venue", "starts_at", "ends_on", "prize_pool", "description",
            "banner", "banner_video_mp4", "banner_video_webm", "is_published", "require_steam", "items_keyword",
            "pandascore_serie_id", "match_posts_enabled",
        )
        widgets = {
            "name": _text(),
            "slug": _text(placeholder="из названия"),
            "location": _text(),
            "venue": _text(),
            "starts_at": _dt_widget(),
            "ends_on": _date_widget(),
            "prize_pool": _text(placeholder="$1 250 000"),
            "description": forms.Textarea(attrs={"class": "form-control", "rows": 3}),
            "items_keyword": _text(),
            "pandascore_serie_id": _number(min=1),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["slug"].help_text = "Адрес страницы /majors/<slug>/. Пусто — из названия."
        self.fields["starts_at"].input_formats = _DT_INPUTS


class MatchPostForm(forms.ModelForm):
    """What the channel post says; the MVP is the usual addition after the match."""

    maps_text = forms.CharField(
        label="Карты",
        required=False,
        widget=forms.Textarea(attrs={"class": "form-control", "rows": 3, "placeholder": "Mirage 13:9\nInferno 11:13"}),
        help_text="По строке на карту: название и счёт по раундам (A:B), счёт можно не писать.",
    )

    class Meta:
        model = MatchPost
        fields = ("team_a", "score_a", "score_b", "team_b", "mvp", "progress")
        widgets = {
            "team_a": _text(),
            "team_b": _text(),
            "score_a": _number(min=0, max=5),
            "score_b": _number(min=0, max=5),
            "mvp": _text(placeholder="ник игрока"),
            "progress": _text(),
        }

    MAP_LINE = re.compile(r"^(?P<name>.*?)(?:\s+(?P<a>\d{1,2})\s*[:\-]\s*(?P<b>\d{1,2}))?$")

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        lines = []
        for item in self.instance.maps or []:
            rounds = f" {item['score_a']}:{item['score_b']}" if item.get("score_a") is not None and item.get("score_b") is not None else ""
            lines.append(f"{item.get('name') or 'Карта'}{rounds}")
        self.fields["maps_text"].initial = "\n".join(lines)

    def clean_maps_text(self):
        old = list(self.instance.maps or [])
        team_a = self.cleaned_data.get("team_a") or self.instance.team_a
        team_b = self.cleaned_data.get("team_b") or self.instance.team_b
        maps = []
        for line in (raw.strip() for raw in (self.cleaned_data.get("maps_text") or "").splitlines()):
            if not line:
                continue
            match = self.MAP_LINE.match(line)
            name = (match.group("name") or "").strip()
            score_a = int(match.group("a")) if match.group("a") else None
            score_b = int(match.group("b")) if match.group("b") else None
            previous = old[len(maps)] if len(maps) < len(old) else {}
            if score_a is not None and score_b is not None and score_a != score_b:
                winner = team_a if score_a > score_b else team_b
            else:
                winner = previous.get("winner", "")
            maps.append({"name": "" if name.lower() == "карта" else name[:40], "winner": winner, "score_a": score_a, "score_b": score_b})
        return maps

    def save(self, commit=True):
        self.instance.maps = self.cleaned_data["maps_text"]
        if self.instance.score_a != self.instance.score_b:
            self.instance.winner = self.instance.team_a if self.instance.score_a > self.instance.score_b else self.instance.team_b
        return super().save(commit=commit)


class StageForm(forms.ModelForm):
    class Meta:
        model = Stage
        fields = ("order", "name", "kind", "starts_at", "ends_on", "pass_correct", "is_settled")
        widgets = {
            "order": _number(min=1, max=20),
            "name": _text(),
            "kind": _select(),
            "starts_at": _dt_widget(),
            "ends_on": _date_widget(),
            "pass_correct": _number(min=0, max=20),
        }

    def __init__(self, *args, major: Major, **kwargs):
        super().__init__(*args, **kwargs)
        self.major = major
        self.fields["starts_at"].input_formats = _DT_INPUTS
        if not self.instance.pk:
            self.initial.setdefault("order", (major.stages.order_by("-order").values_list("order", flat=True).first() or 0) + 1)

    def clean_kind(self):
        kind = self.cleaned_data["kind"]
        stage = self.instance
        if stage.pk and kind != stage.kind and (stage.swiss_picks.exists() or stage.bracket_picks.exists()):
            # Picks are stored in the format of the stage; switching it would silently drop them from scoring.
            raise forms.ValidationError("На стадию уже есть прогнозы — формат менять нельзя.")
        return kind

    def clean_order(self):
        order = self.cleaned_data["order"]
        if self.major.stages.exclude(pk=self.instance.pk).filter(order=order).exists():
            raise forms.ValidationError("Стадия с таким порядком уже есть.")
        return order

    def save(self, commit=True):
        self.instance.major = self.major
        return super().save(commit)


def _active_teams():
    return Team.objects.filter(is_active=True).order_by("name")


class StageTeamForm(forms.ModelForm):
    class Meta:
        model = StageTeam
        fields = ("seed", "team", "record")
        widgets = {"seed": _number(min=0, max=64), "team": _select(), "record": _select()}

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        teams = _active_teams()
        if self.instance.pk:
            teams = teams | Team.objects.filter(pk=self.instance.team_id)
        self.fields["team"].queryset = teams.distinct()


class _StageTeamFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if not self.instance.pk or not self.instance.swiss_picks.exists():
            return
        # Swiss picks point at teams: with picks made, a team can neither leave the stage nor be swapped. Records stay editable.
        for form in self.initial_forms:
            if self.can_delete and self._should_delete_form(form):
                raise forms.ValidationError("На стадию уже есть прогнозы — убирать команды нельзя.")
            if "team" in form.changed_data:
                raise forms.ValidationError("На стадию уже есть прогнозы — заменять команды нельзя.")
        for form in self.extra_forms:
            if form.has_changed() and not (self.can_delete and self._should_delete_form(form)):
                raise forms.ValidationError("На стадию уже есть прогнозы — добавлять команды нельзя.")


StageTeamFormSet = forms.inlineformset_factory(Stage, StageTeam, form=StageTeamForm, formset=_StageTeamFormSet, extra=4, can_delete=True)


class MatchForm(forms.ModelForm):
    class Meta:
        model = Match
        fields = ("round", "slot", "team_a", "team_b", "winner", "score", "starts_at")
        widgets = {
            "round": _select(),
            "slot": _number(min=1, max=8),
            "team_a": _select(),
            "team_b": _select(),
            "winner": _select(),
            "score": _text(placeholder="2:1", size=5),
            "starts_at": _dt_widget(),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        teams = _active_teams()
        used = [pk for pk in (self.instance.team_a_id, self.instance.team_b_id, self.instance.winner_id) if pk]
        if used:
            teams = teams | Team.objects.filter(pk__in=used)
        for name in ("team_a", "team_b", "winner"):
            self.fields[name].queryset = teams.distinct()
        self.fields["starts_at"].input_formats = _DT_INPUTS


class _MatchFormSet(forms.BaseInlineFormSet):
    def clean(self):
        super().clean()
        if not self.instance.pk or not self.instance.bracket_picks.exists():
            return
        # Bracket picks point at (round, slot): with picks made, a match can neither go away nor move.
        for form in self.initial_forms:
            if self.can_delete and self._should_delete_form(form):
                raise forms.ValidationError("На сетку уже есть прогнозы — удалять матчи нельзя.")
            if {"round", "slot"} & set(form.changed_data):
                raise forms.ValidationError("На сетку уже есть прогнозы — раунд и номер матча менять нельзя.")


MatchFormSet = forms.inlineformset_factory(Stage, Match, form=MatchForm, formset=_MatchFormSet, extra=0, can_delete=True)


class TeamForm(_SlugFromNameMixin, forms.ModelForm):
    class Meta:
        model = Team
        fields = ("name", "slug", "tag", "region", "logo", "logo_static", "is_active")
        widgets = {
            "name": _text(),
            "slug": _text(placeholder="из названия"),
            "tag": _text(),
            "region": _select(),
            "logo_static": _text(placeholder="img/majors/teams/navi.webp"),
        }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["slug"].required = False
        self.fields["slug"].help_text = "Пусто — из названия."


class RewardRuleForm(forms.ModelForm):
    item_name = forms.CharField(
        label="Скин", required=False, max_length=255, widget=_text(placeholder="AK-47 | Redline (Field-Tested)"),
        help_text="Точное market_hash_name из каталога.",
    )

    class Meta:
        model = RewardRule
        fields = ("title", "condition", "stage", "min_correct", "kind", "amount", "plan", "days", "effect_ref", "requires_approval", "is_active", "sort_order")
        widgets = {
            "title": _text(),
            "condition": _select(),
            "stage": _select(),
            "min_correct": _number(min=0, max=20),
            "kind": _select(),
            "amount": _number(min=0, step="0.01"),
            "plan": _select(),
            "days": _number(min=0, max=365),
            "effect_ref": _select(),
            "sort_order": _number(min=0, max=1000),
        }

    def __init__(self, *args, major: Major, **kwargs):
        super().__init__(*args, **kwargs)
        self.major = major
        self.instance.major = major
        self.fields["stage"].queryset = major.stages.order_by("order")
        self.fields["stage"].empty_label = "Любая стадия"
        from modules.v2.profile_customization.effects import reward_choices

        self.fields["effect_ref"] = forms.ChoiceField(
            label=RewardRule._meta.get_field("effect_ref").verbose_name, required=False,
            choices=[("", "—"), *reward_choices()], widget=_select(),
            help_text=RewardRule._meta.get_field("effect_ref").help_text,
        )
        if self.instance.item_id:
            self.initial.setdefault("item_name", self.instance.item.market_hash_name)

    # What a player is promised: fixed once the rule has grants, since a grant is delivered by the rule as it is then.
    FROZEN_WITH_GRANTS = ("condition", "stage", "min_correct", "kind", "amount", "plan", "days", "item_name", "effect_ref")

    def clean(self):
        cleaned = super().clean()
        if self.instance.pk and self.instance.grants.exists():
            changed = [name for name in self.FROZEN_WITH_GRANTS if name in self.changed_data]
            if changed:
                labels = ", ".join(str(self.fields[name].label) for name in changed)
                raise forms.ValidationError(f"Награду уже выдавали — условие и приз менять нельзя ({labels}). Выключите её и создайте новую.")
        return cleaned

    def add_error(self, field, error):
        # The model reports a missing skin on "item"; the form asks for it by name.
        if field is None and isinstance(error, forms.ValidationError) and hasattr(error, "error_dict") and "item" in error.error_dict:
            error.error_dict["item_name"] = error.error_dict.pop("item")
        super().add_error("item_name" if field == "item" else field, error)

    def clean_item_name(self):
        from modules.v2.steam_catalog.models import SteamItem

        name = (self.cleaned_data.get("item_name") or "").strip()
        if not name:
            self.instance.item = None
            return ""
        item = SteamItem.objects.filter(market_hash_name__iexact=name).first()
        if item is None:
            raise forms.ValidationError("В каталоге нет предмета с таким названием.")
        self.instance.item = item
        return item.market_hash_name
