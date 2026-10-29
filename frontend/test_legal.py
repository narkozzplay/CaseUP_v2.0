from django.test import TestCase, override_settings
from django.urls import reverse

from frontend.legal import AGREEMENT_VERSION_LABEL
from modules.v2.system.services import get_site_configuration


class AgreementPageTests(TestCase):
    def test_agreement_is_public_and_versioned(self):
        response = self.client.get(reverse("frontend:agreement"))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Пользовательское соглашение")
        self.assertContains(response, f"Редакция от {AGREEMENT_VERSION_LABEL}")
        # CaseUP specifics: simulator items are not Steam items, no withdrawal yet, refunds of unspent money.
        self.assertContains(response, "не является предметом Steam")
        self.assertContains(response, "вывод Виртуальных предметов в Steam не предусмотрен")
        self.assertContains(response, "Неизрасходованные средства")

    def test_owner_placeholder_until_filled_in(self):
        response = self.client.get(reverse("frontend:agreement"))
        self.assertContains(response, "Сведения о владельце сайта ещё не заполнены")

        config = get_site_configuration()
        config.legal_owner_name = "ИП Тестов Тест Тестович"
        config.legal_owner_details = "ИНН 000000000000"
        config.support_email = "support@caseup.test"
        config.save()

        response = self.client.get(reverse("frontend:agreement"))
        self.assertNotContains(response, "Сведения о владельце сайта ещё не заполнены")
        self.assertContains(response, "ИП Тестов Тест Тестович")
        self.assertContains(response, "ИНН 000000000000")
        self.assertContains(response, "mailto:support@caseup.test")

    def test_footer_links_the_agreement(self):
        response = self.client.get(reverse("frontend:home"))
        self.assertContains(response, reverse("frontend:agreement"))


class LegalOwnerDeployCheckTests(TestCase):
    def test_warns_until_owner_is_named(self):
        from modules.v2.system.checks import check_legal_owner_is_published

        get_site_configuration()
        with override_settings(DEBUG=False):
            self.assertEqual([w.id for w in check_legal_owner_is_published(None)], ["caseup.W003"])
            config = get_site_configuration()
            config.legal_owner_name = "ИП Тестов"
            config.save()
            self.assertEqual(check_legal_owner_is_published(None), [])


class LegalSectionTests(TestCase):
    DOCUMENTS = (
        "frontend:agreement",
        "frontend:privacy",
        "frontend:pd_consent",
        "frontend:cookies",
        "frontend:withdrawal",
        "frontend:pickem_rules",
    )

    def test_all_documents_are_public_and_cross_linked(self):
        for name in self.DOCUMENTS:
            response = self.client.get(reverse(name))
            self.assertEqual(response.status_code, 200, name)
            for other in self.DOCUMENTS:
                self.assertContains(response, reverse(other))

    def test_only_contest_prizes_are_withdrawn_to_steam(self):
        response = self.client.get(reverse("frontend:withdrawal"))
        self.assertContains(response, "В Steam выводятся только <strong>Призы</strong>")
        self.assertContains(response, "Виртуальные предметы из Симулятора кейсов")
        # Withdrawal terms are part of the agreement, which points to them.
        self.assertContains(self.client.get(reverse("frontend:agreement")), reverse("frontend:withdrawal"))

    def test_pickem_is_a_free_contest(self):
        response = self.client.get(reverse("frontend:pickem_rules"))
        self.assertContains(response, "Участие бесплатное.")
        self.assertContains(response, "не является азартной игрой, пари или лотереей")

    def test_privacy_policy_names_cross_border_transfer_to_steam(self):
        self.assertContains(self.client.get(reverse("frontend:privacy")), "трансграничную передачу")

    def test_cookie_policy_lists_the_cookies_actually_set(self):
        from django.conf import settings

        response = self.client.get(reverse("frontend:cookies"))
        for cookie in (settings.SESSION_COOKIE_NAME, settings.CSRF_COOKIE_NAME, settings.LANGUAGE_COOKIE_NAME):
            self.assertContains(response, f"<code>{cookie}</code>")


class PaymentServiceNameTests(TestCase):
    @override_settings(PAYMENTS_PROVIDER="freekassa", FREEKASSA_MERCHANT_ID="1", FREEKASSA_SECRET_1="a", FREEKASSA_SECRET_2="b")
    def test_documents_name_freekassa_when_top_ups_go_through_it(self):
        for name in ("frontend:agreement", "frontend:privacy"):
            response = self.client.get(reverse(name))
            self.assertContains(response, "FreeKassa (freekassa.net)")
            self.assertNotContains(response, "ЮKassa")

    @override_settings(PAYMENTS_PROVIDER="yookassa", YOOKASSA_SHOP_ID="123456", YOOKASSA_SECRET_KEY="test_secret")
    def test_documents_follow_the_configured_provider(self):
        for name in ("frontend:agreement", "frontend:privacy"):
            self.assertContains(self.client.get(reverse(name)), "ЮKassa (ООО НКО «ЮМани»)")


class CurrentTermsTests(TestCase):
    def test_commission_follows_the_site_setting(self):
        from decimal import Decimal

        config = get_site_configuration()
        config.topup_fee_percent = Decimal("10.00")
        config.save()
        response = self.client.get(reverse("frontend:agreement"))
        self.assertContains(response, "текущий размер — 10%")
        self.assertNotContains(response, "8,5%")

    def test_telegram_bot_and_payouts_are_described(self):
        self.assertContains(self.client.get(reverse("frontend:agreement")), "Telegram-бот")
        privacy = self.client.get(reverse("frontend:privacy"))
        self.assertContains(privacy, "Данные для выплат с Рыночного баланса")
        self.assertContains(privacy, "привязка бота означает трансграничную передачу")
        self.assertContains(self.client.get(reverse("frontend:pd_consent")), "при привязке Telegram-бота")
