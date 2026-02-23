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

# Environment variables that contain secrets (will be stored as Container Apps secrets)
$SECRET_VARS = @("AZURE_OPENAI_API_KEY", "AZURE_CLIENT_SECRET")

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
        Write-Host "  Service Principal credentials found in .env - skipping creation" -ForegroundColor Green
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

    # --- Assign roles on AI resource ---
    $envVarsLocal = Read-EnvFile $ENV_FILE
    $foundryEp = $envVarsLocal["FOUNDRY_PROJECT_ENDPOINT"]

    if ($foundryEp -and $foundryEp -match 'https://([^.]+)\.') {
        $aiResName = $matches[1]
        Write-Host "  Looking up AI resource '$aiResName'..." -ForegroundColor Gray
        $resId = az resource list --name $aiResName --query "[0].id" -o tsv 2>$null

        if ($resId) {
            # Foundry evaluation needs multiple roles:
            #  - Cognitive Services OpenAI Contributor: call models
            #  - Azure AI Developer: create evaluations, upload datasets
            #  - Storage Blob Data Contributor: read/write eval data in storage
            $roles = @(
                "Cognitive Services OpenAI Contributor",
                "Azure AI Developer"
            )
            foreach ($role in $roles) {
                Write-Host "  Assigning '$role'..." -ForegroundColor Cyan
                az role assignment create `
                    --assignee $sp.appId `
                    --role $role `
                    --scope $resId `
                    --output none 2>$null
                if ($LASTEXITCODE -eq 0) {
                    Write-Host "    OK" -ForegroundColor Green
                } else {
                    Write-Host "    May already exist or insufficient permissions" -ForegroundColor Yellow
                }
            }

            # Also assign Storage Blob Data Contributor on the resource group
            $rgScope = ($resId -split '/providers/')[0]
            if ($rgScope) {
                Write-Host "  Assigning 'Storage Blob Data Contributor' on resource group..." -ForegroundColor Cyan
                az role assignment create `
                    --assignee $sp.appId `
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
            Write-Host "  Assign roles to $($sp.appId) manually." -ForegroundColor Yellow
        }
    } else {
        Write-Host "  FOUNDRY_PROJECT_ENDPOINT not in .env -- skipping role assignment." -ForegroundColor Yellow
    }

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
    foreach ($key in @("AZURE_OPENAI_ENDPOINT", "AZURE_OPENAI_API_KEY", "FOUNDRY_PROJECT_ENDPOINT", "AZURE_TENANT_ID", "AZURE_CLIENT_ID", "AZURE_CLIENT_SECRET")) {
        $val = $finalEnv[$key]
        if ($val) {
            $masked = $val.Substring(0, [Math]::Min(8, $val.Length)) + "..."
            Write-Host "  $key = $masked" -ForegroundColor Green
        } else {
            Write-Host "  $key = (empty)" -ForegroundColor Red
            if ($key -like "AZURE_*_ID" -or $key -like "AZURE_CLIENT_SECRET") { $spOk = $false }
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
    Write-Host "  1. Skip to Step 2 (ACR)          - Resource Group already exists"
    Write-Host "  2. Skip to Step 3 (Docker)        - ACR already exists"
    Write-Host "  3. Skip to Step 4 (Environment)   - Image already pushed"
    Write-Host "  4. Skip to Step 5 (Env vars)      - Environment already exists"
    Write-Host "  5. Skip to Step 6 (Container App) - Ready to create / update app"
    Write-Host "  6. Skip to Step 7 (Get URL)       - App already deployed"
    Write-Host ""
    $startStep = Read-Host "Start from step [0-6, default 0]"
    if (-not $startStep) { $startStep = "0" }
    $startStep = [int]$startStep

    # --- Read .env ---
    Write-Host "Reading environment variables from $ENV_FILE..." -ForegroundColor Cyan
    $envVars = Read-EnvFile $ENV_FILE
    Write-Host "  Found $($envVars.Count) variables" -ForegroundColor Gray

    # == Step 1: Resource Group ================================================
    if ($startStep -le 0) {
        Write-Host ""
        Write-Host "[Step 1/7] Checking Resource Group..." -ForegroundColor Cyan

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
        Write-Host "[Step 1/7] Skipped (Resource Group)" -ForegroundColor Gray
    }

    # == Step 2: Azure Container Registry ======================================
    if ($startStep -le 1) {
        Write-Host ""
        Write-Host "[Step 2/7] Azure Container Registry..." -ForegroundColor Cyan

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
        Write-Host "[Step 2/7] Skipped (ACR)" -ForegroundColor Gray
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
        Write-Host "[Step 3/7] Building and pushing Docker image..." -ForegroundColor Cyan

        az acr login --name $ACR_NAME
        Test-StepSuccess "ACR login"

        docker build --no-cache -t "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}" -f $DOCKERFILE .
        Test-StepSuccess "Docker build"

        docker push "${ACR_LOGIN_SERVER}/${IMAGE_NAME}:${IMAGE_TAG}"
        Test-StepSuccess "Docker push"
    } else {
        Write-Host "[Step 3/7] Skipped (Docker build/push)" -ForegroundColor Gray
    }

    # == Step 4: Container Apps Environment ====================================
    if ($startStep -le 3) {
        Write-Host ""
        Write-Host "[Step 4/7] Container Apps Environment..." -ForegroundColor Cyan

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
        Write-Host "[Step 4/7] Skipped (Environment)" -ForegroundColor Gray
    }

    # == Step 5: Build YAML configuration ======================================
    Write-Host ""
    Write-Host "[Step 5/7] Preparing environment variables & secrets..." -ForegroundColor Cyan

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

    # == Step 6: Create / Update Container App =================================
    if ($startStep -le 5) {
        Write-Host ""
        Write-Host "[Step 6/7] Deploying Container App..." -ForegroundColor Cyan

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
        Write-Host "[Step 6/7] Skipped (Container App)" -ForegroundColor Gray
    }

    # == Step 7: Get Application URL ===========================================
    Write-Host ""
    Write-Host "[Step 7/7] Retrieving application URL..." -ForegroundColor Cyan

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
