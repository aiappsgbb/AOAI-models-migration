// ---------------------------------------------------------------------------
// main.bicep — Azure Developer CLI entry point
// Deploys: RG → Monitoring → ACR + CAE → Identity → AI Services hub → App
//
// Always creates all resources from scratch using a SINGLE AI Services
// account (kind: AIServices) which:
//   • Hosts all model deployments (gpt-4.1, gpt-4o, gpt-4.1-mini, gpt-5.4, gpt-5.1, gpt-5.2,
//     Phi-4, gpt-realtime, gpt-realtime-1.5, gpt-4o-mini-tts)
//   • Acts as AI Foundry hub with a project for evaluations
//   • Provides the OpenAI-compatible endpoint for the app
//
// Optionally assigns RBAC roles on a SEPARATE Cognitive Services account
// used as the dedicated Realtime/TTS voice endpoint (via realtime-access.bicep).
//
// This avoids the 'kind: OpenAI' SKU which requires a separate quota.
// ---------------------------------------------------------------------------

targetScope = 'subscription'

// ── Parameters ─────────────────────────────────────────────────────────────
@minLength(1)
@maxLength(64)
@description('Name of the azd environment — used to derive all resource names')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources')
param location string

@description('Container image name for the web service. Set automatically by azd.')
param webImageName string = ''

@description('SMTP host for authentication emails. Leave empty for console-mode (OTP printed to stdout).')
param smtpHost string = ''

@description('SMTP username for authentication emails.')
param smtpUsername string = ''

@secure()
@description('SMTP password (stored as Container Apps secret).')
param smtpPassword string = ''

@description('SMTP sender email address.')
param smtpFromEmail string = ''

@description('Auth code verification: "true" for full OTP flow, "false" for email-only sign-in. Overrides settings.yaml.')
param authCodeVerification string = ''

@description('Auth email provider: "smtp" for real emails, "console" for stdout (dev). Overrides settings.yaml.')
param authEmailProvider string = ''

@description('EasyAuth auto-login: "true" (default) to auto sign-in via Container Apps EasyAuth headers, "false" to always show login page. Overrides settings.yaml.')
param authEasyAuthAutoLogin string = ''

@description('Enable auto-assignment of Foundry Reader roles to users on login. Grants User Access Administrator to the managed identity. Default: false.')
param enableAutoAssignFoundryReader bool = false

@secure()
@description('Flask session signing key (stored as Container Apps secret). Auto-generated if empty.')
param flaskSecretKey string = ''

@secure()
@description('Google Gemini API key for Gemini model evaluations (optional). Get from https://aistudio.google.com/')
param geminiApiKey string = ''

@description('Azure region for the dedicated Realtime/TTS voice endpoint. Voice models are deployed to a separate OpenAI account which must be in a region with gpt-realtime quota. Defaults to swedencentral.')
param realtimeLocation string = 'swedencentral'

@description('Deploy voice models (gpt-realtime) to a dedicated account. Set to "false" if the subscription does not have access to Realtime models. Default: true.')
param deployVoiceModels string = 'true'

@description('Principal ID (object ID) of the deployer for Storage RBAC. Auto-populated by azd for the logged-in user. Leave empty to skip.')
param deployerPrincipalId string = ''

// ── Derived names ──────────────────────────────────────────────────────────
var resourceSuffix = take(uniqueString(subscription().id, environmentName, location), 6)
var envNameLower = replace(toLower(environmentName), '_', '-')
var resourceGroupName = 'rg-${environmentName}'

// Single AI Services account — hosts ALL models + Foundry project
var aiServicesName = 'ais-${envNameLower}-${resourceSuffix}'
var foundryProjectName = '${envNameLower}-project'

// Storage Account for persisting user data across container restarts
var storageAccountName = take('st${replace(envNameLower, '-', '')}${resourceSuffix}', 24)

