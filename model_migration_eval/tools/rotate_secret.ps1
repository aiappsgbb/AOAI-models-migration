# =============================================================================
# rotate_secret.ps1 — Rotate the Service Principal secret and update .env
# =============================================================================
# Usage:  .\tools\rotate_secret.ps1
#
# What it does:
#   1. Reads AZURE_CLIENT_ID and AZURE_TENANT_ID from .env
#   2. Tests the current secret — if still valid, shows expiry and exits
#   3. If expired/invalid, rotates the secret via Azure CLI (az)
#   4. Validates the new secret can acquire a token
#   5. Updates .env in-place with the new AZURE_CLIENT_SECRET
#
# Prerequisites:  Azure CLI (az) installed and logged in (az login)
# =============================================================================

$ErrorActionPreference = "Stop"
$ENV_FILE = Join-Path $PSScriptRoot "..\\.env"

if (-not (Test-Path $ENV_FILE)) {
    Write-Host "ERROR: .env not found at $ENV_FILE" -ForegroundColor Red
    exit 1
}

# ── Parse .env ───────────────────────────────────────────────────────────────
function Read-EnvVar {
    param([string]$Path, [string]$Key)
    $match = Select-String -Path $Path -Pattern "^${Key}=(.+)$" | Select-Object -First 1
    if ($match) { return $match.Matches[0].Groups[1].Value.Trim() }
    return $null
}

$tenantId  = Read-EnvVar $ENV_FILE "AZURE_TENANT_ID"
$clientId  = Read-EnvVar $ENV_FILE "AZURE_CLIENT_ID"
$secret    = Read-EnvVar $ENV_FILE "AZURE_CLIENT_SECRET"

if (-not $tenantId -or -not $clientId) {
    Write-Host "ERROR: AZURE_TENANT_ID and AZURE_CLIENT_ID must be set in .env" -ForegroundColor Red
    Write-Host "Run deploy.ps1 first to create the Service Principal." -ForegroundColor Yellow
    exit 1
}

Write-Host ""
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Service Principal Secret Rotation"     -ForegroundColor Cyan
Write-Host "=======================================" -ForegroundColor Cyan
Write-Host "  Tenant ID : $tenantId"                 -ForegroundColor Gray
Write-Host "  Client ID : $clientId"                 -ForegroundColor Gray
Write-Host ""

