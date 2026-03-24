# =============================================================================
# backup_user_data.ps1 — Download all user data via the app's REST API
# =============================================================================
#
# Downloads prompts, synthetic test data, topics, version history, and
# evaluation results for a given user from a running instance of the
# Model Migration Evaluation app (Docker or Azure Container Apps).
#
# Usage:
#   # Backup from local Docker / dev server
#   .\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com
#
#   # Backup from Azure Container Apps
#   .\tools\backup_user_data.ps1 -BaseUrl https://my-app.azurecontainerapps.io -Email user@test.com
#
#   # Restore to another environment
#   .\tools\backup_user_data.ps1 -BaseUrl https://target-app.azurecontainerapps.io -Email user@test.com -Restore -BackupDir .\backups\user_at_test_com_20260314_153000
#
#   # Specify OTP code (when code_verification is enabled)
#   .\tools\backup_user_data.ps1 -BaseUrl http://localhost:5000 -Email user@test.com -OtpCode 123456
#
# =============================================================================

[CmdletBinding()]
param(
    [Parameter(Mandatory)]
    [string]$BaseUrl,

    [Parameter(Mandatory)]
    [string]$Email,

    [string]$OtpCode = "",

    [string]$OutputDir = ".\backups",

    [switch]$Restore,

    [string]$BackupDir = "",

    [switch]$SkipResults,

    [switch]$DryRun
)

$ErrorActionPreference = "Stop"

# ── Helpers ─────────────────────────────────────────────────────────────────

function Write-Step  { param([string]$msg) Write-Host "  ▸ $msg" -ForegroundColor Cyan }
function Write-Ok    { param([string]$msg) Write-Host "  ✔ $msg" -ForegroundColor Green }
function Write-Warn  { param([string]$msg) Write-Host "  ⚠ $msg" -ForegroundColor Yellow }
function Write-Err   { param([string]$msg) Write-Host "  ✗ $msg" -ForegroundColor Red }

function Invoke-Api {
    <#
    .SYNOPSIS
        Call an API endpoint using the authenticated session.
    #>
    param(
        [string]$Path,
        [string]$Method = "GET",
        [object]$Body = $null,
        [switch]$RawResponse
    )
    $uri = "$BaseUrl$Path"
    $params = @{
        Uri             = $uri
        Method          = $Method
        WebSession      = $script:session
        ContentType     = "application/json"
        UseBasicParsing = $true
    }
    if ($Body) {
        $params["Body"] = ($Body | ConvertTo-Json -Depth 20 -Compress)
    }
    try {
        $resp = Invoke-WebRequest @params
        if ($RawResponse) { return $resp }
        return ($resp.Content | ConvertFrom-Json)
    }
    catch {
        $status = $_.Exception.Response.StatusCode.value__
        Write-Err "API call failed: $Method $Path → HTTP $status"
        Write-Err $_.Exception.Message
        return $null
    }
}

function Ensure-Dir {
    param([string]$Path)
    if (-not (Test-Path $Path)) {
        New-Item -ItemType Directory -Path $Path -Force | Out-Null
    }
}

function Save-JsonFile {
    param([string]$FilePath, [object]$Data)
    $Data | ConvertTo-Json -Depth 50 | Set-Content -Path $FilePath -Encoding UTF8
}

function Save-TextFile {
    param([string]$FilePath, [string]$Content)
    # Use .NET to write without BOM (matches Python's utf-8 output)
    [System.IO.File]::WriteAllText($FilePath, $Content, [System.Text.UTF8Encoding]::new($false))
}

# ── Authentication ──────────────────────────────────────────────────────────

