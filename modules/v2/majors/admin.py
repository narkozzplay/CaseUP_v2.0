from django import forms
from django.contrib import admin, messages
from django.utils.html import format_html

from .models import BracketPick, Major, Match, RewardGrant, RewardRule, Stage, StageTeam, SwissPick, Team
from .services import advance_winners, approve_grant, mark_item_sent, reject_grant, settle_major, trade_url


@admin.register(Team)
class TeamAdmin(admin.ModelAdmin):
    list_display = ("logo_preview", "name", "tag", "region", "is_active")
    list_display_links = ("logo_preview", "name")
    list_filter = ("region", "is_active")
    search_fields = ("name", "tag")
    prepopulated_fields = {"slug": ("name",)}

    @admin.display(description="Лого")
    def logo_preview(self, obj):
        if not obj.logo_url:
            return "—"
        return format_html('<img src="{}" alt="" style="width:32px;height:32px;object-fit:contain">', obj.logo_url)


class StageInline(admin.TabularInline):
    model = Stage
    extra = 0
    fields = ("order", "name", "kind", "starts_at", "ends_on", "pass_correct", "is_settled")
    show_change_link = True


class RewardRuleInlineForm(forms.ModelForm):
    effect_ref = forms.ChoiceField(label="Оформление", required=False)

    class Meta:
        model = RewardRule
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from modules.v2.profile_customization.effects import reward_choices

        self.fields["effect_ref"].choices = [("", "—"), *reward_choices()]


class RewardRuleInline(admin.TabularInline):
    model = RewardRule
    form = RewardRuleInlineForm
    extra = 0
    fields = ("sort_order", "title", "condition", "stage", "min_correct", "kind", "amount", "plan", "days", "item", "effect_ref", "requires_approval", "is_active")
    raw_id_fields = ("item",)


@admin.register(Major)
class MajorAdmin(admin.ModelAdmin):
    list_display = ("name", "starts_at", "ends_on", "is_published")
    prepopulated_fields = {"slug": ("name",)}
    inlines = (StageInline, RewardRuleInline)
    actions = ("settle",)

    @admin.action(description="Подвести итоги Pick'em и выдать награды")
    def settle(self, request, queryset):
        for major in queryset:
            report = settle_major(major)
            self.message_user(
                request,
                f"{major.name}: выдано {report.granted}, ждут проверки {report.pending}, пропущено (нет Steam или бан) {report.skipped}.",
                messages.SUCCESS,
            )


class StageTeamInline(admin.TabularInline):
    model = StageTeam
    extra = 0
    fields = ("seed", "team", "record")
    autocomplete_fields = ("team",)


class MatchInline(admin.TabularInline):
    model = Match
    extra = 0
    fields = ("round", "slot", "team_a", "team_b", "winner", "score", "starts_at")
    autocomplete_fields = ("team_a", "team_b", "winner")


@admin.register(Stage)
class StageAdmin(admin.ModelAdmin):
    list_display = ("__str__", "kind", "starts_at", "is_settled")
    list_filter = ("major", "kind", "is_settled")
    inlines = (StageTeamInline, MatchInline)

    def save_related(self, request, form, formsets, change):
        super().save_related(request, form, formsets, change)
        if not form.instance.is_swiss:
            # Winners move on to the next round by themselves.
            advance_winners(form.instance)


@admin.register(RewardGrant)
class RewardGrantAdmin(admin.ModelAdmin):
    list_display = ("created_at", "user", "rule", "stage", "status", "trade_link", "tax_data", "decided_at")
    list_filter = ("status", "rule__major", "rule")
    search_fields = ("user__username",)
    raw_id_fields = ("user", "decided_by")
    actions = ("approve", "mark_sent", "reject")

    @admin.display(description="Трейд-ссылка")
    def trade_link(self, obj):
        if obj.rule.kind != RewardRule.Kind.ITEM:
            return ""
        url = trade_url(obj.user)
        return format_html('<a href="{}" target="_blank" rel="noopener">открыть</a>', url) if url else "нет ссылки"

    @admin.display(description="НДФЛ")
    def tax_data(self, obj):
        return "нужны ФИО и ИНН" if obj.rule.needs_tax_data else ""

    @admin.action(description="Скин отправлен трейдом")
    def mark_sent(self, request, queryset):
        done = sum(1 for grant in queryset if mark_item_sent(grant, by=request.user))
        self.message_user(request, f"Отмечено отправленными: {done}.", messages.SUCCESS)

    @admin.action(description="Одобрить и выдать")
    def approve(self, request, queryset):
        done = sum(1 for grant in queryset if approve_grant(grant, by=request.user))
        self.message_user(request, f"Выдано наград: {done}.", messages.SUCCESS)

    @admin.action(description="Отклонить")
    def reject(self, request, queryset):
        done = sum(1 for grant in queryset if reject_grant(grant, by=request.user, note="Отклонено в админке"))
        self.message_user(request, f"Отклонено: {done}.", messages.WARNING)


@admin.register(SwissPick)
class SwissPickAdmin(admin.ModelAdmin):
    list_display = ("user", "stage", "team", "pick", "updated_at")
    list_filter = ("stage__major", "stage", "pick")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)


@admin.register(BracketPick)
class BracketPickAdmin(admin.ModelAdmin):
    list_display = ("user", "stage", "round", "slot", "team", "updated_at")
    list_filter = ("stage__major", "round")
    search_fields = ("user__username",)
    raw_id_fields = ("user",)