# ── Test current secret ──────────────────────────────────────────────────────
function Test-Secret {
    param([string]$Tenant, [string]$Client, [string]$Secret)
    if (-not $Secret) { return $false }
    try {
        $body = "grant_type=client_credentials&client_id=$Client&client_secret=$([uri]::EscapeDataString($Secret))&scope=https%3A%2F%2Fcognitiveservices.azure.com%2F.default"
        $resp = Invoke-RestMethod -Uri "https://login.microsoftonline.com/$Tenant/oauth2/v2.0/token" `
            -Method POST -ContentType "application/x-www-form-urlencoded" -Body $body -ErrorAction Stop
        return [bool]$resp.access_token
    } catch {
        return $false
    }
}

if ($secret) {
    Write-Host "Testing current secret..." -ForegroundColor Cyan
    if (Test-Secret $tenantId $clientId $secret) {
        # Show credential expiry
        $creds = az ad app credential list --id $clientId --query "[].endDateTime" -o tsv 2>$null
        $nearest = $creds | Sort-Object | Select-Object -First 1
        if ($nearest) {
            $expiry = [datetime]::Parse($nearest)
            $daysLeft = [math]::Ceiling(($expiry - (Get-Date)).TotalDays)
            Write-Host "Current secret is VALID (expires $($expiry.ToString('yyyy-MM-dd')), ${daysLeft} days left)" -ForegroundColor Green
            if ($daysLeft -gt 7) {
                Write-Host "No rotation needed." -ForegroundColor Green
                exit 0
            }
            Write-Host "Expiring soon — rotating proactively." -ForegroundColor Yellow
        } else {
            Write-Host "Current secret is VALID" -ForegroundColor Green
            Write-Host "No rotation needed." -ForegroundColor Green
            exit 0
        }
    } else {
        Write-Host "Current secret is EXPIRED or INVALID" -ForegroundColor Yellow
    }
} else {
    Write-Host "No AZURE_CLIENT_SECRET in .env" -ForegroundColor Yellow
}

# ── Ensure Azure CLI is ready ────────────────────────────────────────────────
Write-Host ""
Write-Host "Rotating secret via Azure CLI..." -ForegroundColor Cyan

if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
    Write-Host "ERROR: Azure CLI (az) not found." -ForegroundColor Red
    Write-Host "Install from https://aka.ms/installazurecliwindows" -ForegroundColor Yellow
    exit 1
}

$acct = az account show 2>$null | ConvertFrom-Json
if (-not $acct) {
    Write-Host "Not logged in. Launching az login..." -ForegroundColor Yellow
    az login
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) {
        Write-Host "ERROR: Azure login failed." -ForegroundColor Red
        exit 1
    }
}
Write-Host "  Logged in as: $($acct.user.name) ($($acct.name))" -ForegroundColor Gray

# ── Rotate: try 90 → 30 → 7 day durations ───────────────────────────────────
$newPassword = $null
$validDays = 0

foreach ($days in @(90, 30, 7)) {
    $endDate = (Get-Date).ToUniversalTime().AddDays($days).ToString("yyyy-MM-ddTHH:mm:ssZ")
    Write-Host "  Trying ${days}-day credential (expires $endDate)..." -ForegroundColor Cyan
    $credJson = az ad app credential reset --id $clientId --end-date $endDate 2>&1
    $jsonOnly = ($credJson | Where-Object { $_ -notmatch '^(WARNING|ERROR|System\.)' }) -join ""
    $cred = $null
    try { $cred = $jsonOnly | ConvertFrom-Json } catch { }
    if ($cred -and $cred.password) {
        $newPassword = $cred.password
        $validDays = $days
        break
    }
    Write-Host "  ${days}-day rejected by tenant policy, trying shorter..." -ForegroundColor Yellow
}

if (-not $newPassword) {
    Write-Host "ERROR: All credential durations rejected." -ForegroundColor Red
    Write-Host "Create the secret manually in Azure Portal and update .env." -ForegroundColor Yellow
    exit 1
}

Write-Host "  New secret obtained (valid $validDays days)" -ForegroundColor Green

# ── Wait for AAD propagation and validate ────────────────────────────────────
Write-Host "  Waiting for AAD propagation..." -ForegroundColor Gray
$validated = $false
for ($i = 1; $i -le 6; $i++) {
    Start-Sleep -Seconds 5
    if (Test-Secret $tenantId $clientId $newPassword) {
        $validated = $true
        break
    }
    Write-Host "  Attempt $i/6 — not yet propagated..." -ForegroundColor Gray
}

if (-not $validated) {
    Write-Host "WARNING: Could not validate new secret yet (may need more time)." -ForegroundColor Yellow
    Write-Host "The secret has been generated — updating .env anyway." -ForegroundColor Yellow
}

# ── Update .env ──────────────────────────────────────────────────────────────
$envLines = Get-Content $ENV_FILE -Encoding UTF8
$updatedLines = $envLines | ForEach-Object {
    if ($_ -match '^AZURE_CLIENT_SECRET=') {
        "AZURE_CLIENT_SECRET=$newPassword"
    } else {
        $_
    }
}

# If AZURE_CLIENT_SECRET line didn't exist, add it after AZURE_CLIENT_ID
if (-not ($envLines | Where-Object { $_ -match '^AZURE_CLIENT_SECRET=' })) {
    $result = @()
    foreach ($line in $updatedLines) {
        $result += $line
        if ($line -match '^AZURE_CLIENT_ID=') {
            $result += "AZURE_CLIENT_SECRET=$newPassword"
        }
    }
    $updatedLines = $result
}

$updatedLines | Set-Content $ENV_FILE -Encoding UTF8

# ── Summary ──────────────────────────────────────────────────────────────────
$expiryDate = (Get-Date).AddDays($validDays).ToString("yyyy-MM-dd")
Write-Host ""
Write-Host "=======================================" -ForegroundColor Green
Write-Host "  Secret rotated successfully!" -ForegroundColor Green
Write-Host "  Expires: $expiryDate ($validDays days)" -ForegroundColor Green
Write-Host "  .env updated" -ForegroundColor Green
Write-Host "=======================================" -ForegroundColor Green
Write-Host ""
Write-Host "Restart 'python app.py' to pick up the new credentials." -ForegroundColor Cyan
