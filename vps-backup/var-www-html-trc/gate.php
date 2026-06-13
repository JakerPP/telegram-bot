<?php
header('Content-Type: application/json; charset=utf-8');

$envFile = '/opt/trc-tuya/gate_api.env';

if (!file_exists($envFile)) {
    http_response_code(500);
    echo json_encode(["ok" => false, "error" => "Gate API env file missing"], JSON_UNESCAPED_UNICODE);
    exit;
}

$env = parse_ini_file($envFile);
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
    echo json_encode(["ok" => false, "error" => "Forbidden"], JSON_UNESCAPED_UNICODE);
    exit;
}

$action = strtolower(trim($_GET['action'] ?? ''));

$allowed = ['open', 'full', 'partial', 'pedestrian', 'close', 'stop', 'status'];

if (!in_array($action, $allowed, true)) {
    http_response_code(400);
    echo json_encode(["ok" => false, "error" => "Invalid action"], JSON_UNESCAPED_UNICODE);
    exit;
}

$remoteIp = $_SERVER['REMOTE_ADDR'] ?? '';

putenv('TRC_GATE_SOURCE=http');
putenv('TRC_GATE_USER=' . $remoteIp);
putenv('TRC_GATE_REMOTE_IP=' . $remoteIp);

$cmd = 'cd /opt/trc-tuya && /opt/trc-tuya/gate_control.py ' . escapeshellarg($action) . ' 2>&1';

$output = [];
$returnCode = 0;

exec($cmd, $output, $returnCode);

$response = implode("\n", $output);

if ($returnCode !== 0) {
    http_response_code(500);
}

echo $response;