function Connect-App {
    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  Authenticating to $BaseUrl" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan

    # Step 1: Login request
    Write-Step "Sending login request for $Email ..."
    $loginBody = @{ email = $Email } | ConvertTo-Json -Compress
    $loginResp = Invoke-WebRequest -Uri "$BaseUrl/api/auth/login" `
        -Method POST -ContentType "application/json" `
        -Body $loginBody -SessionVariable ws -UseBasicParsing
    $script:session = $ws
    $loginData = $loginResp.Content | ConvertFrom-Json

    if ($loginData.status -eq "authenticated") {
        Write-Ok "Authenticated directly (code_verification=false)"
        return $true
    }

    if ($loginData.status -eq "code_sent") {
        # Step 2: OTP verification required
        if (-not $OtpCode) {
            Write-Warn "OTP code sent to $Email."
            Write-Warn "Check the console logs (docker logs <container>) or email."
            $OtpCode = Read-Host "  Enter the 6-digit code"
        }
        Write-Step "Verifying OTP code ..."
        $verifyBody = @{ email = $Email; code = $OtpCode } | ConvertTo-Json -Compress
        $verifyResp = Invoke-WebRequest -Uri "$BaseUrl/api/auth/verify" `
            -Method POST -ContentType "application/json" `
            -Body $verifyBody -WebSession $script:session -UseBasicParsing
        $verifyData = $verifyResp.Content | ConvertFrom-Json
        if ($verifyData.status -eq "authenticated") {
            Write-Ok "Authenticated via OTP"
            return $true
        }
        Write-Err "OTP verification failed: $($verifyData.error)"
        return $false
    }

    Write-Err "Unexpected login response: $($loginData | ConvertTo-Json -Compress)"
    return $false
}

# ── Backup ──────────────────────────────────────────────────────────────────

