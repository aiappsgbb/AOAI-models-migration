# =============================================================================
# Deployment Script - Azure OpenAI Model Migration Evaluation Framework
# Local Docker Desktop  or  Azure Container Apps
# =============================================================================

# ---------------------------------------------------------------------------
# Variables -- UPDATE THESE FOR AZURE DEPLOYMENT
# ---------------------------------------------------------------------------
$RESOURCE_GROUP      = "rg-model-migration"
$LOCATION            = "swedencentral"
$ENVIRONMENT_NAME    = "model-migration-env"
$CONTAINER_APP_NAME  = "model-migration-eval"
$ACR_NAME            = "acrmodelmigration"          # Must be globally unique, lowercase, no dashes
$IMAGE_NAME          = "model-migration-eval"
$IMAGE_TAG           = "v$(Get-Date -Format 'yyyyMMdd-HHmmss')"
$LOCAL_PORT          = 5000
$CONTAINER_PORT      = 5000
$ENV_FILE            = ".env"
$DOCKERFILE          = "Dockerfile"
$STORAGE_ACCOUNT_NAME = "stmodelmigration"     # Must be globally unique, lowercase, no dashes, 3-24 chars

# Environment variables that contain secrets (will be stored as Container Apps secrets)
$SECRET_VARS = @("AZURE_CLIENT_SECRET", "SMTP_PASSWORD", "FLASK_SECRET_KEY", "GEMINI_API_KEY")

# Service Principal name for Foundry auth inside containers
$SP_NAME = "sp-model-migration-eval"

# =============================================================================
# Ask user for deployment target
# =============================================================================
Write-Host ""
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host "  Azure OpenAI Model Migration - Deployment" -ForegroundColor Cyan
Write-Host "========================================================" -ForegroundColor Cyan
Write-Host ""
Write-Host "Where do you want to deploy?" -ForegroundColor Yellow
Write-Host "  1. Local Docker Desktop  (for development / testing)"
Write-Host "  2. Azure Container Apps  (for production / demos)"
Write-Host ""
$choice = Read-Host "Enter your choice (1 or 2)"

# =============================================================================
# Helper: check last exit code
# =============================================================================
function Test-StepSuccess {
    param([string]$StepName)
    if ($LASTEXITCODE -ne 0) {
        Write-Host "ERROR: $StepName failed (exit code $LASTEXITCODE)" -ForegroundColor Red
        Write-Host "Fix the issue and re-run the script." -ForegroundColor Yellow
        exit 1
    }
    Write-Host "  OK - $StepName" -ForegroundColor Green
}

# =============================================================================
# Helper: parse .env file into a hashtable
# =============================================================================
function Read-EnvFile {
    param([string]$Path)
    $vars = @{}
    Get-Content $Path | ForEach-Object {
        if ($_ -match "^\s*#" -or $_ -match "^\s*$") { return }
        if ($_ -match "^([^=]+)=(.*)$") {
            $key   = $matches[1].Trim()
            $value = $matches[2].Trim() -replace '^["'']|["'']$', ''
            if ($value -and $value -ne "" -and -not $value.StartsWith("<")) {
                $vars[$key] = $value
            }
        }
    }
    return $vars
}

# =============================================================================
# Helper: Ensure RBAC roles are assigned for the Service Principal
# Called both when creating a new SP and when reusing existing creds.
# =============================================================================
function Ensure-ServicePrincipalRBAC {
    param([string]$SpAppId)

    Write-Host ""
    Write-Host "--- Verifying RBAC role assignments for SP $SpAppId ---" -ForegroundColor Cyan

    # Ensure Azure CLI is available and logged in
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        Write-Host "  WARNING: Azure CLI (az) not found -- cannot verify RBAC roles." -ForegroundColor Yellow
        return
    }
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) {
        Write-Host "  Not logged in to Azure CLI. Launching login..." -ForegroundColor Yellow
        az login
        $acct = az account show 2>$null | ConvertFrom-Json
        if (-not $acct) {
            Write-Host "  WARNING: Azure login failed -- cannot verify RBAC roles." -ForegroundColor Yellow
            return
        }
    }

    $envVarsLocal = Read-EnvFile $ENV_FILE

    # --- Assign roles on AI / Foundry resource ---
    $foundryEp = $envVarsLocal["FOUNDRY_PROJECT_ENDPOINT"]
    if ($foundryEp -and $foundryEp -match 'https://([^.]+)\.') {
        $aiResName = $matches[1]
        Write-Host "  Looking up AI resource '$aiResName'..." -ForegroundColor Gray
        $resId = az resource list --name $aiResName --query "[0].id" -o tsv 2>$null

        if ($resId) {
            $roles = @(
                "Cognitive Services OpenAI Contributor",
                "Cognitive Services OpenAI User",
                "Azure AI Developer",
                "Azure AI User",
                "User Access Administrator"
            )
            foreach ($role in $roles) {
                Write-Host "  Assigning '$role'..." -ForegroundColor Cyan
                az role assignment create `
                    --assignee $SpAppId `
                    --role $role `
                    --scope $resId `
                    --output none 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "    OK" -ForegroundColor Green
                } else {
                    Write-Host "    May already exist or insufficient permissions" -ForegroundColor Yellow
                }
            }

            $rgScope = ($resId -split '/providers/')[0]
            if ($rgScope) {
                Write-Host "  Assigning 'Storage Blob Data Contributor' on resource group..." -ForegroundColor Cyan
                az role assignment create `
                    --assignee $SpAppId `
                    --role "Storage Blob Data Contributor" `
                    --scope $rgScope `
                    --output none 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "    OK" -ForegroundColor Green
                } else {
                    Write-Host "    May already exist or insufficient permissions" -ForegroundColor Yellow
                }
            }
        } else {
            Write-Host "  WARNING: Could not find resource '$aiResName'." -ForegroundColor Yellow
            Write-Host "  Assign roles to $SpAppId manually." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  FOUNDRY_PROJECT_ENDPOINT not in .env -- skipping Foundry role assignment." -ForegroundColor Yellow
    }

    # --- Assign roles on dedicated Realtime/TTS endpoint (if configured) ---
    $realtimeEp = $envVarsLocal["AZURE_OPENAI_REALTIME_ENDPOINT"]
    if ($realtimeEp -and $realtimeEp -match 'https://([^.]+)\.') {
        $rtResName = $matches[1]
        Write-Host ""
        Write-Host "  Looking up Realtime/TTS AI resource '$rtResName'..." -ForegroundColor Gray
        $rtResId = az resource list --name $rtResName --query "[0].id" -o tsv 2>$null

        if ($rtResId) {
            # TTS (audio/speech) requires "Cognitive Services OpenAI Contributor"
            # which includes the deployments/audio/action data action.
            $rtRoles = @(
                "Cognitive Services OpenAI Contributor",
                "Cognitive Services OpenAI User"
            )
            foreach ($role in $rtRoles) {
                Write-Host "  Assigning '$role' on realtime resource..." -ForegroundColor Cyan
                az role assignment create `
                    --assignee $SpAppId `
                    --role $role `
                    --scope $rtResId `
                    --output none 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "    OK" -ForegroundColor Green
                } else {
                    Write-Host "    May already exist or insufficient permissions" -ForegroundColor Yellow
                }
            }
        } else {
            Write-Host "  WARNING: Could not find realtime resource '$rtResName'." -ForegroundColor Yellow
            Write-Host "  Assign 'Cognitive Services OpenAI Contributor' to $SpAppId manually." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  AZURE_OPENAI_REALTIME_ENDPOINT not in .env -- using main endpoint for TTS/Realtime." -ForegroundColor Gray
    }

}

