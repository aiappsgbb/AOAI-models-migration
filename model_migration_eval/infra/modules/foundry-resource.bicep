// ---------------------------------------------------------------------------
// foundry-resource.bicep — Creates an AI Services account with
// allowProjectManagement enabled + a Foundry project inside it.
//
// The AI Services account can also host model deployments (gpt-4.1 etc.)
// used as judge/grader for Foundry evaluations.
// ---------------------------------------------------------------------------

@description('Name of the AI Services account (must be globally unique).')
param name string

@description('Name of the AI Foundry project.')
param projectName string

@description('Azure region for the resource.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Model deployments for the judge/grader. Each: { name, model, skuName?, capacity? }')
param deployments array = []

// ── AI Services account (hub) ──────────────────────────────────────────────
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  identity: {
    type: 'SystemAssigned'
  }
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
    allowProjectManagement: true
  }
}

// ── Model deployments (judge/grader models for evaluations) ────────────────
@batchSize(1)
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: {
      name: d.?skuName ?? 'GlobalStandard'
      capacity: d.?capacity ?? 10
    }
    properties: {
      model: {
        format: 'OpenAI'
        name: d.model
        version: d.version
      }
    }
  }
]

// ── Foundry project ────────────────────────────────────────────────────────
// Note: 'kind' and 'sku' are valid for API 2025-06-01 but Bicep types
// haven't caught up yet — we suppress the false warnings.
#disable-next-line BCP037
resource project 'Microsoft.CognitiveServices/accounts/projects@2025-06-01' = {
  parent: account
  name: projectName
  location: location
  tags: tags
  #disable-next-line BCP037
  kind: 'project'
  #disable-next-line BCP037
  identity: {
    type: 'SystemAssigned'
  }
  #disable-next-line BCP037
  sku: {
    name: 'S0'
  }
  properties: {}
  dependsOn: [modelDeployment]
}

// ── Outputs ────────────────────────────────────────────────────────────────
@description('AI Services account name')
output accountName string = account.name

@description('Foundry project name')
output projectName string = project.name

@description('Foundry project endpoint (https://<account>.services.ai.azure.com/api/projects/<project>)')
output projectEndpoint string = 'https://${name}.services.ai.azure.com/api/projects/${projectName}'

@description('AI Services endpoint')
output endpoint string = account.properties.endpoint
