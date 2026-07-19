<?php
/**
 * Receives one clip + description pair from social-upload.html and saves it
 * into socialClips/pending/ as <base>.mp4 + <base>.txt (same base name).
 *
 * Deployed manually to the TigerTech subdomain root next to socialClips/.
 * Follows the existing dojo upload pattern: move_uploaded_file() with
 * filename sanitization and a timestamp suffix.
 *
 * Note: the server must allow large POSTs. In cPanel set upload_max_filesize
 * and post_max_size to at least 512M (MultiPHP INI Editor).
 */

header('Content-Type: application/json');

$pendingDir = __DIR__ . '/socialClips/pending/';
$maxBytes = 500 * 1024 * 1024;

function fail(string $msg, int $code = 400): void
{
    http_response_code($code);
    echo json_encode(['ok' => false, 'error' => $msg]);
    exit;
}

if ($_SERVER['REQUEST_METHOD'] !== 'POST') {
    fail('POST only', 405);
}

// A large upload that exceeds post_max_size arrives as an empty request.
if (empty($_FILES) && empty($_POST)) {
    fail('Upload too large for the server limits (check post_max_size / upload_max_filesize).', 413);
}

if (!isset($_FILES['video'])) {
    fail('No video file received.');
}
if ($_FILES['video']['error'] !== UPLOAD_ERR_OK) {
    fail('Upload failed (PHP error code ' . $_FILES['video']['error'] . ').');
}

$description = trim($_POST['description'] ?? '');
if ($description === '') {
    fail('Description is required.');
}

if ($_FILES['video']['size'] > $maxBytes) {
    fail('File is over 500 MB.');
}

$originalName = $_FILES['video']['name'];
if (!preg_match('/\.mp4$/i', $originalName)) {
    fail('Only MP4 files are accepted.');
}

if (!is_dir($pendingDir)) {
    fail('Server misconfiguration: pending directory not found.', 500);
}

// Sanitize the base name and add a timestamp suffix (existing site pattern).
$base = pathinfo($originalName, PATHINFO_FILENAME);
$base = preg_replace('/[^a-zA-Z0-9-_]/', '-', $base);
if ($base === '' || $base === null) {
    $base = 'clip';
}
$base .= '-' . date('Ymd-His');

// Guard against a same-second collision.
if (file_exists($pendingDir . $base . '.mp4')) {
    $base .= '-' . substr(uniqid(), -5);
}

$videoPath = $pendingDir . $base . '.mp4';
$textPath  = $pendingDir . $base . '.txt';

// Write the .txt first: the pipeline only processes complete pairs keyed on
// the .mp4, so the pair becomes visible only once the video lands.
if (file_put_contents($textPath, $description) === false) {
    fail('Could not save the description.', 500);
}

if (!move_uploaded_file($_FILES['video']['tmp_name'], $videoPath)) {
    @unlink($textPath);
    fail('Could not save the video.', 500);
}

echo json_encode(['ok' => true, 'name' => $base . '.mp4']);
