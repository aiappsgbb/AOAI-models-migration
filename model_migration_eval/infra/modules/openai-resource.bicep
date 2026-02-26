// ---------------------------------------------------------------------------
// openai-resource.bicep — Creates an Azure OpenAI (Cognitive Services)
// account with model deployments.
//
// Used when the user opts to create new resources via the preprovision hook
// instead of pointing to an existing Azure OpenAI endpoint.
// ---------------------------------------------------------------------------

@description('Name of the Azure OpenAI account (must be globally unique).')
param name string

@description('Azure region for the resource.')
param location string

@description('Resource tags.')
param tags object = {}

@description('Model deployments. Each object: { name, model, skuName?, capacity? }')
param deployments array = []

resource account 'Microsoft.CognitiveServices/accounts@2024-10-01' = {
  name: name
  location: location
  tags: tags
  kind: 'OpenAI'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
  }
}

@batchSize(1)
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2024-10-01' = [
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
      }
    }
  }
]

@description('Full endpoint URL (e.g. https://name.openai.azure.com/)')
output endpoint string = account.properties.endpoint

@description('Account name')
output accountName string = account.name

@description('Full resource ID of the account')
output accountId string = account.id