function Invoke-Backup {
    # Verify session
    $me = Invoke-Api "/api/auth/me"
    if (-not $me -or -not $me.authenticated) {
        Write-Err "Not authenticated. Aborting."
        return
    }
    $userId = $me.user_id
    $userEmail = $me.email

    # Create backup directory with timestamp
    $ts = Get-Date -Format "yyyyMMdd_HHmmss"
    $backupRoot = Join-Path $OutputDir "${userId}_${ts}"
    Ensure-Dir $backupRoot

    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan
    Write-Host "  Backing up user: $userEmail ($userId)" -ForegroundColor Cyan
    Write-Host "  Output: $backupRoot" -ForegroundColor Cyan
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Cyan

    # Save metadata
    $manifest = @{
        user_id    = $userId
        email      = $userEmail
        timestamp  = (Get-Date -Format "o")
        base_url   = $BaseUrl
        version    = "1.0"
    }
    Save-JsonFile (Join-Path $backupRoot "manifest.json") $manifest

    # ── 1. Prompts ──────────────────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Prompts ──────────────────────────────────────" -ForegroundColor Yellow

    $promptList = Invoke-Api "/api/prompts"
    $promptCount = 0
    if ($promptList -and $promptList.prompts) {
        foreach ($p in $promptList.prompts) {
            $model = $p.model
            $ptype = $p.prompt_type
            Write-Step "$model / $ptype"

            $detail = Invoke-Api "/api/prompts/$model/$ptype"
            if ($detail -and $detail.content) {
                $dir = Join-Path $backupRoot "prompts" $model
                Ensure-Dir $dir
                Save-TextFile (Join-Path $dir "$ptype.md") $detail.content
                $promptCount++
            }
        }
    }
    Write-Ok "$promptCount prompts downloaded"

    # ── 2. Synthetic test data ──────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Synthetic Data ───────────────────────────────" -ForegroundColor Yellow

    $dataTypes = @("classification", "dialog", "general", "rag", "tool_calling")
    $dataCount = 0
    foreach ($dtype in $dataTypes) {
        Write-Step "$dtype"
        $raw = Invoke-Api "/api/data/raw/$dtype"
        if ($raw -and $raw.data) {
            $dir = Join-Path $backupRoot "synthetic" $dtype
            Ensure-Dir $dir
            Save-JsonFile (Join-Path $dir "data.json") $raw.data
            Write-Ok "  $($raw.count) scenarios"
            $dataCount += $raw.count
        } else {
            Write-Warn "  empty or missing"
        }
    }
    Write-Ok "$dataCount total scenarios downloaded"

    # ── 3. Topics (archived) ────────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Topics ───────────────────────────────────────" -ForegroundColor Yellow

    $topics = Invoke-Api "/api/topics"
    $topicCount = 0
    if ($topics -and $topics.topics) {
        foreach ($t in $topics.topics) {
            $slug = $t.slug
            if (-not $slug) { continue }
            Write-Step "Topic: $slug"

            # Save topic metadata
            $topicDir = Join-Path $backupRoot "topics" $slug
            Ensure-Dir $topicDir
            Save-JsonFile (Join-Path $topicDir "topic_meta.json") $t

            # Download topic prompts (each model/type from the archived topic)
            if ($promptList -and $promptList.prompts) {
                foreach ($p in $promptList.prompts) {
                    $model = $p.model
                    $ptype = $p.prompt_type
                    # The app serves archived topic prompts via the same
                    # endpoint when the topic is active — so we download
                    # the topic's data files which are accessible via the
                    # raw endpoint with ?topic= query param.
                }
            }

            # Download topic data for each type
            foreach ($dtype in $dataTypes) {
                $raw = Invoke-Api "/api/data/raw/${dtype}?topic=$slug"
                if ($raw -and $raw.data -and $raw.count -gt 0) {
                    $dataDir = Join-Path $topicDir "data" $dtype
                    Ensure-Dir $dataDir
                    Save-JsonFile (Join-Path $dataDir "data.json") $raw.data
                }
            }
            $topicCount++
        }
    }
    Write-Ok "$topicCount topics downloaded"

    # ── 4. Version history ──────────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Version History ──────────────────────────────" -ForegroundColor Yellow

    $history = Invoke-Api "/api/prompts/history"
    $versionCount = 0
    if ($history -and $history.versions) {
        $historyDir = Join-Path $backupRoot "history"
        Ensure-Dir $historyDir

        # Save index
        Save-JsonFile (Join-Path $historyDir "versions.json") $history.versions

        # Download each version's content
        foreach ($v in $history.versions) {
            $vid = $v.id
            if (-not $vid) { continue }
            $detail = Invoke-Api "/api/prompts/history/$vid"
            if ($detail -and $detail.content) {
                $fname = $v.filename
                if (-not $fname) { $fname = "${vid}.md" }
                Save-TextFile (Join-Path $historyDir $fname) $detail.content
                $versionCount++
            }
        }
    }
    Write-Ok "$versionCount version snapshots downloaded"

    # ── 5. Evaluation results ───────────────────────────────────────
    if (-not $SkipResults) {
        Write-Host ""
        Write-Host "  ── Evaluation Results ───────────────────────────" -ForegroundColor Yellow

        $resultsList = Invoke-Api "/api/results"
        $resultCount = 0
        if ($resultsList -and $resultsList.results) {
            $resultsDir = Join-Path $backupRoot "results"
            Ensure-Dir $resultsDir

            foreach ($r in $resultsList.results) {
                $fname = $r.filename
                if (-not $fname) { continue }
                Write-Step "$fname"
                $full = Invoke-Api "/api/results/$fname"
                if ($full) {
                    Save-JsonFile (Join-Path $resultsDir $fname) $full
                    $resultCount++
                }
            }
        }
        Write-Ok "$resultCount results downloaded"
    } else {
        Write-Warn "Skipping results (--SkipResults)"
    }

    # ── Summary ─────────────────────────────────────────────────────
    $manifest.completed = (Get-Date -Format "o")
    $manifest.counts = @{
        prompts  = $promptCount
        scenarios = $dataCount
        topics   = $topicCount
        versions = $versionCount
        results  = if ($SkipResults) { "skipped" } else { $resultCount }
    }
    Save-JsonFile (Join-Path $backupRoot "manifest.json") $manifest

    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  ✅ Backup complete!" -ForegroundColor Green
    Write-Host "  📁 $backupRoot" -ForegroundColor Green
    Write-Host "     Prompts:   $promptCount" -ForegroundColor Gray
    Write-Host "     Scenarios: $dataCount" -ForegroundColor Gray
    Write-Host "     Topics:    $topicCount" -ForegroundColor Gray
    Write-Host "     Versions:  $versionCount" -ForegroundColor Gray
    if (-not $SkipResults) {
        Write-Host "     Results:   $resultCount" -ForegroundColor Gray
    }
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
}

