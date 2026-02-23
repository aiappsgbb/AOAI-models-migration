// ---------------------------------------------------------------------------
// openai-access.bicep — Grants a managed identity the
// "Cognitive Services OpenAI User" role on an existing Azure OpenAI account.
// Deployed to the resource group where the account lives.
// ---------------------------------------------------------------------------

@description('Name of the existing Cognitive Services account.')
param accountName string

@description('Principal ID of the managed identity to grant access to.')
param principalId string

// Cognitive Services OpenAI User
var roleDefinitionId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: accountName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(openAiAccount.id, principalId, roleDefinitionId)
  scope: openAiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
