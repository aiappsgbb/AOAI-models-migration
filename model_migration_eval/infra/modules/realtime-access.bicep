// ---------------------------------------------------------------------------
// realtime-access.bicep — Grants a managed identity the roles needed for
// TTS and Realtime (speech-to-speech) on a SEPARATE Cognitive Services
// account used as the dedicated voice endpoint.
//
// The TTS audio/speech API requires "Cognitive Services OpenAI Contributor"
// (which includes the Microsoft.CognitiveServices/accounts/OpenAI/
// deployments/audio/action data action). The Realtime WebSocket API
// requires at least "Cognitive Services OpenAI User".
//
// Both roles are assigned here so the managed identity can:
//   1. Synthesise text → audio via TTS (audio/speech endpoint)
//   2. Open Realtime WebSocket sessions (realtime endpoint)
//
// This module is only deployed when a dedicated realtime endpoint is
// configured (i.e. realtimeAccountName is not empty).
// ---------------------------------------------------------------------------

@description('Name of the external Cognitive Services account hosting TTS + Realtime models.')
param accountName string

@description('Principal ID of the managed identity to grant access to.')
param principalId string

// Cognitive Services OpenAI Contributor — includes audio/action for TTS
var csOpenAIContributorRoleId = 'a001fd3d-188f-4b5d-821b-7da978bf7442'

// Cognitive Services OpenAI User — chat/completions + Realtime WebSocket
var csOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

resource csOpenAIContributorAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, csOpenAIContributorRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', csOpenAIContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource csOpenAIUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, csOpenAIUserRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', csOpenAIUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