# =============================================================================
# Helper: Ensure Foundry Service Principal exists and .env has credentials
# =============================================================================
function Setup-FoundryServicePrincipal {
    # DefaultAzureCredential inside containers needs AZURE_TENANT_ID,
    # AZURE_CLIENT_ID and AZURE_CLIENT_SECRET.  This block creates a
    # Service Principal, assigns the right roles on the AI resource,
    # and writes the vars to .env.
    Write-Host ""
    Write-Host "--- Foundry Auth: Service Principal Setup ---" -ForegroundColor Cyan
    $script:spCreated = $false

    # Check if SP vars are already populated in .env
    $envContent = Get-Content $ENV_FILE -Raw -Encoding UTF8
    $hasTenant = $envContent -match '(?m)^AZURE_TENANT_ID=.+'
    $hasClient = $envContent -match '(?m)^AZURE_CLIENT_ID=.+'
    $hasSecret = $envContent -match '(?m)^AZURE_CLIENT_SECRET=.+'

    if ($hasTenant -and $hasClient -and $hasSecret) {
        Write-Host "  Service Principal credentials found in .env" -ForegroundColor Green
        $existingEnv = Read-EnvFile $ENV_FILE
        $existingAppId  = $existingEnv["AZURE_CLIENT_ID"]
        $existingTenant = $existingEnv["AZURE_TENANT_ID"]
        $existingSecret = $existingEnv["AZURE_CLIENT_SECRET"]

        # --- Validate the existing secret is still usable ---
        Write-Host "  Validating credentials..." -ForegroundColor Cyan
        $tokenOk = $false
        try {
            $body = "grant_type=client_credentials&client_id=$existingAppId&client_secret=$([uri]::EscapeDataString($existingSecret))&scope=https%3A%2F%2Fcognitiveservices.azure.com%2F.default"
            $tokenResp = Invoke-RestMethod -Uri "https://login.microsoftonline.com/$existingTenant/oauth2/v2.0/token" `
                -Method POST -ContentType "application/x-www-form-urlencoded" -Body $body -ErrorAction Stop
            if ($tokenResp.access_token) { $tokenOk = $true }
        } catch { }

        if ($tokenOk) {
            Write-Host "  Credentials are valid" -ForegroundColor Green
            Ensure-ServicePrincipalRBAC -SpAppId $existingAppId
            return
        }

        # --- Secret expired / invalid — rotate it ---
        Write-Host "  Credential EXPIRED or INVALID — rotating secret..." -ForegroundColor Yellow

        # Ensure Azure CLI is available for rotation
        if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
            Write-Host "  ERROR: Azure CLI (az) not found — cannot rotate secret." -ForegroundColor Red
            Write-Host "  Install from https://aka.ms/installazurecliwindows or update AZURE_CLIENT_SECRET in .env manually." -ForegroundColor Yellow
            return
        }
        $acctCheck = az account show 2>$null | ConvertFrom-Json
        if (-not $acctCheck) {
            Write-Host "  Not logged in to Azure CLI. Launching login..." -ForegroundColor Yellow
            az login
            $acctCheck = az account show 2>$null | ConvertFrom-Json
            if (-not $acctCheck) {
                Write-Host "  ERROR: Azure login failed — cannot rotate secret." -ForegroundColor Red
                return
            }
        }

        # Try to reset the credential with decreasing durations
        $rotateOk = $false
        $newPassword = $null
        foreach ($days in @(90, 30, 7)) {
            $endDate = (Get-Date).ToUniversalTime().AddDays($days).ToString("yyyy-MM-ddTHH:mm:ssZ")
            Write-Host "  Resetting credential ($days days, expires $endDate)..." -ForegroundColor Cyan
            $credJson = az ad app credential reset --id $existingAppId --end-date $endDate 2>&1
            $jsonOnly = ($credJson | Where-Object { $_ -notmatch '^(WARNING|ERROR|System\.)' }) -join ""
            $cred = $null
            try { $cred = $jsonOnly | ConvertFrom-Json } catch { }
            if ($cred -and $cred.password) {
                $newPassword = $cred.password
                $rotateOk = $true
                break
            }
            Write-Host "  $days-day rejected by policy, trying shorter..." -ForegroundColor Yellow
        }

        if (-not $rotateOk) {
            Write-Host "  ERROR: Could not rotate secret. Update AZURE_CLIENT_SECRET in .env manually." -ForegroundColor Red
            return
        }

        # --- Update .env with the new secret ---
        $lines = Get-Content $ENV_FILE -Encoding UTF8 | Where-Object {
            $_ -notmatch '^AZURE_CLIENT_SECRET='
        }
        $updatedLines = @()
        foreach ($l in $lines) {
            $updatedLines += $l
            # Insert new secret right after AZURE_CLIENT_ID line
            if ($l -match '^AZURE_CLIENT_ID=') {
                $updatedLines += "AZURE_CLIENT_SECRET=$newPassword"
            }
        }
        $updatedLines | Set-Content $ENV_FILE -Encoding UTF8
        Write-Host "  New secret written to .env (valid $days days)" -ForegroundColor Green

        Ensure-ServicePrincipalRBAC -SpAppId $existingAppId
        return
    }

    Write-Host "  SP credentials missing in .env - creating Service Principal..." -ForegroundColor Yellow

    # --- Check Azure CLI ---
    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        Write-Host "  ERROR: Azure CLI (az) not found." -ForegroundColor Red
        Write-Host "  Install from https://aka.ms/installazurecliwindows" -ForegroundColor Yellow
        Write-Host ""
        $skipFoundry = Read-Host "  Continue WITHOUT Foundry evaluation? (y/n)"
        if ($skipFoundry -ne "y") { exit 1 }
        return
    }

    # --- Ensure logged in ---
    $acct = az account show 2>$null | ConvertFrom-Json
    if (-not $acct) {
        Write-Host "  Not logged in to Azure CLI. Launching login..." -ForegroundColor Yellow
        az login
        $acct = az account show 2>$null | ConvertFrom-Json
    }

    if (-not $acct) {
        Write-Host "  ERROR: Azure login failed." -ForegroundColor Red
        $skipFoundry = Read-Host "  Continue WITHOUT Foundry evaluation? (y/n)"
        if ($skipFoundry -ne "y") { exit 1 }
        return
    }

    Write-Host "  Subscription : $($acct.name) ($($acct.id))" -ForegroundColor Gray
    $sp = $null

    # --- Clean up any orphan app registration with our SP name ---
    $orphanAppId = az ad app list --display-name $SP_NAME --query "[0].appId" -o tsv 2>$null
    if ($orphanAppId) {
        Write-Host "  Found existing app registration '$SP_NAME' -- deleting to recreate cleanly..." -ForegroundColor Yellow
        az ad app delete --id $orphanAppId 2>$null
        Start-Sleep -Seconds 3
    }

    # --- Create Service Principal in 3 steps ---
    # Step 1: Create app registration
    Write-Host "  Step 1/3: Creating app registration '$SP_NAME'..." -ForegroundColor Cyan
    $appJson = az ad app create --display-name $SP_NAME 2>&1
    $appJsonClean = ($appJson | Where-Object { $_ -notmatch '^(WARNING|ERROR|System\.)' }) -join ""
    $app = $null
    try { $app = $appJsonClean | ConvertFrom-Json } catch { }

    if (-not $app -or -not $app.appId) {
        Write-Host "  ERROR: Failed to create app registration." -ForegroundColor Red
        Write-Host "  Output: $appJson" -ForegroundColor Red
        $skipFoundry = Read-Host "  Continue WITHOUT Foundry evaluation? (y/n)"
        if ($skipFoundry -ne "y") { exit 1 }
        return
    }

    $spAppId = $app.appId
    Write-Host "  App created: appId = $spAppId" -ForegroundColor Green

    # Step 2: Create service principal for the app
    Write-Host "  Step 2/3: Creating service principal..." -ForegroundColor Cyan
    az ad sp create --id $spAppId --output none 2>$null
    Start-Sleep -Seconds 2

    # Step 3: Add a short-lived password credential with --end-date
    $credOk = $false
    foreach ($days in @(90, 30, 7)) {
        $endDate = (Get-Date).ToUniversalTime().AddDays($days).ToString("yyyy-MM-ddTHH:mm:ssZ")
        Write-Host "  Step 3/3: Adding $days-day credential (expires $endDate)..." -ForegroundColor Cyan
        $credJson = az ad app credential reset --id $spAppId --end-date $endDate 2>&1
        # Filter out WARNING/ERROR lines -- keep only JSON
        $jsonOnly = ($credJson | Where-Object { $_ -notmatch '^(WARNING|ERROR|System\.)' }) -join ""
        $cred = $null
        try { $cred = $jsonOnly | ConvertFrom-Json } catch { }
        if ($cred -and $cred.password) {
            $credOk = $true
            break
        }
        Write-Host "  $days-day rejected by policy, trying shorter..." -ForegroundColor Yellow
    }

    if (-not $credOk) {
        Write-Host "  ERROR: All credential durations rejected by policy." -ForegroundColor Red
        Write-Host "  Last output: $credJson" -ForegroundColor Red
        az ad app delete --id $spAppId 2>$null
        $skipFoundry = Read-Host "  Continue WITHOUT Foundry evaluation? (y/n)"
        if ($skipFoundry -ne "y") { exit 1 }
        return
    }

    # Build SP object
    $sp = [PSCustomObject]@{
        appId    = $spAppId
        password = $cred.password
        tenant   = $acct.tenantId
    }
    Write-Host "  SP appId   : $($sp.appId)" -ForegroundColor Green
    Write-Host "  SP tenant  : $($sp.tenant)" -ForegroundColor Green

    # --- Grant Microsoft Graph User.Read.All (for RBAC email → objectId lookup) ---
    Write-Host "  Granting Graph API 'User.Read.All' permission..." -ForegroundColor Cyan
    $graphAppId = "00000003-0000-0000-c000-000000000000"
    $userReadAllId = "df021288-bdef-4463-88db-98f22de89214"
    az ad app permission add --id $spAppId --api $graphAppId `
        --api-permissions "${userReadAllId}=Role" --output none 2>$null
    az ad app permission admin-consent --id $spAppId --output none 2>$null
    if ($LASTEXITCODE -eq 0) {
        Write-Host "    Graph User.Read.All — OK (admin-consented)" -ForegroundColor Green
    } else {
        Write-Host "    Graph permission added but admin-consent may require a Global Admin" -ForegroundColor Yellow
        Write-Host "    (RBAC auto-assign will work once consented)" -ForegroundColor Yellow
    }

    # --- Assign RBAC roles (Foundry + Azure OpenAI) ---
    Ensure-ServicePrincipalRBAC -SpAppId $sp.appId

    # --- Write SP credentials to .env ---
    $lines = Get-Content $ENV_FILE -Encoding UTF8 | Where-Object {
        $_ -notmatch '^AZURE_TENANT_ID=' -and
        $_ -notmatch '^AZURE_CLIENT_ID=' -and
        $_ -notmatch '^AZURE_CLIENT_SECRET=' -and
        $_ -notmatch '^# Service Principal for Foundry'
    }
    while ($lines.Count -gt 0 -and $lines[-1].Trim() -eq "") {
        $lines = $lines[0..($lines.Count - 2)]
    }
    $lines += ""
    $lines += "# Service Principal for Foundry auth (auto-generated by deploy.ps1)"
    $lines += "AZURE_TENANT_ID=$($sp.tenant)"
    $lines += "AZURE_CLIENT_ID=$($sp.appId)"
    $lines += "AZURE_CLIENT_SECRET=$($sp.password)"

    # Write FOUNDRY_RESOURCE_ID if we discovered the AI resource
    if ($resId) {
        $lines = $lines | Where-Object { $_ -notmatch '^FOUNDRY_RESOURCE_ID=' }
        $lines += "FOUNDRY_RESOURCE_ID=$resId"
    }

    $lines | Set-Content $ENV_FILE -Encoding UTF8

    Write-Host "  Credentials written to .env" -ForegroundColor Green
    $script:spCreated = $true
}

# =============================================================================
# Helper: validate .env has all required vars for container deployment
# =============================================================================
function Show-EnvValidation {
    Write-Host ""
    Write-Host "--- .env validation ---" -ForegroundColor Cyan
    $finalEnv = Read-EnvFile $ENV_FILE
    $spOk = $true
    foreach ($key in @("AZURE_OPENAI_ENDPOINT", "FOUNDRY_PROJECT_ENDPOINT", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")) {
        $val = $finalEnv[$key]
        if ($val) {
            $masked = $val.Substring(0, [Math]::Min(8, $val.Length)) + "..."
            Write-Host "  $key = $masked" -ForegroundColor Green
        } else {
            Write-Host "  $key = (empty)" -ForegroundColor Red
            if ($key -like "AZURE_*_ID" -or $key -like "AZURE_CLIENT_SECRET") { $spOk = $false }
        }
    }
    # Gemini (optional)
    Write-Host "" 
    Write-Host "--- Google Gemini (optional) ---" -ForegroundColor Cyan
    $geminiVal = $finalEnv["GEMINI_API_KEY"]
    if ($geminiVal) {
        $masked = $geminiVal.Substring(0, [Math]::Min(8, $geminiVal.Length)) + "..."
        Write-Host "  GEMINI_API_KEY = $masked" -ForegroundColor Green
    } else {
        Write-Host "  GEMINI_API_KEY = (not set - Gemini models disabled)" -ForegroundColor Gray
    }

    # Realtime / TTS (optional)
    Write-Host ""
    Write-Host "--- Realtime / TTS (optional) ---" -ForegroundColor Cyan
    $rtVal = $finalEnv["AZURE_OPENAI_REALTIME_ENDPOINT"]
    if ($rtVal) {
        $masked = $rtVal.Substring(0, [Math]::Min(40, $rtVal.Length)) + "..."
        Write-Host "  AZURE_OPENAI_REALTIME_ENDPOINT = $masked" -ForegroundColor Green
        Write-Host "  (Speech-to-speech evaluation enabled via dedicated realtime endpoint)" -ForegroundColor Gray
    } else {
        Write-Host "  AZURE_OPENAI_REALTIME_ENDPOINT = (not set - using main endpoint for Realtime/TTS)" -ForegroundColor Gray
    }

    # RBAC auto-assign (optional)
    Write-Host ""
    Write-Host "--- RBAC Auto-assign (optional) ---" -ForegroundColor Cyan
    $rbacVal = $finalEnv["AUTO_ASSIGN_FOUNDRY_READER"]
    if ($rbacVal -and $rbacVal -match '^(true|1|yes)$') {
        Write-Host "  AUTO_ASSIGN_FOUNDRY_READER = $rbacVal" -ForegroundColor Green
        $resIdVal = $finalEnv["FOUNDRY_RESOURCE_ID"]
        if ($resIdVal) {
            $masked = $resIdVal.Substring(0, [Math]::Min(40, $resIdVal.Length)) + "..."
            Write-Host "  FOUNDRY_RESOURCE_ID = $masked" -ForegroundColor Green
        } else {
            Write-Host "  FOUNDRY_RESOURCE_ID = (missing - RBAC will not work!)" -ForegroundColor Red
        }
        Write-Host "  (Reader + Azure AI User roles will be auto-assigned on login)" -ForegroundColor Gray
    } else {
        Write-Host "  AUTO_ASSIGN_FOUNDRY_READER = (disabled)" -ForegroundColor Gray
    }

    # Authentication / SMTP settings (optional)
    Write-Host "" 
    Write-Host "--- Authentication (optional) ---" -ForegroundColor Cyan
    foreach ($key in @("SMTP_HOST", "SMTP_USERNAME", "SMTP_PASSWORD", "SMTP_FROM_EMAIL", "AUTH_CODE_VERIFICATION", "AUTH_EMAIL_PROVIDER", "FLASK_SECRET_KEY")) {
        $val = $finalEnv[$key]
        if ($val) {
            $masked = $val.Substring(0, [Math]::Min(8, $val.Length)) + "..."
            Write-Host "  $key = $masked" -ForegroundColor Green
        } else {
            Write-Host "  $key = (not set - console auth mode)" -ForegroundColor Gray
        }
    }
    if (-not $spOk) {
        Write-Host ""
        Write-Host "  WARNING: SP credentials are missing. Foundry evaluation will fail inside the container." -ForegroundColor Yellow
        $cont = Read-Host "  Continue anyway? (y/n)"
        if ($cont -ne "y") { exit 1 }
    }
}

# =============================================================================
# OPTION 1: Local Docker Desktop
# =============================================================================
if ($choice -eq "1") {
    Write-Host ""
    Write-Host "=== Deploying to Local Docker Desktop ===" -ForegroundColor Green
    Write-Host ""

    # --- Pre-flight checks ---
    if (-not (Test-Path $ENV_FILE)) {
        Write-Host "ERROR: $ENV_FILE not found!" -ForegroundColor Red
        Write-Host "Copy .env.example to .env and fill in your credentials." -ForegroundColor Yellow
        exit 1
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: docker CLI not found. Is Docker Desktop installed and running?" -ForegroundColor Red
        exit 1
    }

    # --- Foundry Auth + .env validation ---
    Setup-FoundryServicePrincipal
    Show-EnvValidation

    # --- Wait for Azure role propagation (if SP was just created/assigned) ---
    if ($script:spCreated) {
        Write-Host "Waiting 30s for Azure RBAC role propagation..." -ForegroundColor Yellow
        Start-Sleep -Seconds 30
        Write-Host "  Done." -ForegroundColor Green
    }
    Write-Host ""

    # --- Stop any previous container ---
    Write-Host "Stopping existing container (if any)..." -ForegroundColor Cyan
    docker stop $IMAGE_NAME 2>$null | Out-Null
    docker rm   $IMAGE_NAME 2>$null | Out-Null

    # --- Build ---
    Write-Host "Building Docker image..." -ForegroundColor Cyan
    docker build --no-cache -t "${IMAGE_NAME}:${IMAGE_TAG}" -f $DOCKERFILE .
    Test-StepSuccess "Docker build"

    # --- Run ---
    Write-Host "Starting container..." -ForegroundColor Cyan
    docker run -d `
        --name $IMAGE_NAME `
        -p "${LOCAL_PORT}:${CONTAINER_PORT}" `
        --env-file $ENV_FILE `
        "${IMAGE_NAME}:${IMAGE_TAG}"
    Test-StepSuccess "Docker run"

    # --- Wait & health check ---
    Write-Host "Waiting for server to start..." -ForegroundColor Cyan
    Start-Sleep -Seconds 5

    $containerStatus = docker ps --filter "name=$IMAGE_NAME" --format "{{.Status}}"
    if ($containerStatus) {
        Write-Host ""
        Write-Host "========================================================" -ForegroundColor Green
        Write-Host "  Container started successfully!" -ForegroundColor Green
        Write-Host "========================================================" -ForegroundColor Green
        Write-Host ""
        Write-Host "Web UI:" -ForegroundColor Yellow
        Write-Host "  http://localhost:$LOCAL_PORT" -ForegroundColor White
        Write-Host ""
        Write-Host "Health Check:" -ForegroundColor Yellow
        Write-Host "  http://localhost:$LOCAL_PORT/api/health" -ForegroundColor White
        Write-Host ""
        Write-Host "--- Useful Commands ---" -ForegroundColor Magenta
        Write-Host "  View logs:    docker logs -f $IMAGE_NAME"
        Write-Host "  Stop:         docker stop $IMAGE_NAME"
        Write-Host "  Restart:      docker restart $IMAGE_NAME"
        Write-Host "  Remove:       docker rm -f $IMAGE_NAME"
        Write-Host "  Shell:        docker exec -it $IMAGE_NAME bash"
        Write-Host ""
        Write-Host "--- Foundry Evaluation Auth ---" -ForegroundColor Magenta
        Write-Host "  Service Principal credentials are passed via .env automatically." -ForegroundColor Gray
        Write-Host "  To re-create SP: delete AZURE_TENANT_ID/CLIENT_ID/CLIENT_SECRET from .env and re-run." -ForegroundColor Gray
        Write-Host ""

        # Quick health ping
        try {
            $resp = Invoke-WebRequest -Uri "http://localhost:$LOCAL_PORT/api/health" `
                        -UseBasicParsing -TimeoutSec 5
            if ($resp.StatusCode -eq 200) {
                Write-Host "Health check: OK" -ForegroundColor Green
            }
        } catch {
            Write-Host "Health check: server still starting - run 'docker logs $IMAGE_NAME'" -ForegroundColor Yellow
        }
    } else {
        Write-Host "Container failed to start. Check: docker logs $IMAGE_NAME" -ForegroundColor Red
        exit 1
    }
}

# =============================================================================
# OPTION 2: Azure Container Apps
# =============================================================================
elseif ($choice -eq "2") {
    Write-Host ""
    Write-Host "=== Deploying to Azure Container Apps ===" -ForegroundColor Green
    Write-Host ""
    Write-Host "  Image tag : $IMAGE_TAG" -ForegroundColor Gray
    Write-Host "  Resource  : $RESOURCE_GROUP / $CONTAINER_APP_NAME" -ForegroundColor Gray
    Write-Host ""

    # --- Pre-flight checks ---
    if (-not (Test-Path $ENV_FILE)) {
        Write-Host "ERROR: $ENV_FILE not found!" -ForegroundColor Red
        Write-Host "Copy .env.example to .env and fill in your credentials." -ForegroundColor Yellow
        exit 1
    }

    if (-not (Get-Command az -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: Azure CLI (az) not found. Install from https://aka.ms/installazurecliwindows" -ForegroundColor Red
        exit 1
    }

    if (-not (Get-Command docker -ErrorAction SilentlyContinue)) {
        Write-Host "ERROR: docker CLI not found. Is Docker Desktop installed and running?" -ForegroundColor Red
        exit 1
    }

    # --- Foundry Auth + .env validation ---
    Setup-FoundryServicePrincipal
    Show-EnvValidation

    # --- Wait for Azure role propagation (if SP was just created/assigned) ---
    if ($script:spCreated) {
        Write-Host "Waiting 30s for Azure RBAC role propagation..." -ForegroundColor Yellow
        Start-Sleep -Seconds 30
        Write-Host "  Done." -ForegroundColor Green
    }
    Write-Host ""

    # --- Skip-step selector ---
    Write-Host "Skip completed steps?" -ForegroundColor Yellow
    Write-Host "  0. Run all steps (default)"
    Write-Host "  1. Skip to Step 2 (ACR)           - Resource Group already exists"
    Write-Host "  2. Skip to Step 3 (Docker)         - ACR already exists"
    Write-Host "  3. Skip to Step 4 (Environment)    - Image already pushed"
    Write-Host "  4. Skip to Step 5 (Storage)        - Environment already exists"
    Write-Host "  5. Skip to Step 6 (Env vars)       - Storage already exists"
    Write-Host "  6. Skip to Step 7 (Container App)  - Ready to create / update app"
    Write-Host "  7. Skip to Step 8 (Get URL)        - App already deployed"
    Write-Host ""
    $startStep = Read-Host "Start from step [0-7, default 0]"
    if (-not $startStep) { $startStep = "0" }
    $startStep = [int]$startStep

    # --- Read .env ---
    Write-Host "Reading environment variables from $ENV_FILE..." -ForegroundColor Cyan
    $envVars = Read-EnvFile $ENV_FILE
    Write-Host "  Found $($envVars.Count) variables" -ForegroundColor Gray

    # == Step 1: Resource Group ================================================
    if ($startStep -le 0) {
        Write-Host ""
        Write-Host "[Step 1/8] Checking Resource Group..." -ForegroundColor Cyan

        $rgExists = az group exists --name $RESOURCE_GROUP 2>$null
        if ($rgExists -eq "true") {
            $rgState = az group show --name $RESOURCE_GROUP --query "properties.provisioningState" -o tsv 2>$null
            if ($rgState -eq "Deleting") {
                Write-Host "  Resource Group is being deleted - waiting..." -ForegroundColor Yellow
                $waited = 0
                while ($waited -lt 300) {
                    Start-Sleep -Seconds 10; $waited += 10
                    if ((az group exists --name $RESOURCE_GROUP 2>$null) -ne "true") {
                        Write-Host "  Deleted." -ForegroundColor Green; break
                    }
                    Write-Host "    still deleting... (${waited}s)" -ForegroundColor Gray
                }
                if ($waited -ge 300) {
                    Write-Host "ERROR: Deletion timed out." -ForegroundColor Red; exit 1
                }
            } else {
                Write-Host "  Resource Group already exists (state: $rgState)" -ForegroundColor Gray
            }
        }

        az group create --name $RESOURCE_GROUP --location $LOCATION --output none
        Test-StepSuccess "Resource Group"
    } else {
        Write-Host "[Step 1/8] Skipped (Resource Group)" -ForegroundColor Gray
    }

    # == Step 2: Azure Container Registry ======================================
    if ($startStep -le 1) {
        Write-Host ""
        Write-Host "[Step 2/8] Azure Container Registry..." -ForegroundColor Cyan

        $acrExists = az acr show --name $ACR_NAME --query "name" -o tsv 2>$null
        if ($acrExists) {
            Write-Host "  ACR '$ACR_NAME' already exists - skipping creation" -ForegroundColor Gray
        } else {
            az acr create `
                --resource-group $RESOURCE_GROUP `
                --name $ACR_NAME `
                --sku Basic `
                --admin-enabled true `
                --output none
            Test-StepSuccess "ACR creation"
        }
    } else {
        Write-Host "[Step 2/8] Skipped (ACR)" -ForegroundColor Gray
    }

    # Get ACR credentials (always needed for later steps)
    Write-Host "  Retrieving ACR credentials..." -ForegroundColor Gray
    $ACR_LOGIN_SERVER = az acr show --name $ACR_NAME --query loginServer -o tsv
    $ACR_USERNAME     = az acr credential show --name $ACR_NAME --query username -o tsv
    $ACR_PASSWORD     = az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv

    if (-not $ACR_LOGIN_SERVER -or -not $ACR_USERNAME -or -not $ACR_PASSWORD) {
        Write-Host "ERROR: Failed to retrieve ACR credentials" -ForegroundColor Red
        exit 1
    }
    Write-Host "  ACR server: $ACR_LOGIN_SERVER" -ForegroundColor Gray

    # == Step 3: Build & Push Docker Image =====================================
    if ($startStep -le 2) {
        Write-Host ""
        Write-Host "[Step 3/8] Building and pushing Docker image..." -ForegroundColor Cyan

        az acr login --name $ACR_NAME
        Test-StepSuccess "ACR login"

        docker build --no-cache -t "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" -f $DOCKERFILE .
        Test-StepSuccess "Docker build"

        docker push "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
        Test-StepSuccess "Docker push"
    } else {
        Write-Host "[Step 3/8] Skipped (Docker build/push)" -ForegroundColor Gray
    }

    # == Step 4: Container Apps Environment ====================================
    if ($startStep -le 3) {
        Write-Host ""
        Write-Host "[Step 4/8] Container Apps Environment..." -ForegroundColor Cyan

        $envExists = az containerapp env show `
            --name $ENVIRONMENT_NAME `
            --resource-group $RESOURCE_GROUP `
            --query "name" -o tsv 2>$null
        if ($envExists) {
            Write-Host "  Environment '$ENVIRONMENT_NAME' already exists - skipping" -ForegroundColor Gray
        } else {
            az containerapp env create `
                --name $ENVIRONMENT_NAME `
                --resource-group $RESOURCE_GROUP `
                --location $LOCATION `
                --output none
            Test-StepSuccess "Container Apps Environment"
        }
    } else {
        Write-Host "[Step 4/8] Skipped (Environment)" -ForegroundColor Gray
    }

    # == Step 5: Storage Account (user data persistence) ========================
    if ($startStep -le 4) {
        Write-Host ""
        Write-Host "[Step 5/8] Storage Account (user data persistence)..." -ForegroundColor Cyan

        $saExists = az storage account show --name $STORAGE_ACCOUNT_NAME --resource-group $RESOURCE_GROUP --query "name" -o tsv 2>$null
        if ($saExists) {
            Write-Host "  Storage Account '$STORAGE_ACCOUNT_NAME' already exists - skipping creation" -ForegroundColor Gray
        } else {
            az storage account create `
                --name $STORAGE_ACCOUNT_NAME `
                --resource-group $RESOURCE_GROUP `
                --location $LOCATION `
                --sku Standard_LRS `
                --kind StorageV2 `
                --https-only true `
                --min-tls-version TLS1_2 `
                --allow-blob-public-access false `
                --allow-shared-key-access false `
                --output none
            Test-StepSuccess "Storage Account creation"
        }

        # Create blob container
        az storage container create `
            --name userdata `
            --account-name $STORAGE_ACCOUNT_NAME `
            --auth-mode login `
            --output none 2>$null
        Write-Host "  Blob container 'userdata' ensured" -ForegroundColor Gray

        # Assign Storage Blob Data Contributor to the Service Principal
        $spAppId = $envVars['AZURE_CLIENT_ID']
        if ($spAppId) {
            $saId = az storage account show --name $STORAGE_ACCOUNT_NAME --resource-group $RESOURCE_GROUP --query "id" -o tsv
            $existingRole = az role assignment list --assignee $spAppId --scope $saId --role "Storage Blob Data Contributor" --query "[0].id" -o tsv 2>$null
            if (-not $existingRole) {
                az role assignment create `
                    --assignee $spAppId `
                    --role "Storage Blob Data Contributor" `
                    --scope $saId `
                    --output none
                Test-StepSuccess "Storage RBAC assignment"
            } else {
                Write-Host "  Storage RBAC already assigned" -ForegroundColor Gray
            }
        }

        # Add storage account name to env vars for the container
        $envVars['AZURE_STORAGE_ACCOUNT_NAME'] = $STORAGE_ACCOUNT_NAME
    } else {
        Write-Host "[Step 5/8] Skipped (Storage Account)" -ForegroundColor Gray
        # Still need the env var even if skipping
        $envVars['AZURE_STORAGE_ACCOUNT_NAME'] = $STORAGE_ACCOUNT_NAME
    }

    # == Step 6: Build YAML configuration ======================================
    Write-Host ""
    Write-Host "[Step 6/8] Preparing environment variables & secrets..." -ForegroundColor Cyan

    function ConvertTo-YamlSafe {
        param([string]$Value)
        if ($Value -match '[":{}[\],&*#?|\-<>=!%@`]' -or $Value -match "'" -or $Value -match '\n') {
            return "'$($Value.Replace("'", "''"))'"
        }
        return "`"$Value`""
    }

    # Secrets section: ACR password + secret env vars
    $secretsYaml = "    - name: acr-password`n      value: $(ConvertTo-YamlSafe $ACR_PASSWORD)"
    foreach ($key in $envVars.Keys) {
        if ($SECRET_VARS -contains $key) {
            $secretName = $key.ToLower().Replace("_", "-")
            $secretsYaml += "`n    - name: $secretName`n      value: $(ConvertTo-YamlSafe $envVars[$key])"
        }
    }

    # Env section: secrets -> secretRef, others -> plain value
    $envVarsYaml = ""
    foreach ($key in $envVars.Keys) {
        $secretName = $key.ToLower().Replace("_", "-")
        if ($SECRET_VARS -contains $key) {
            $envVarsYaml += "      - name: $key`n        secretRef: $secretName`n"
        } else {
            $envVarsYaml += "      - name: $key`n        value: $(ConvertTo-YamlSafe $envVars[$key])`n"
        }
    }

    $subscriptionId = az account show --query id -o tsv

    $yamlContent = @"
properties:
  managedEnvironmentId: /subscriptions/$subscriptionId/resourceGroups/$RESOURCE_GROUP/providers/Microsoft.App/managedEnvironments/$ENVIRONMENT_NAME
  configuration:
    activeRevisionsMode: Single
    ingress:
      external: true
      targetPort: $CONTAINER_PORT
      transport: auto
      allowInsecure: false
    registries:
    - server: $ACR_LOGIN_SERVER
      username: $ACR_USERNAME
      passwordSecretRef: acr-password
    secrets:
$secretsYaml
  template:
    containers:
    - image: ${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}
      name: $CONTAINER_APP_NAME
      resources:
        cpu: 1.0
        memory: 2Gi
      env:
$envVarsYaml
      probes:
      - type: Liveness
        httpGet:
          path: /api/health
          port: $CONTAINER_PORT
        initialDelaySeconds: 10
        periodSeconds: 30
      - type: Readiness
        httpGet:
          path: /api/health
          port: $CONTAINER_PORT
        initialDelaySeconds: 5
        periodSeconds: 10
    scale:
      minReplicas: 0
      maxReplicas: 3
      rules:
      - name: http-scaling
        http:
          metadata:
            concurrentRequests: "20"
"@

    $yamlFile = "containerapp-config.yaml"
    [System.IO.File]::WriteAllText(
        (Join-Path $PWD $yamlFile),
        $yamlContent,
        [System.Text.UTF8Encoding]::new($false)
    )
    Write-Host "  Saved $yamlFile" -ForegroundColor Gray

    # == Step 7: Create / Update Container App =================================
    if ($startStep -le 6) {
        Write-Host ""
        Write-Host "[Step 7/8] Deploying Container App..." -ForegroundColor Cyan

        $appExists = az containerapp show `
            --name $CONTAINER_APP_NAME `
            --resource-group $RESOURCE_GROUP `
            --query "name" -o tsv 2>$null

        if ($appExists) {
            Write-Host "  App exists - updating..." -ForegroundColor Yellow
            az containerapp update `
                --name $CONTAINER_APP_NAME `
                --resource-group $RESOURCE_GROUP `
                --yaml $yamlFile `
                --output none
            Test-StepSuccess "Container App update (YAML)"

            az containerapp update `
                --name $CONTAINER_APP_NAME `
                --resource-group $RESOURCE_GROUP `
                --image "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" `
                --output none
            Test-StepSuccess "Container App image update"
        } else {
            Write-Host "  Creating new Container App..." -ForegroundColor Gray
            az containerapp create `
                --name $CONTAINER_APP_NAME `
                --resource-group $RESOURCE_GROUP `
                --environment $ENVIRONMENT_NAME `
                --yaml $yamlFile `
                --output none
            Test-StepSuccess "Container App creation"
        }

        Remove-Item $yamlFile -ErrorAction SilentlyContinue
    } else {
        Write-Host "[Step 7/8] Skipped (Container App)" -ForegroundColor Gray
    }

    # == Step 8: Get Application URL ===========================================
    Write-Host ""
    Write-Host "[Step 8/8] Retrieving application URL..." -ForegroundColor Cyan

    Start-Sleep -Seconds 5

    $APP_FQDN = az containerapp show `
        --name $CONTAINER_APP_NAME `
        --resource-group $RESOURCE_GROUP `
        --query "properties.configuration.ingress.fqdn" -o tsv

    if (-not $APP_FQDN) {
        Write-Host "  WARNING: URL not available yet - app may still be provisioning." -ForegroundColor Yellow
        $APP_FQDN = "<pending>"
    }

    Write-Host ""
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host "  Deployment Complete!" -ForegroundColor Green
    Write-Host "========================================================" -ForegroundColor Green
    Write-Host ""
    Write-Host "Web UI:" -ForegroundColor Yellow
    Write-Host "  https://$APP_FQDN" -ForegroundColor White
    Write-Host ""
    Write-Host "Health Check:" -ForegroundColor Yellow
    Write-Host "  https://$APP_FQDN/api/health" -ForegroundColor White
    Write-Host ""
    Write-Host "--- Useful Commands ---" -ForegroundColor Magenta
    Write-Host "  View logs:    az containerapp logs show -n $CONTAINER_APP_NAME -g $RESOURCE_GROUP --follow"
    Write-Host "  Status:       az containerapp show -n $CONTAINER_APP_NAME -g $RESOURCE_GROUP --query properties.runningStatus"
    Write-Host "  Revisions:    az containerapp revision list -n $CONTAINER_APP_NAME -g $RESOURCE_GROUP -o table"
    Write-Host "  Delete all:   az group delete -n $RESOURCE_GROUP --yes --no-wait"
    Write-Host ""
}
else {
    Write-Host "Invalid choice. Run the script again and enter 1 or 2." -ForegroundColor Red
    exit 1
}
