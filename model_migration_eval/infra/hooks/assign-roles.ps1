# ---------------------------------------------------------------------------
# assign-roles.ps1 — Manually assign RBAC roles after azd provision
#
# Use this script when Conditional Access policies in the tenant block
# role assignments during ARM deployment (GraphBadRequest error).
#
# Usage:
#   .\infra\hooks\assign-roles.ps1 -EnvironmentName <your-azd-env-name>
#
# Prerequisites: Azure CLI logged in (az login)
# ---------------------------------------------------------------------------

param(
    [Parameter(Mandatory = $false)]
    [string]$EnvironmentName
)

$ErrorActionPreference = "Stop"

# If no env name provided, try to get it from azd
if (-not $EnvironmentName) {
    $EnvironmentName = (azd env get-values 2>$null | Select-String "^AZURE_ENV_NAME=" | ForEach-Object { $_ -replace "^AZURE_ENV_NAME=", "" -replace '"', '' })
    if (-not $EnvironmentName) {
        Write-Error "Could not determine environment name. Pass -EnvironmentName or run 'azd env select'."
        exit 1
    }
}

Write-Host "`n=== Assigning RBAC roles for environment: $EnvironmentName ===" -ForegroundColor Cyan

$rg = "rg-$EnvironmentName"

# Get the managed identity principal ID
Write-Host "`nLooking up managed identity..." -ForegroundColor Yellow
$allIdentities = az identity list -g $rg -o json | ConvertFrom-Json
$identities = @($allIdentities | Where-Object { $_.name -like '*id-web-*' })
if ($identities.Count -eq 0) {
    Write-Error "No managed identity found in resource group $rg"
    exit 1
}
$principalId = $identities[0].principalId
$identityName = $identities[0].name
Write-Host "  Identity: $identityName"
Write-Host "  Principal ID: $principalId"

# Get the AI Services account
Write-Host "`nLooking up AI Services account..." -ForegroundColor Yellow
$allAiAccounts = az cognitiveservices account list -g $rg -o json | ConvertFrom-Json
$aiAccounts = @($allAiAccounts | Where-Object { $_.kind -eq 'AIServices' })
if ($aiAccounts.Count -eq 0) {
    Write-Error "No AI Services account found in resource group $rg"
    exit 1
}
$aiAccountId = $aiAccounts[0].id
$aiAccountName = $aiAccounts[0].name
Write-Host "  Account: $aiAccountName"

# Get the ACR
Write-Host "`nLooking up Container Registry..." -ForegroundColor Yellow
$acrs = az acr list -g $rg -o json | ConvertFrom-Json
if ($acrs.Count -eq 0) {
    Write-Error "No Container Registry found in resource group $rg"
    exit 1
}
$acrId = $acrs[0].id
$acrName = $acrs[0].name
Write-Host "  ACR: $acrName"

# Get the resource group ID
$rgId = az group show -n $rg --query id -o tsv

# Role definitions
$roles = @(
    @{ Name = "AcrPull"; Id = "7f951dda-4ed3-4680-a7ca-43fe172d538d"; Scope = $acrId }
    @{ Name = "Cognitive Services OpenAI Contributor"; Id = "a001fd3d-188f-4b5d-821b-7da978bf7442"; Scope = $aiAccountId }
    @{ Name = "Cognitive Services OpenAI User"; Id = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"; Scope = $aiAccountId }
    @{ Name = "Azure AI Developer"; Id = "64702f94-c441-49e6-a78b-ef80e0188fee"; Scope = $aiAccountId }
    @{ Name = "Azure AI User"; Id = "53ca6127-db72-4b80-b1b0-d745d6d5456d"; Scope = $aiAccountId }
    @{ Name = "User Access Administrator"; Id = "18d7d88d-d35e-4fb5-a5c3-7773c20a72d9"; Scope = $aiAccountId }
    @{ Name = "Storage Blob Data Contributor"; Id = "ba92f5b4-2d11-453d-a403-e96b0029c9fe"; Scope = $rgId }
)

Write-Host "`nAssigning roles..." -ForegroundColor Yellow
foreach ($role in $roles) {
    Write-Host "  Assigning '$($role.Name)'..." -NoNewline
    try {
        az role assignment create `
            --assignee-object-id $principalId `
            --assignee-principal-type ServicePrincipal `
            --role $role.Id `
            --scope $role.Scope `
            --only-show-errors 2>&1 | Out-Null
        Write-Host " OK" -ForegroundColor Green
    }
    catch {
        Write-Host " FAILED" -ForegroundColor Red
        Write-Host "    Error: $_" -ForegroundColor Red
    }
}

# ── Dedicated Realtime/TTS voice account (managed by Bicep) ─────────────────
# The voice OpenAI account is now created by infra/modules/realtime-resource.bicep
# in the same resource group. Discover it by kind=OpenAI and assign roles.
Write-Host "`nLooking up dedicated voice (OpenAI) account..." -ForegroundColor Yellow
$voiceAccounts = @($allAiAccounts | Where-Object { $_.kind -eq 'OpenAI' })
if ($voiceAccounts.Count -gt 0) {
    $voiceAccountId = $voiceAccounts[0].id
    $voiceAccountName = $voiceAccounts[0].name
    Write-Host "  Voice account: $voiceAccountName"
    $rtRoles = @(
        @{ Name = "Cognitive Services OpenAI Contributor"; Id = "a001fd3d-188f-4b5d-821b-7da978bf7442"; Scope = $voiceAccountId }
        @{ Name = "Cognitive Services OpenAI User"; Id = "5e0bd9bd-7b93-4f28-af87-19fc36ad61bd"; Scope = $voiceAccountId }
    )
    foreach ($role in $rtRoles) {
        Write-Host "  Assigning '$($role.Name)' on voice account..." -NoNewline
        try {
            az role assignment create `
                --assignee-object-id $principalId `
                --assignee-principal-type ServicePrincipal `
                --role $role.Id `
                --scope $role.Scope `
                --only-show-errors 2>&1 | Out-Null
            Write-Host " OK" -ForegroundColor Green
        }
        catch {
            Write-Host " FAILED" -ForegroundColor Red
            Write-Host "    Error: $_" -ForegroundColor Red
        }
    }
} else {
    Write-Host "  No voice (kind=OpenAI) account found in $rg -- Realtime roles skipped." -ForegroundColor Gray
}

Write-Host "`n=== Done! All roles assigned. ===" -ForegroundColor Green
Write-Host "You can now run 'azd deploy' to push the container image.`n"
