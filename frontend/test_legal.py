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
