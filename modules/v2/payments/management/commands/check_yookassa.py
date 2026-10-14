from urllib.parse import urlparse

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.urls import reverse

from modules.v2.payments.ip_allowlist import webhook_ip_check_enabled
from modules.v2.payments.providers import PaymentProviderError, get_provider
from modules.v2.payments.providers.yookassa import YooKassaProvider


class Command(BaseCommand):
    help = "Check the YooKassa keys against the API and print what the shop cabinet needs (webhook URL, mode)."

    def handle(self, *args, **options):
        active = get_provider()
        self.stdout.write(f"Активный провайдер пополнения: {active.title if active else 'нет (пополнение выключено)'}")

        if not YooKassaProvider.is_configured():
            raise CommandError(
                "YOOKASSA_SHOP_ID / YOOKASSA_SECRET_KEY не заданы. "
                "Возьмите их в личном кабинете ЮKassa: Интеграция → Ключи API (для теста — тестовый магазин)."
            )

        provider = YooKassaProvider()
        try:
            info = provider.get_account_info()
        except PaymentProviderError as exc:
            raise CommandError(f"Ключи не подошли: {exc}") from exc

        is_test = bool(info.get("test", provider.is_test_mode))
        self.stdout.write(self.style.SUCCESS("Ключи ЮKassa работают."))
        self.stdout.write(f"  Магазин (shopId): {info.get('account_id', provider.shop_id)}")
        self.stdout.write(f"  Режим: {'ТЕСТОВЫЙ — реальные деньги не списываются' if is_test else 'боевой — реальные платежи'}")
        if info.get("status"):
            self.stdout.write(f"  Статус магазина: {info['status']}")
        methods = info.get("payment_methods") or []
        if methods:
            self.stdout.write(f"  Способы оплаты: {', '.join(str(method) for method in methods)}")
        fiscal = info.get("fiscalization") or {}
        fiscal_enabled = fiscal.get("enabled", info.get("fiscalization_enabled"))
        if fiscal_enabled is not None:
            self.stdout.write(f"  Онлайн-касса (54-ФЗ): {'подключена' if fiscal_enabled else 'не подключена'}")
            if fiscal_enabled and not getattr(settings, "YOOKASSA_SEND_RECEIPT", False):
                self.stdout.write(self.style.WARNING("  Касса подключена, но YOOKASSA_SEND_RECEIPT=0: чеки не будут отправляться."))

        base_url = (getattr(settings, "SITE_BASE_URL", "") or "").rstrip("/")
        webhook_url = f"{base_url}{reverse('payments_public:webhook', args=[provider.code])}"
        self.stdout.write("")
        self.stdout.write("HTTP-уведомления (кабинет ЮKassa → Интеграция → HTTP-уведомления):")
        self.stdout.write(f"  URL: {webhook_url}")
        self.stdout.write("  События: payment.succeeded, payment.canceled")
        host = (urlparse(base_url).hostname or "").lower()
        if host in {"", "localhost", "127.0.0.1"} or host.endswith(".localhost"):
            self.stdout.write(
                self.style.WARNING(
                    "  SITE_BASE_URL локальный: ЮKassa не сможет отправить webhook. Для локального теста он не обязателен — "
                    "баланс зачислится при возврате на сайт или автосверкой Celery; либо поднимите ngrok."
                )
            )
        self.stdout.write(f"  Проверка IP отправителя webhook: {'включена' if webhook_ip_check_enabled() else 'выключена'}")

        if is_test and not settings.DEBUG:
            self.stdout.write(self.style.WARNING("Тестовые ключи при DEBUG=False: перед запуском для пользователей замените на боевые."))
