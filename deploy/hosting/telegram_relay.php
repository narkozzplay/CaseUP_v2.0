<?php
/**
 * CaseUP Telegram relay: Telegram is blocked from the CaseUP server (Russia), but this hosting can reach it.
 *
 * Outbound: the CaseUP server POSTs a Bot API call here (?method=sendMessage, headers X-Relay-Token and
 * X-Bot-Token), the script forwards it to api.telegram.org and returns Telegram's answer as is.
 * Inbound: Telegram delivers bot updates here (?inbound=<webhook secret>), the script passes them on to the
 * site's webhook with the same secret header. Only api.telegram.org and SITE_URL are ever contacted.
 *
 * Setup: put the relay key below (the same value goes to TELEGRAM_RELAY_TOKEN — or STEAM_PRICE_RELAY_TOKEN
 * if you use one key for both relays — in .env.prod), check SITE_URL, upload next to steam_price.php
 * and set TELEGRAM_RELAY_URL=https://<this hosting>/telegram_relay.php on the server.
 */

const RELAY_TOKEN = 'ЗАМЕНИТЕ_НА_ДЛИННЫЙ_СЛУЧАЙНЫЙ_КЛЮЧ';
const SITE_URL = 'https://case-up.ru';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

function deny(int $code, string $error): void
{
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $error]);
    exit;
}

function forward(string $url, string $body, array $headers): void
{
    $ch = curl_init($url);
    curl_setopt_array($ch, [
        CURLOPT_POST => true,
        CURLOPT_POSTFIELDS => $body,
        CURLOPT_HTTPHEADER => $headers,
        CURLOPT_RETURNTRANSFER => true,
        CURLOPT_FOLLOWLOCATION => false,
        CURLOPT_CONNECTTIMEOUT => 5,
        CURLOPT_TIMEOUT => 20,
        CURLOPT_USERAGENT => 'CaseUP/telegram-relay',
    ]);
    $answer = curl_exec($ch);
    $code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
    curl_close($ch);
    if ($answer === false || $code === 0) {
        deny(502, 'upstream_unreachable');
    }
    http_response_code($code);
    echo $answer;
    exit;
}

if (($_SERVER['REQUEST_METHOD'] ?? '') !== 'POST') {
    deny(405, 'method_not_allowed');
}
$body = (string) file_get_contents('php://input', false, null, 0, 262144);

// Telegram -> site: bot updates (linking accounts with /start).
if (isset($_GET['inbound'])) {
    $secret = (string) $_GET['inbound'];
    $header = (string) ($_SERVER['HTTP_X_TELEGRAM_BOT_API_SECRET_TOKEN'] ?? '');
    if (!preg_match('/^[A-Za-z0-9_-]{16,128}$/', $secret) || !hash_equals($secret, $header)) {
        deny(403, 'forbidden');
    }
    forward(rtrim(SITE_URL, '/') . '/telegram/webhook/' . $secret . '/', $body, [
        'Content-Type: application/json',
        'X-Telegram-Bot-Api-Secret-Token: ' . $header,
    ]);
}

// Site -> Telegram: Bot API calls.
$given = (string) ($_SERVER['HTTP_X_RELAY_TOKEN'] ?? '');
if (strlen(RELAY_TOKEN) < 24 || strpos(RELAY_TOKEN, 'ЗАМЕНИТЕ') === 0 || !hash_equals(RELAY_TOKEN, $given)) {
    deny(403, 'forbidden');
}
$method = (string) ($_GET['method'] ?? '');
$bot = (string) ($_SERVER['HTTP_X_BOT_TOKEN'] ?? '');
if (!preg_match('/^[A-Za-z]{1,40}$/', $method) || !preg_match('/^\d{5,16}:[A-Za-z0-9_-]{30,64}$/', $bot)) {
    deny(400, 'bad_request');
}
forward('https://api.telegram.org/bot' . $bot . '/' . $method, $body, ['Content-Type: application/json']);