// All model deployments live in the AI Services account
// format defaults to 'OpenAI'; Marketplace models (Mistral) need their own format.
// capacity = TPM in units of 1K (e.g. 1000 = 1 M TPM). Set high to avoid 429s.
var modelDeployments = [
  { name: 'gpt-4.1', model: 'gpt-4.1', version: '2025-04-14', skuName: 'GlobalStandard', capacity: 1000 }
  { name: 'gpt-4o', model: 'gpt-4o', version: '2024-08-06', skuName: 'GlobalStandard', capacity: 1000 }
  { name: 'gpt-4.1-mini', model: 'gpt-4.1-mini', version: '2025-04-14', skuName: 'GlobalStandard', capacity: 1000 }
  { name: 'gpt-5.4', model: 'gpt-5.4', version: '2026-03-05', skuName: 'GlobalStandard', capacity: 1000 }
  { name: 'gpt-5.1', model: 'gpt-5.1', version: '2025-11-13', skuName: 'GlobalStandard', capacity: 1000 }
  { name: 'gpt-5.2', model: 'gpt-5.2', version: '2025-12-11', skuName: 'GlobalStandard', capacity: 1000 }
  // TTS model — lives in the primary AI Services account because
  // gpt-4o-mini-tts is only available in eastus2 (not swedencentral).
  { name: 'gpt-4o-mini-tts', model: 'gpt-4o-mini-tts', version: '2025-03-20', skuName: 'GlobalStandard', capacity: 100 }
  // Phi-4 SLM — Microsoft model catalog (format: 'Microsoft')
  { name: 'Phi-4', model: 'Phi-4', version: '2', format: 'Microsoft', skuName: 'GlobalStandard', capacity: 1 }
  // Note: Mistral removed — requires Marketplace subscription agreement that
  // blocks the entire @batchSize(1) loop when it fails, preventing subsequent
  // resources (Foundry project) from being created.
  // { name: 'Mistral-Large-3', model: 'Mistral-Large-3', version: '1', format: 'Mistral AI', skuName: 'GlobalStandard', capacity: 100 }
]

// Voice model deployments — deployed to a dedicated AI Services account
// in a region with Realtime quota (may differ from the primary region).
// Capacity is kept low (1 RPM) to fit within default quotas; increase after
// requesting additional quota in the Azure portal.
// Note: gpt-4o-mini-tts is NOT here — it's only in eastus2 (primary account).
var voiceModelDeployments = [
  { name: 'gpt-realtime', model: 'gpt-realtime', version: '2025-08-28', skuName: 'GlobalStandard', capacity: 1 }
  { name: 'gpt-realtime-1.5', model: 'gpt-realtime-1.5', version: '2026-02-23', skuName: 'GlobalStandard', capacity: 1 }
]

// Convert string param to bool (handles empty string from unset env var)
// When the env var is unset, azd passes an empty string — treat that as 'true' (default).
// Only skip voice models when explicitly set to 'false'.
var enableVoiceModels = toLower(deployVoiceModels) != 'false'

// Use the explicit realtimeLocation; falls back to param default (swedencentral)
// if the env var is empty. Never use the primary 'location' — the primary
// AI Services account already consumes the gpt-realtime quota there.
var effectiveRealtimeLocation = !empty(realtimeLocation) ? realtimeLocation : 'swedencentral'
var voiceAccountName = 'oai-voice-${envNameLower}-${resourceSuffix}'

var tags = {
  'azd-env-name': environmentName
}

// ── Resource Group ─────────────────────────────────────────────────────────
resource rg 'Microsoft.Resources/resourceGroups@2024-03-01' = {
  name: resourceGroupName
  location: location
  tags: tags
}

// ── Monitoring (AVM) ───────────────────────────────────────────────────────
module monitoring 'br/public:avm/ptn/azd/monitoring:0.1.0' = {
  name: 'monitoring'
  scope: rg
  params: {
    logAnalyticsName: 'log-${envNameLower}-${resourceSuffix}'
    applicationInsightsName: 'ai-${envNameLower}-${resourceSuffix}'
    applicationInsightsDashboardName: 'aid-${envNameLower}-${resourceSuffix}'
    location: location
    tags: tags
  }
}

// ── Container Apps Stack — ACR + CAE (AVM) ─────────────────────────────────
module containerApps 'br/public:avm/ptn/azd/container-apps-stack:0.1.0' = {
  name: 'container-apps-stack'
  scope: rg
  params: {
    containerAppsEnvironmentName: 'cae-${envNameLower}-${resourceSuffix}'
    containerRegistryName: 'acr${replace(envNameLower, '-', '')}${resourceSuffix}'
    logAnalyticsWorkspaceResourceId: monitoring.outputs.logAnalyticsWorkspaceResourceId
    location: location
    tags: tags
    acrSku: 'Basic'
    acrAdminUserEnabled: true
    zoneRedundant: false
  }
}

