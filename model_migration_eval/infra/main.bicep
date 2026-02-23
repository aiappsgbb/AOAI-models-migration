// ---------------------------------------------------------------------------
// main.bicep — Azure Developer CLI entry point (AVM pattern modules)
// Deploys: Resource Group → Monitoring → ACR + CAE (Stack) → Identity → App
// ---------------------------------------------------------------------------

targetScope = 'subscription'

// ── Parameters (populated by azd) ──────────────────────────────────────────
@minLength(1)
@maxLength(64)
@description('Name of the azd environment — used to derive all resource names')
param environmentName string

@minLength(1)
@description('Primary Azure region for all resources')
param location string

@description('ID of the principal running the deployment (used for RBAC)')
param principalId string = ''

// ── Azure OpenAI / AI Foundry settings ─────────────────────────────────────
// Authentication uses the User-Assigned Managed Identity (Entra ID / keyless).
// No API keys or Service Principal credentials needed.
@description('Azure OpenAI endpoint URL (e.g. https://<name>.openai.azure.com)')
param azureOpenAiEndpoint string = ''

@description('AI Foundry project endpoint (optional)')
param foundryProjectEndpoint string = ''

// ── RBAC: resource IDs for role assignments (optional) ─────────────────────
// Provide these so Bicep assigns Cognitive Services OpenAI User and
// Azure AI Developer roles to the managed identity automatically.
@description('Resource ID of the Azure OpenAI (Cognitive Services) account for RBAC. Leave empty to assign roles manually.')
param azureOpenAiAccountResourceId string = ''

@description('Full resource ID of the AI Foundry project (Microsoft.CognitiveServices/accounts/{account}/projects/{project}) for RBAC. Leave empty to assign roles manually.')
param aiFoundryProjectResourceId string = ''

@description('Container image name for the web service. Set automatically by azd after deploy to prevent image reset on re-provision.')
param webImageName string = ''

// ── Derived names ──────────────────────────────────────────────────────────
var resourceSuffix = take(uniqueString(subscription().id, environmentName, location), 6)
var envNameLower = toLower(environmentName)
var resourceGroupName = 'rg-${environmentName}'

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

// ── Container Apps Stack — ACR + Container Apps Environment (AVM) ──────────
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

// ── User-Assigned Managed Identity for web (ACR pull + Entra ID auth) ──────
module webIdentity 'br/public:avm/res/managed-identity/user-assigned-identity:0.4.0' = {
  name: 'web-identity'
  scope: rg
  params: {
    name: 'id-web-${envNameLower}-${resourceSuffix}'
    location: location
    tags: tags
  }
}

// ── ACR Pull Role Assignment ───────────────────────────────────────────────
module acrAccess './modules/acr-access.bicep' = {
  name: 'acr-access'
  scope: rg
  params: {
    containerRegistryName: containerApps.outputs.registryName
    principalId: webIdentity.outputs.principalId
  }
}

// ── RBAC: Azure OpenAI + AI Foundry role assignments ───────────────────────
// Grants the managed identity access to call models and run evaluations,
// replacing the need for API keys or Service Principal credentials.
// Each module is deployed to the resource group where the target resource
// lives (which may differ from the deployment RG).

// Safe placeholders so split() always has enough segments when ID is empty
var _openAiId = !empty(azureOpenAiAccountResourceId)
  ? azureOpenAiAccountResourceId
  : '/subscriptions/x/resourceGroups/x/providers/x/x/x'
// Foundry project IDs have the form:
// /subscriptions/{sub}/resourceGroups/{rg}/providers/Microsoft.CognitiveServices/accounts/{account}/projects/{project}
var _foundryId = !empty(aiFoundryProjectResourceId)
  ? aiFoundryProjectResourceId
  : '/subscriptions/x/resourceGroups/x/providers/Microsoft.CognitiveServices/accounts/x/projects/x'

module openAiAccess './modules/openai-access.bicep' = if (!empty(azureOpenAiAccountResourceId)) {
  name: 'openai-access'
  scope: resourceGroup(split(_openAiId, '/')[4])
  params: {
    accountName: last(split(_openAiId, '/'))
    principalId: webIdentity.outputs.principalId
  }
}

module foundryAccess './modules/foundry-access.bicep' = if (!empty(aiFoundryProjectResourceId)) {
  name: 'foundry-access'
  scope: resourceGroup(split(_foundryId, '/')[4])
  params: {
    accountName: split(_foundryId, '/')[8]   // CognitiveServices account name
    projectName: last(split(_foundryId, '/')) // project name
    principalId: webIdentity.outputs.principalId
  }
}

// ── Container App — the web service (AVM resource module) ──────────────────
// Uses avm/res/app/container-app directly for full control over probes,
// scale rules, ingress security, and scale-to-zero — features that the
// azd pattern modules (container-app-upsert / acr-container-app) don't expose.
var containerAppName = take('ca-${envNameLower}-${resourceSuffix}', 32)
var containerImage = !empty(webImageName)
  ? webImageName
  : 'mcr.microsoft.com/azuredocs/containerapps-helloworld:latest'

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

    // ── Ingress ────────────────────────────────────────────────────────────
    ingressExternal: true
    ingressTargetPort: 5000
    ingressTransport: 'auto'
    ingressAllowInsecure: false          // redirect HTTP → HTTPS

    // ── Scale (matches deploy.ps1) ─────────────────────────────────────────
    scaleMinReplicas: 0                  // scale-to-zero for cost savings
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

    // ── Container definition (with probes from deploy.ps1) ─────────────────
    // No secrets needed — authentication uses the User-Assigned Managed Identity.
    // DefaultAzureCredential picks up the identity via AZURE_CLIENT_ID.
    containers: [
      {
        image: containerImage
        name: 'main'
        env: [
          { name: 'PYTHONUNBUFFERED', value: '1' }
          { name: 'AZURE_OPENAI_ENDPOINT', value: azureOpenAiEndpoint }
          { name: 'FOUNDRY_PROJECT_ENDPOINT', value: foundryProjectEndpoint }
          { name: 'AZURE_CLIENT_ID', value: webIdentity.outputs.clientId }
          { name: 'APPLICATIONINSIGHTS_CONNECTION_STRING', value: monitoring.outputs.applicationInsightsConnectionString }
        ]
        resources: {
          cpu: json('1.0')
          memory: '2.0Gi'
        }
        probes: [
          {
            type: 'Liveness'
            httpGet: {
              path: '/api/health'
              port: 5000
            }
            initialDelaySeconds: 10
            periodSeconds: 30
          }
          {
            type: 'Readiness'
            httpGet: {
              path: '/api/health'
              port: 5000
            }
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
