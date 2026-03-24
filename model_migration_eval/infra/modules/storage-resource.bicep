// ---------------------------------------------------------------------------
// storage-resource.bicep — Storage Account + Blob container for user data
//
// Creates a GPv2 Storage Account with:
//   • publicNetworkAccess: Enabled  (required — Container Apps uses HTTPS)
//   • A blob container "userdata" for persisting user files and auth.db
//   • RBAC: Storage Blob Data Contributor for the app's managed identity
//   • RBAC: Storage Blob Data Contributor for the deployer (CLI management)
//
// Authentication is via Managed Identity / Entra ID (DefaultAzureCredential)
// — no shared key access needed, compatible with corporate policies.
// ---------------------------------------------------------------------------

@description('Name of the storage account (must be globally unique, 3-24 chars, lowercase + numbers only).')
param name string

@description('Azure region for the storage account.')
param location string = resourceGroup().location

@description('Resource tags.')
param tags object = {}

@description('Principal ID of the managed identity to grant blob access.')
param principalId string

@description('Principal ID of the deployer identity (user or SP running azd up) for CLI management. Leave empty to skip.')
param deployerPrincipalId string = ''

@description('Name of the blob container for user data.')
param containerName string = 'userdata'

// ── Storage Account ────────────────────────────────────────────────────────

resource storageAccount 'Microsoft.Storage/storageAccounts@2023-05-01' = {
  name: name
  location: location
  tags: tags
  kind: 'StorageV2'
  sku: {
    name: 'Standard_LRS'
  }
  properties: {
    accessTier: 'Hot'
    publicNetworkAccess: 'Enabled'   // Required — Container Apps uses public HTTPS endpoint
    supportsHttpsTrafficOnly: true
    minimumTlsVersion: 'TLS1_2'
    allowBlobPublicAccess: false
    allowSharedKeyAccess: false   // Entra-only auth — corporate policy safe
    networkAcls: {
      defaultAction: 'Allow'      // Container Apps outbound HTTPS is allowed
      bypass: 'AzureServices'
    }
  }
}

// ── Blob Service + Container ───────────────────────────────────────────────

resource blobService 'Microsoft.Storage/storageAccounts/blobServices@2023-05-01' = {
  parent: storageAccount
  name: 'default'
}

resource blobContainer 'Microsoft.Storage/storageAccounts/blobServices/containers@2023-05-01' = {
  parent: blobService
  name: containerName
  properties: {
    publicAccess: 'None'
  }
}

// ── RBAC: Storage Blob Data Contributor ────────────────────────────────────

var storageBlobDataContributorRoleId = 'ba92f5b4-2d11-453d-a403-e96b0029c9fe'

resource blobRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = {
  name: guid(storageAccount.id, principalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: principalId
    principalType: 'ServicePrincipal'
  }
}

// ── RBAC: Deployer (human operator running azd / CLI) ──────────────────────
// Allows the person who deploys to inspect and manage blobs from CLI/Portal.

resource deployerBlobRoleAssignment 'Microsoft.Authorization/roleAssignments@2022-04-01' = if (!empty(deployerPrincipalId)) {
  name: guid(storageAccount.id, deployerPrincipalId, storageBlobDataContributorRoleId)
  scope: storageAccount
  properties: {
    roleDefinitionId: subscriptionResourceId('Microsoft.Authorization/roleDefinitions', storageBlobDataContributorRoleId)
    principalId: deployerPrincipalId
    principalType: 'User'
  }
}

// ── Outputs ────────────────────────────────────────────────────────────────

output accountName string = storageAccount.name
output accountId string = storageAccount.id