// ── User-Assigned Managed Identity ─────────────────────────────────────────
module webIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.0' = {
  name: 'web-identity'
  scope: rg
  params: {
    name: 'id-web-${envNameLower}-${resourceSuffix}'
    location: location
    tags: tags
  }
}

// ── ACR Pull ───────────────────────────────────────────────────────────────
module acrAccess './modules/acr-access.bicep' = {
  name: 'acr-access'
  scope: rg
  params: {
    containerRegistryName: containerApps.outputs.registryName
    principalId: webIdentity.outputs.principalId
  }
}

// ── AI Services hub + model deployments + Foundry project ──────────────────
// Single account (kind: AIServices) replaces both the OpenAI account and
// the Foundry hub. All models are deployed here and the same endpoint is
// used for both app inference and Foundry evaluations.
module aiServices './modules/foundry-resource.bicep' = {
  name: 'ai-services'
  scope: rg
  params: {
    name: aiServicesName
    projectName: foundryProjectName
    location: location
    tags: tags
    deployments: modelDeployments
  }
}

// ── RBAC: All roles (OpenAI + Foundry + Storage) ───────────────────────────
module foundryAccess './modules/foundry-access.bicep' = {
  name: 'foundry-access'
  scope: rg
  params: {
    accountName: aiServices.outputs.accountName
    projectName: aiServices.outputs.projectName
    principalId: webIdentity.outputs.principalId
    enableAutoAssign: enableAutoAssignFoundryReader
  }
}

// ── Dedicated Azure OpenAI account for voice models (Realtime + TTS) ───────
// Deployed in a potentially different region to avoid quota issues.
// Skipped when deployVoiceModels is false (subscription lacks access).
module realtimeServices './modules/realtime-resource.bicep' = if (enableVoiceModels) {
  name: 'realtime-services'
  scope: rg
  params: {
    name: voiceAccountName
    location: effectiveRealtimeLocation
    tags: tags
    deployments: voiceModelDeployments
  }
}

// ── RBAC: Voice endpoint (OpenAI Contributor + User) ───────────────────────
module realtimeAccess './modules/realtime-access.bicep' = if (enableVoiceModels) {
  name: 'realtime-access'
  scope: rg
  params: {
    accountName: realtimeServices!.outputs.accountName
    principalId: webIdentity.outputs.principalId
  }
}

// ── Storage Account for user-data persistence ─────────────────────────────
module storage './modules/storage-resource.bicep' = {
  name: 'storage'
  scope: rg
  params: {
    name: storageAccountName
    location: location
    tags: tags
    principalId: webIdentity.outputs.principalId
    deployerPrincipalId: deployerPrincipalId
  }
}

// ── Container App ──────────────────────────────────────────────────────────
var containerAppName = take('ca-${envNameLower}-${resourceSuffix}', 32)
var containerImage = !empty(webImageName)
  ? webImageName
  : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

