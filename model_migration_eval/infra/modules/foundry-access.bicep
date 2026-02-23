// ---------------------------------------------------------------------------
// foundry-access.bicep — Grants a managed identity the
// "Azure AI Developer" role on an existing AI Foundry project.
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
var roleDefinitionId = '64702f94-c441-49e6-a78b-ef80e0188fee'

resource account 'Microsoft.CognitiveServices/accounts@2023-05-01' existing = {
  name: accountName
}

resource project 'Microsoft.CognitiveServices/accounts/projects@2024-10-01' existing = {
  parent: account
  name: projectName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(project.id, principalId, roleDefinitionId)
  scope: project
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
