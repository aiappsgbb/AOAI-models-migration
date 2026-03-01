// ---------------------------------------------------------------------------
// main.bicep — Azure Developer CLI entry point
// Deploys: RG → Monitoring → ACR + CAE → Identity → AI Services hub → App
//
// Always creates all resources from scratch using a SINGLE AI Services
// account (kind: AIServices) which:
//   • Hosts all model deployments (gpt-4.1, gpt-5.2, gpt-5.1)
//   • Acts as AI Foundry hub with a project for evaluations
//   • Provides the OpenAI-compatible endpoint for the app
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

@secure()
@description('Flask session signing key (stored as Container Apps secret). Auto-generated if empty.')
param flaskSecretKey string = ''

// ── Derived names ──────────────────────────────────────────────────────────
var resourceSuffix = take(uniqueString(subscription().id, environmentName, location), 6)
var envNameLower = replace(toLower(environmentName), '_', '-')
var resourceGroupName = 'rg-${environmentName}'

// Single AI Services account — hosts ALL models + Foundry project
var aiServicesName = 'ais-${envNameLower}-${resourceSuffix}'
var foundryProjectName = '${envNameLower}-project'

// All model deployments live in the AI Services account
var modelDeployments = [
  { name: 'gpt-4.1', model: 'gpt-4.1', version: '2025-04-14', skuName: 'GlobalStandard', capacity: 10 }
  { name: 'gpt-5.2', model: 'gpt-5.2', version: '2025-12-11', skuName: 'GlobalStandard', capacity: 10 }
  { name: 'gpt-5.1', model: 'gpt-5.1', version: '2025-11-13', skuName: 'GlobalStandard', capacity: 10 }
]

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
]
var smtpEnv = empty(smtpHost) ? [] : [
  { name: 'SMTP_HOST', value: smtpHost }
  { name: 'SMTP_USERNAME', value: smtpUsername }
  { name: 'SMTP_PASSWORD', value: smtpPassword }
  { name: 'SMTP_FROM_EMAIL', value: smtpFromEmail }
]
var authEnv = empty(flaskSecretKey) ? [] : [
  { name: 'FLASK_SECRET_KEY', value: flaskSecretKey }
]
var containerEnv = concat(baseEnv, smtpEnv, authEnv)

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