// Build env vars: base + optional SMTP/auth (values come from @secure params)
var baseEnv = [
  { name: 'PYTHONUNBUFFERED', value: '1' }
  { name: 'AZURE_OPENAI_ENDPOINT', value: aiServices.outputs.endpoint }
  { name: 'FOUNDRY_PROJECT_ENDPOINT', value: aiServices.outputs.projectEndpoint }
  { name: 'AZURE_CLIENT_ID', value: webIdentity.outputs.clientId }
  { name: 'AZURE_TENANT_ID', value: tenant().tenantId }
  { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.applicationInsightsConnectionString }
  { name: 'AZURE_STORAGE_ACCOUNT_NAME', value: storage.outputs.accountName }
]
var smtpEnv = empty(smtpHost) ? [] : [
  { name: 'SMTP_HOST', value: smtpHost }
  { name: 'SMTP_USERNAME', value: smtpUsername }
  { name: 'SMTP_PASSWORD', value: smtpPassword }
  { name: 'SMTP_FROM_EMAIL', value: smtpFromEmail }
]
// Auto-generate a deterministic Flask secret key from resource identifiers when not
// explicitly provided.  This keeps the key stable across container restarts / redeployments
// while still being unique per resource-group + subscription.
var generatedFlaskKey = guid(rg.id, 'flask-session-signing-key')
var effectiveFlaskKey = empty(flaskSecretKey) ? generatedFlaskKey : flaskSecretKey
var authEnv = [
  { name: 'FLASK_SECRET_KEY', value: effectiveFlaskKey }
]
var codeVerifEnv = empty(authCodeVerification) ? [] : [
  { name: 'AUTH_CODE_VERIFICATION', value: authCodeVerification }
]
var emailProviderEnv = empty(authEmailProvider) ? [] : [
  { name: 'AUTH_EMAIL_PROVIDER', value: authEmailProvider }
]
var easyAuthEnv = empty(authEasyAuthAutoLogin) ? [] : [
  { name: 'AUTH_EASYAUTH_AUTO_LOGIN', value: authEasyAuthAutoLogin }
]
var geminiEnv = empty(geminiApiKey) ? [] : [
  { name: 'GEMINI_API_KEY', value: geminiApiKey }
]
var realtimeEnv = enableVoiceModels ? [
  { name: 'AZURE_OPENAI_REALTIME_ENDPOINT', value: realtimeServices!.outputs.endpoint }
] : []
var containerEnv = concat(baseEnv, smtpEnv, authEnv, codeVerifEnv, emailProviderEnv, easyAuthEnv, geminiEnv, realtimeEnv)

module web 'br/public:avm/res/app/container-app:0.10.0' = {
  name: 'web-container-app'
  scope: rg
  dependsOn: [acrAccess]
  params: {
    name: containerAppName
    location: location
    tags: union(tags, { 'azd-service-name': 'web' })
    environmentResourceId: '${rg.id}/providers/Microsoft.App/managedEnvironments/${containerApps.outputs.environmentName}'
    managedIdentities: {
      userAssignedResourceIds: [webIdentity.outputs.resourceId]
    }
    registries: [
      {
        server: containerApps.outputs.registryLoginServer
        identity: webIdentity.outputs.resourceId
      }
    ]

    ingressExternal: true
    ingressTargetPort: 5000
    ingressTransport: 'auto'
    ingressAllowInsecure: false

    scaleMinReplicas: 0
    scaleMaxReplicas: 3
    scaleRules: [
      {
        name: 'http-scaling'
        http: {
          metadata: {
            concurrentRequests: '20'
          }
        }
      }
    ]

    containers: [
      {
        image: containerImage
        name: 'main'
        env: containerEnv
        resources: {
          cpu: json('1.0')
          memory: '2.0Gi'
        }
        probes: [
          {
            type: 'Liveness'
            httpGet: { path: '/api/health', port: 5000 }
            initialDelaySeconds: 10
            periodSeconds: 30
          }
          {
            type: 'Readiness'
            httpGet: { path: '/api/health', port: 5000 }
            initialDelaySeconds: 5
            periodSeconds: 10
          }
        ]
      }
    ]
  }
}

// ── Outputs (consumed by azd) ──────────────────────────────────────────────
output AZURE_CONTAINER_REGISTRY_ENDPOINT string = containerApps.outputs.registryLoginServer
output AZURE_CONTAINER_REGISTRY_NAME string = containerApps.outputs.registryName
output AZURE_CONTAINER_ENVIRONMENT_NAME string = containerApps.outputs.environmentName
output AZURE_RESOURCE_GROUP string = rg.name
output SERVICE_WEB_ENDPOINT_URL string = 'https://${web.outputs.fqdn}'
output SERVICE_WEB_IMAGE_NAME string = containerImage
output SERVICE_WEB_NAME string = web.outputs.name
output AZURE_OPENAI_ENDPOINT string = aiServices.outputs.endpoint
output FOUNDRY_PROJECT_ENDPOINT string = aiServices.outputs.projectEndpoint
output MODEL_DEPLOYMENTS array = aiServices.outputs.deploymentNames
output AZURE_STORAGE_ACCOUNT_NAME string = storage.outputs.accountName
output AZURE_OPENAI_REALTIME_ENDPOINT string = enableVoiceModels ? realtimeServices!.outputs.endpoint : ''
output VOICE_MODEL_DEPLOYMENTS array = enableVoiceModels ? realtimeServices!.outputs.deploymentNames : []
