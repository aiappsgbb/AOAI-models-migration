// ---------------------------------------------------------------------------
// foundry-access.bicep — Grants a managed identity the
// "Azure AI Developer" role on an existing AI Foundry project (ML workspace).
// Deployed to the resource group where the workspace lives.
// ---------------------------------------------------------------------------

@description('Name of the existing ML workspace (AI Foundry project).')
param workspaceName string

@description('Principal ID of the managed identity to grant access to.')
param principalId string

// Azure AI Developer
var roleDefinitionId = '64702f94-c441-49e6-a78b-ef80e0188fee'

resource aiFoundryProject 'Microsoft.MachineLearningServices/workspaces@2023-06-01-preview' existing = {
  name: workspaceName
}

resource roleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(aiFoundryProject.id, principalId, roleDefinitionId)
  scope: aiFoundryProject
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', roleDefinitionId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}
