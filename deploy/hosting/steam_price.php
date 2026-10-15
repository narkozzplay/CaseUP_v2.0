<?php
/**
 * CaseUP price relay: asks Steam for one item price from this hosting's IP address.
 *
 * The CaseUP server sends part of its price requests here, so Steam sees two IP addresses and the
 * price queue runs about twice as fast. Only the fixed Steam priceoverview address can be requested,
 * so this script cannot be used as an open proxy.
 *
 * Setup: put a long random key below (the same value goes to STEAM_PRICE_RELAY_TOKEN in .env.prod on the
 * server), upload the file to the hosting site folder and add its address to STEAM_PRICE_RELAY_URLS.
 */

const RELAY_TOKEN = 'ЗАМЕНИТЕ_НА_ДЛИННЫЙ_СЛУЧАЙНЫЙ_КЛЮЧ';

header('Content-Type: application/json; charset=utf-8');
header('Cache-Control: no-store');

$given = isset($_SERVER['HTTP_X_RELAY_TOKEN']) ? (string) $_SERVER['HTTP_X_RELAY_TOKEN'] : '';
if (strlen(RELAY_TOKEN) < 24 || strpos(RELAY_TOKEN, 'ЗАМЕНИТЕ') === 0 || !hash_equals(RELAY_TOKEN, $given)) {
    http_response_code(403);
    echo '{"success":false,"error":"forbidden"}';
    exit;
}

$name = isset($_GET['market_hash_name']) ? (string) $_GET['market_hash_name'] : '';
if ($name === '' || strlen($name) > 255) {
    http_response_code(400);
    echo '{"success":false,"error":"bad_request"}';
    exit;
}

$url = 'https://steamcommunity.com/market/priceoverview/?appid=730&currency=5&market_hash_name=' . rawurlencode($name);
$ch = curl_init($url);
curl_setopt_array($ch, [
    CURLOPT_RETURNTRANSFER => true,
    CURLOPT_FOLLOWLOCATION => false,
    CURLOPT_CONNECTTIMEOUT => 5,
    CURLOPT_TIMEOUT => 15,
    CURLOPT_USERAGENT => 'CaseUP/price-relay',
    CURLOPT_HTTPHEADER => ['Accept-Language: ru,en;q=0.9'],
]);
$body = curl_exec($ch);
$code = (int) curl_getinfo($ch, CURLINFO_HTTP_CODE);
curl_close($ch);

if ($body === false || $code === 0) {
    http_response_code(502);
    echo '{"success":false,"error":"steam_unreachable"}';
    exit;
}

http_response_code($code);
echo $body;
