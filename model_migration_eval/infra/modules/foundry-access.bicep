// ---------------------------------------------------------------------------
// foundry-access.bicep — Grants a managed identity the
// "Azure AI Developer" role on an existing AI Foundry project and
// "Storage Blob Data Contributor" on the resource group (for dataset uploads).
// Deployed to the resource group where the Cognitive Services account lives.
//
// AI Foundry projects are child resources of Cognitive Services accounts:
//   Microsoft.CognitiveServices/accounts/{accountName}/projects/{projectName}
// ---------------------------------------------------------------------------

@description('Name of the parent Cognitive Services (AI Services) account.')
param accountName string

@description('Name of the AI Foundry project (child of the account).')
param projectName string

@description('Principal ID of the managed identity to grant access to.')
param principalId string

// Azure AI Developer
var aiDeveloperRoleDefinitionId = '64702f94-c441-49e6-a78b-ef80e0188fee'

// Azure AI User — minimum role for Foundry data-plane actions
// (includes Microsoft.CognitiveServices/accounts/AIServices/agents/write
//  required by the Foundry Evals API).
var aiUserRoleDefinitionId = '53ca6127-db72-4b80-b1b0-d745d6d5456b'

// Storage Blob Data Contributor — needed to upload evaluation JSONL datasets
// to the Foundry project's backing storage account.
var storageBlobDataContributorRoleDefinitionId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource account 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2024-10-01' existing = {
  parent: account
  name: projectName
}

resource aiDeveloperRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, aiDeveloperRoleDefinitionId)
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiDeveloperRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Azure AI User on the Cognitive Services account — required for
// Foundry Evals API data-plane calls (agents/write, etc.).
resource aiUserRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(account.id, principalId, aiUserRoleDefinitionId)
  scope: account
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', aiUserRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// Assigned at the resource group scope so the identity can upload datasets
// to whichever storage account backs the Foundry project.
resource storageBlobRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(resourceGroup().id, principalId, storageBlobDataContributorRoleDefinitionId)
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
