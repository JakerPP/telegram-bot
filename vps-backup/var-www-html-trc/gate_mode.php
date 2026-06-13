<?php
header('Content-Type: application/json; charset=utf-8');

$mode = strtolower(trim($_GET['mode'] ?? ''));

$map = [
    'full' => 'open',
    'open' => 'open',
    'partial' => 'partial',
    'pedestrian' => 'pedestrian',
    'close' => 'close',
    'stop' => 'stop',
    'status' => 'status'
];

if (!isset($map[$mode])) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "Invalid mode"], JSON_UNESCAPED_UNICODE);
    exit;
}

$envFile = '/opt/trc-tuya/gate_api.env';
$env = file_exists($envFile) ? parse_ini_file($envFile) : [];
$token = trim($env['GATE_API_TOKEN'] ?? '');

if ($token === '') {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "token missing"], JSON_UNESCAPED_UNICODE);
    exit;
}

$action = $map[$mode];
$url = 'http://127.0.0.1/trc/gate.php?action=' . urlencode($action) . '&token=' . urlencode($token);

$response = @file_get_contents($url);

if ($response === false) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "local gate request failed"], JSON_UNESCAPED_UNICODE);
    exit;
}

echo $response;
