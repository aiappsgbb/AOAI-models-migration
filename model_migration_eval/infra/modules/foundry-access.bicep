// ---------------------------------------------------------------------------
// foundry-access.bicep — Grants a managed identity the roles needed for
// Azure OpenAI inference AND Foundry evaluations on an AI Services account.
//
// Roles on the AI Services account:
//   1. "Cognitive Services OpenAI Contributor" — model deployments + data-plane
//   2. "Cognitive Services OpenAI User"        — chat/completions inference
//   3. "Azure AI Developer"                    — Foundry project operations
//   4. "Azure AI User"                         — Foundry asset store access
//
// Role on the resource group:
//   5. "Storage Blob Data Contributor"          — dataset uploads to backing storage
//
// AI Foundry projects are child resources of Cognitive Services accounts:
//   Microsoft.CognitiveServices/accounts/{accountName}/projects/{projectName}
// ---------------------------------------------------------------------------

@description('Name of the parent Cognitive Services (AI Services) account.')
param accountName string

@description('Name of the AI Foundry project (child of the account). Reserved for future per-project roles.')
#disable-next-line no-unused-params
param projectName string

@description('Principal ID of the managed identity to grant access to.')
param principalId string

// Cognitive Services OpenAI Contributor — model management + data-plane write
var csOpenAIContributorRoleId = 'a001fd3d-188f-4b5d-821b-7da978bf7442'

// Cognitive Services OpenAI User — chat/completions inference
var csOpenAIUserRoleId = '5e0bd9bd-7b93-4f28-af87-19fc36ad61bd'

// Azure AI Developer — Foundry project-level operations
var aiDeveloperRoleId = '64702f94-c441-49e6-a78b-ef80e0188fee'

// Azure AI User — Foundry asset store read/browse access
var aiUserRoleId = '53ca6127-db72-4b80-b1b0-d745d6d5456d'

// Storage Blob Data Contributor — upload evaluation JSONL datasets
var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource account 'Microsoft.CognitiveServices/accounts@2025-06-01' existing = {
  name: accountName
}

// ── Roles on the AI Services account ───────────────────────────────────────

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

resource aiDeveloperAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, aiDeveloperRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiDeveloperRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

resource aiUserAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, aiUserRoleId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUserRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// ── Storage role on the resource group ─────────────────────────────────────

resource storageBlobAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, storageBlobDataContributorRoleId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
