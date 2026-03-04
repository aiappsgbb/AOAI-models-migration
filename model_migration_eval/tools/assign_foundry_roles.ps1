<#
.SYNOPSIS
    Assigns Reader + Azure AI User roles on the AI Foundry resource to one or more users.

.DESCRIPTION
    Grants users the minimum roles needed to view evaluation results
    in the Azure AI Foundry portal (https://ai.azure.com):

      - Reader           = ARM read-only (see the project in Azure Portal)
      - Azure AI User    = data-plane read (browse evaluations, runs, assets)

    The scope (AI Services resource ID) is auto-derived from
    FOUNDRY_PROJECT_ENDPOINT in .env.

    Requires: Azure CLI (az) logged in with Owner or User Access
    Administrator on the target resource.

.PARAMETER Email
    One or more user emails.  Accepts a comma-separated list or array.

.PARAMETER File
    Path to a text file with one email per line (blank lines and
    # comments are ignored).

.EXAMPLE
    # Single user
    .\tools\assign_foundry_roles.ps1 -Email "user@microsoft.com"

.EXAMPLE
    # Multiple users
    .\tools\assign_foundry_roles.ps1 -Email "user1@ms.com","user2@ms.com","user3@ms.com"

.EXAMPLE
    # From a file (one email per line)
    .\tools\assign_foundry_roles.ps1 -File attendees.txt
#>

[CmdletBinding(DefaultParameterSetName = 'ByEmail')]
param(
    [Parameter(Mandatory = $true, Position = 0, ParameterSetName = 'ByEmail')]
    [string[]]$Email,

    [Parameter(Mandatory = $true, ParameterSetName = 'ByFile')]
    [string]$File
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Write-Step { param($m) Write-Host "  > $m" -ForegroundColor Cyan }
function Write-Ok   { param($m) Write-Host "  [OK] $m" -ForegroundColor Green }
function Write-Err  { param($m) Write-Host "  [!!] $m" -ForegroundColor Red }

# -- Build email list ----------------------------------------------------------
$emails = @()
if ($File) {
    if (-not (Test-Path $File)) { Write-Err "File not found: $File"; exit 1 }
    $emails = @(Get-Content $File | ForEach-Object { $_.Trim() } |
                Where-Object { $_ -and $_ -notmatch '^#' })
} else {
    # Flatten: handle commas inside a single string (cmd.exe passes arrays as one string)
    $emails = @($Email | ForEach-Object { $_ -split ',' } |
                ForEach-Object { $_.Trim().Trim('"').Trim("'") } |
                Where-Object { $_ })
}

if ($emails.Count -eq 0) { Write-Err "No emails provided."; exit 1 }

Write-Host ""
Write-Host "  Assign Foundry Viewer Roles (Reader + Azure AI User)" -ForegroundColor Blue
Write-Host "  Users: $($emails.Count)" -ForegroundColor Blue
Write-Host ""

# -- 1. Verify Azure CLI -------------------------------------------------------
$account = az account show 2>&1 | ConvertFrom-Json
if (-not $account) { Write-Err "Not logged in. Run 'az login' first."; exit 1 }
Write-Ok "Logged in as $($account.user.name)"

# -- 2. Resolve scope from .env ------------------------------------------------
$envFile = Join-Path $PSScriptRoot "..\.env"
if (-not (Test-Path $envFile)) { Write-Err ".env not found at $envFile"; exit 1 }

$endpoint = (Get-Content $envFile | Where-Object { $_ -match "^FOUNDRY_PROJECT_ENDPOINT=" }) `
            -replace "^FOUNDRY_PROJECT_ENDPOINT=", "" | Select-Object -First 1

if (-not $endpoint -or $endpoint -notmatch "https://([^.]+)\.services\.ai\.azure\.com") {
    Write-Err "Cannot parse FOUNDRY_PROJECT_ENDPOINT from .env"; exit 1
}
$accountName = $Matches[1]

Write-Step "Looking up resource ID for '$accountName'..."
$Scope = (az resource list --name $accountName --resource-type "Microsoft.CognitiveServices/accounts" --query "[0].id" -o tsv 2>&1).Trim()
if (-not $Scope -or $Scope -like "*ERROR*") { Write-Err "AI Services account '$accountName' not found."; exit 1 }
Write-Ok "Scope: $Scope"

# -- 3. Process each user ------------------------------------------------------
$roles = @(
    @{ Name = "Reader";        Id = "acdd72a7-3385-48ef-bd42-f606fba81ae7" }
    @{ Name = "Azure AI User"; Id = "53ca6127-db72-4b80-b1b0-d745d6d5456d" }
)

$succeeded = @()
$failed    = @()

foreach ($addr in $emails) {
    Write-Host ""
    Write-Host "  -- $addr --" -ForegroundColor White

    # Resolve objectId via display-name search
    $prefix = ($addr -split "@")[0]
    $displaySearch = ($prefix -replace '[._]', ' ').Trim()

    Write-Step "Searching Azure AD for '$displaySearch'..."
    $searchResult = az ad user list --display-name $displaySearch 2>&1
    if ($LASTEXITCODE -ne 0) {
        Write-Err "Azure AD search failed - skipping."
        $failed += $addr
        continue
    }

    $users = $searchResult | ConvertFrom-Json
    $match = $null
    $match = $users | Where-Object {
        $_.mail -ieq $addr -or
        $_.userPrincipalName -ieq $addr -or
        ($_.PSObject.Properties['otherMails'] -and $_.otherMails -contains $addr) -or
        $_.userPrincipalName -ilike "$($prefix.Replace('.','_'))*"
    } | Select-Object -First 1

    if (-not $match) {
        Write-Err "Not found in Azure AD - skipping."
        $failed += $addr
        continue
    }
    Write-Ok "Found: $($match.displayName) ($($match.id))"

    # Assign roles
    $userOk = $true
    foreach ($role in $roles) {
        Write-Step "Assigning '$($role.Name)'..."
        $result = az role assignment create `
            --assignee-object-id $match.id `
            --assignee-principal-type "User" `
            --role $role.Id `
            --scope $Scope `
            --only-show-errors 2>&1

        if ($LASTEXITCODE -eq 0) {
            Write-Ok "'$($role.Name)' assigned"
        } elseif ("$result" -match "already exists") {
            Write-Ok "'$($role.Name)' already assigned"
        } else {
            Write-Err "Failed: $result"
            $userOk = $false
        }
    }

    if ($userOk) { $succeeded += $addr } else { $failed += $addr }
}

# -- 4. Summary ----------------------------------------------------------------
Write-Host ""
Write-Host "  =========================================================" -ForegroundColor Blue
Write-Host "  Results: $($succeeded.Count) succeeded, $($failed.Count) failed (of $($emails.Count) total)" -ForegroundColor Blue
if ($succeeded.Count -gt 0) {
    Write-Ok "Succeeded: $($succeeded -join ', ')"
}
if ($failed.Count -gt 0) {
    Write-Err "Failed:    $($failed -join ', ')"
}
Write-Host "  =========================================================" -ForegroundColor Blue
Write-Host ""
