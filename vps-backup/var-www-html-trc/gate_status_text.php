<?php
header('Content-Type: text/plain; charset=utf-8');

$envFile = '/opt/trc-tuya/gate_api.env';
$statusFile = '/var/www/html/trc/status.json';

$env = file_exists($envFile) ? parse_ini_file($envFile) : [];
$expectedToken = trim($env['GATE_API_TOKEN'] ?? '');

function get_header_value($name) {
    $key = 'HTTP_' . strtoupper(str_replace('-', '_', $name));

    if (isset($_SERVER[$key])) {
        return trim($_SERVER[$key]);
    }

    if (function_exists('getallheaders')) {
        foreach (getallheaders() as $k => $v) {
            if (strtolower($k) === strtolower($name)) {
                return trim($v);
            }
        }
    }

    return '';
}

$providedToken = get_header_value('X-TRC-Token');

if ($providedToken === '') {
    $providedToken = trim($_GET['token'] ?? '');
}

if ($expectedToken === '' || !hash_equals($expectedToken, $providedToken)) {
    http_response_code(403);
    echo "Доступ запрещён";
    exit;
}

if (!file_exists($statusFile)) {
    echo "Статус неизвестен";
    exit;
}

$data = json_decode(file_get_contents($statusFile), true);

if (!is_array($data)) {
    echo "Статус неизвестен";
    exit;
}

$gate = strtoupper((string)($data['gate'] ?? 'UNKNOWN'));
$motion = strtoupper((string)($data['gate_motion'] ?? ''));

if ($gate === 'CLOSED' || $motion === 'CLOSED') {
    echo "Закрыто";
    exit;
}

if ($gate === 'OPENED' || $gate === 'OPEN' || $motion === 'OPENED' || $motion === 'OPEN') {
    echo "Открыто";
    exit;
}

if ($motion === 'OPENING') {
    echo "Открываются";
    exit;
}

if ($motion === 'CLOSING') {
    echo "Закрываются";
    exit;
}

echo "Частично открыто";
