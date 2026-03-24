// ---------------------------------------------------------------------------
// realtime-resource.bicep — Creates a dedicated Azure OpenAI account for
// voice models (Realtime S2S + TTS) in a region with available quota.
//
// Deployed separately from the main AI Services account because Realtime
// and TTS models may not have quota in the primary region.  The app
// uses AZURE_OPENAI_REALTIME_ENDPOINT to route voice traffic here.
//
// Models deployed:
//   • gpt-realtime       — Speech-to-speech evaluation (v1)
//   • gpt-realtime-1.5   — Speech-to-speech evaluation (v1.5)
//   • gpt-4o-mini-tts    — Text-to-speech for converting test cases to audio
// ---------------------------------------------------------------------------

@description('Name of the Azure OpenAI account (must be globally unique).')
param name string

@description('Azure region for the resource (e.g. eastus2).')
param location string

@description('Resource tags.')
param tags object = {}

@description('Voice model deployments. Each: { name, model, version, skuName?, capacity? }')
param deployments array = []

// ── Azure Cognitive Services account (kind: AIServices) ────────────────────
// Using kind: AIServices (the modern unified type) because the subscription
// may not have the legacy 'OpenAI' kind enabled.
resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' = {
  name: name
  location: location
  tags: tags
  kind: 'AIServices'
  sku: {
    name: 'S0'
  }
  properties: {
    customSubDomainName: name
    publicNetworkAccess: 'Enabled'
  }
}

// ── Voice model deployments ────────────────────────────────────────────────
@batchSize(1)
resource modelDeployment 'Microsoft.CognitiveServices/accounts/deployments@2025-06-01' = [
  for d in deployments: {
    parent: account
    name: d.name
    sku: {
      name: d.?skuName ?? 'GlobalStandard'
      capacity: d.?capacity ?? 100
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

// ── Outputs ────────────────────────────────────────────────────────────────
@description('Account name')
output accountName string = account.name

@description('Azure OpenAI endpoint (https://<name>.openai.azure.com/)')
output endpoint string = account.properties.endpoint

@description('Names of deployed voice models')
output deploymentNames array = [for (d, i) in deployments: modelDeployment[i].name]