# ── Restore ─────────────────────────────────────────────────────────────────

function Invoke-Restore {
    if (-not $BackupDir -or -not (Test-Path $BackupDir)) {
        Write-Err "BackupDir '$BackupDir' does not exist."
        Write-Err "Usage: -Restore -BackupDir <path_to_backup_folder>"
        return
    }

    $manifestPath = Join-Path $BackupDir "manifest.json"
    if (-not (Test-Path $manifestPath)) {
        Write-Err "No manifest.json found in $BackupDir — is this a valid backup?"
        return
    }
    $manifest = Get-Content $manifestPath -Raw | ConvertFrom-Json

    # Verify session
    $me = Invoke-Api "/api/auth/me"
    if (-not $me -or -not $me.authenticated) {
        Write-Err "Not authenticated. Aborting."
        return
    }

    Write-Host ""
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Magenta
    Write-Host "  Restoring backup to: $BaseUrl" -ForegroundColor Magenta
    Write-Host "  Target user: $($me.email) ($($me.user_id))" -ForegroundColor Magenta
    Write-Host "  Source: $BackupDir" -ForegroundColor Magenta
    Write-Host "  Original: $($manifest.email) @ $($manifest.timestamp)" -ForegroundColor Gray
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Magenta

    if ($DryRun) {
        Write-Warn "DRY RUN — no changes will be made."
    }

    # ── 1. Restore prompts ──────────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Restoring Prompts ────────────────────────────" -ForegroundColor Yellow

    $promptsPath = Join-Path $BackupDir "prompts"
    $promptCount = 0
    if (Test-Path $promptsPath) {
        foreach ($modelDir in Get-ChildItem $promptsPath -Directory) {
            $model = $modelDir.Name
            foreach ($file in Get-ChildItem $modelDir.FullName -Filter "*.md") {
                $ptype = $file.BaseName
                $content = Get-Content $file.FullName -Raw -Encoding UTF8
                Write-Step "$model / $ptype"

                if (-not $DryRun) {
                    $body = @{ content = $content }
                    $resp = Invoke-Api "/api/prompts/$model/$ptype" -Method PUT -Body $body
                    if ($resp -and $resp.status -eq "saved") {
                        $promptCount++
                    } else {
                        Write-Warn "  Failed to save prompt"
                    }
                } else {
                    $promptCount++
                }
            }
        }
    }
    Write-Ok "$promptCount prompts restored"

    # ── 2. Restore synthetic data ───────────────────────────────────
    Write-Host ""
    Write-Host "  ── Restoring Synthetic Data ─────────────────────" -ForegroundColor Yellow

    $dataTypes = @("classification", "dialog", "general", "rag", "tool_calling")
    $dataCount = 0
    foreach ($dtype in $dataTypes) {
        $dataFile = Join-Path $BackupDir "synthetic" $dtype "data.json"
        if (Test-Path $dataFile) {
            $items = Get-Content $dataFile -Raw -Encoding UTF8 | ConvertFrom-Json
            $count = ($items | Measure-Object).Count
            Write-Step "$dtype ($count scenarios)"

            if (-not $DryRun) {
                $body = @{ data = $items }
                $resp = Invoke-Api "/api/data/raw/$dtype" -Method PUT -Body $body
                if ($resp -and $resp.status -eq "saved") {
                    $dataCount += $count
                } else {
                    Write-Warn "  Failed to save $dtype data"
                }
            } else {
                $dataCount += $count
            }
        }
    }
    Write-Ok "$dataCount scenarios restored"

    # ── 3. Restore topic data ───────────────────────────────────────
    Write-Host ""
    Write-Host "  ── Restoring Topic Data ─────────────────────────" -ForegroundColor Yellow

    $topicsPath = Join-Path $BackupDir "topics"
    $topicDataCount = 0
    if (Test-Path $topicsPath) {
        foreach ($topicDir in Get-ChildItem $topicsPath -Directory) {
            $slug = $topicDir.Name
            Write-Step "Topic: $slug"

            $topicDataPath = Join-Path $topicDir.FullName "data"
            if (Test-Path $topicDataPath) {
                foreach ($dtype in $dataTypes) {
                    $dataFile = Join-Path $topicDataPath $dtype "data.json"
                    if (Test-Path $dataFile) {
                        $items = Get-Content $dataFile -Raw -Encoding UTF8 | ConvertFrom-Json
                        $count = ($items | Measure-Object).Count

                        if (-not $DryRun) {
                            $body = @{ data = $items; topic = $slug }
                            $resp = Invoke-Api "/api/data/raw/$dtype" -Method PUT -Body $body
                            if ($resp -and $resp.status -eq "saved") {
                                $topicDataCount += $count
                            }
                        } else {
                            $topicDataCount += $count
                        }
                    }
                }
            }
        }
    }
    Write-Ok "$topicDataCount topic scenarios restored"

    # ── Summary ─────────────────────────────────────────────────────
    Write-Host ""
    $verb = if ($DryRun) { "would be restored" } else { "restored" }
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
    Write-Host "  ✅ Restore complete! ($verb)" -ForegroundColor Green
    Write-Host "     Prompts:       $promptCount" -ForegroundColor Gray
    Write-Host "     Scenarios:     $dataCount" -ForegroundColor Gray
    Write-Host "     Topic data:    $topicDataCount" -ForegroundColor Gray
    if (-not $SkipResults) {
        Write-Host "     Results:       (not restored — results are read-only via API)" -ForegroundColor DarkGray
    }
    Write-Host "═══════════════════════════════════════════════════════" -ForegroundColor Green
}

