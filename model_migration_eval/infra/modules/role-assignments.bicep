// ---------------------------------------------------------------------------
// role-assignments.bicep — Assigns RBAC roles to a managed identity for
// Azure OpenAI and AI Foundry access (Entra ID / keyless auth)
// ---------------------------------------------------------------------------

@description('Principal ID of the managed identity to grant roles to.')
param principalId string

@description('Resource ID of the Azure OpenAI (Cognitive Services) account. Leave empty to skip.')
param azureOpenAiAccountResourceId string = ''

@description('Resource ID of the AI Foundry project. Leave empty to skip.')
param aiFoundryProjectResourceId string = ''

// ── Built-in role definition IDs ───────────────────────────────────────────
// Cognitive Services OpenAI User — call models (completions, embeddings, etc.)
var cognitiveServicesOpenAiUserId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

// Azure AI Developer — create evaluations, upload datasets, manage projects
var azureAiDeveloperId = '64702f94-c441-49e6-a78b-ef80e0188fee'

// ── Azure OpenAI role assignment ───────────────────────────────────────────
resource openAiAccount 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = if (!empty(azureOpenAiAccountResourceId)) {
  name: last(split(azureOpenAiAccountResourceId, '/'))
}

resource openAiRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(azureOpenAiAccountResourceId)) {
  name: guid(openAiAccount.id, principalId, cognitiveServicesOpenAiUserId)
  scope: openAiAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', cognitiveServicesOpenAiUserId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// ── AI Foundry project role assignment ─────────────────────────────────────
resource aiFoundryProject 'Microsoft.MachineLearningServices/workspaces@2023-06-01-preview' existing = if (!empty(aiFoundryProjectResourceId)) {
  name: last(split(aiFoundryProjectResourceId, '/'))
}

resource aiDeveloperRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(aiFoundryProjectResourceId)) {
  name: guid(aiFoundryProject.id, principalId, azureAiDeveloperId)
  scope: aiFoundryProject
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', azureAiDeveloperId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