# ── Main ────────────────────────────────────────────────────────────────────

Write-Host ""
Write-Host "╔═══════════════════════════════════════════════════════╗" -ForegroundColor Cyan
Write-Host "║  Model Migration Eval — User Data Backup / Restore    ║" -ForegroundColor Cyan
Write-Host "╚═══════════════════════════════════════════════════════╝" -ForegroundColor Cyan

# Verify connectivity (retry for cold-start — Container Apps scale-to-zero)
$maxRetries = 3
$health = $null
for ($attempt = 1; $attempt -le $maxRetries; $attempt++) {
    try {
        $health = Invoke-RestMethod -Uri "$BaseUrl/api/health" -UseBasicParsing -TimeoutSec 30
        Write-Ok "Connected to $BaseUrl (status: $($health.status))"
        break
    }
    catch {
        if ($attempt -lt $maxRetries) {
            Write-Warn "Attempt $attempt/$maxRetries — waiting for app to start (cold start?) ..."
            Start-Sleep -Seconds 10
        } else {
            Write-Err "Cannot reach $BaseUrl/api/health after $maxRetries attempts"
            Write-Err "Is the app running? Check the URL and try again."
            Write-Err "Detail: $($_.Exception.Message)"
            exit 1
        }
    }
}

# Authenticate
$ok = Connect-App
if (-not $ok) {
    Write-Err "Authentication failed. Aborting."
    exit 1
}

# Execute
if ($Restore) {
    Invoke-Restore
} else {
    Invoke-Backup
}
