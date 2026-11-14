# Bootstrap: the few resources that must exist before Terraform can manage the
# platform from CI. Applied once, by a human, with local state.
#
#   1. A storage account that holds the remote Terraform state of infra/platform.
#   2. A managed identity that GitHub Actions can use through OIDC federation,
#      so no client secret is ever stored in GitHub.

locals {
  tags = merge(
    {
      project    = "release-guardian"
      managed_by = "terraform"
      component  = "bootstrap"
    },
    var.tags,
  )

  # Tokens issued by GitHub for these subjects are accepted by Azure.
  github_subjects = {
    branch       = "repo:${var.github_repository}:ref:refs/heads/${var.github_branch}"
    pull-request = "repo:${var.github_repository}:pull_request"
  }
}

resource "random_string" "suffix" {
  length  = 6
  upper   = false
  special = false
}

resource "azurerm_resource_group" "state" {
  name     = "${var.prefix}-tfstate-rg"
  location = var.location
  tags     = local.tags
}

# ---------------------------------------------------------------- remote state

resource "azurerm_storage_account" "state" {
  name                            = "${replace(var.prefix, "-", "")}tf${random_string.suffix.result}"
  resource_group_name             = azurerm_resource_group.state.name
  location                        = azurerm_resource_group.state.location
  account_tier                    = "Standard"
  account_replication_type        = "LRS"
  min_tls_version                 = "TLS1_2"
  allow_nested_items_to_be_public = false
  tags                            = local.tags

  blob_properties {
    versioning_enabled = true

    delete_retention_policy {
      days = 7
    }
  }
}

resource "azurerm_storage_container" "state" {
  name                  = "tfstate"
  storage_account_id    = azurerm_storage_account.state.id
  container_access_type = "private"
}

# ------------------------------------------------------- GitHub Actions (OIDC)

resource "azurerm_user_assigned_identity" "github" {
  name                = "${var.prefix}-github-actions"
  resource_group_name = azurerm_resource_group.state.name
  location            = azurerm_resource_group.state.location
  tags                = local.tags
}

resource "azurerm_federated_identity_credential" "github" {
  for_each = local.github_subjects

  name                = "github-${each.key}"
  resource_group_name = azurerm_resource_group.state.name
  parent_id           = azurerm_user_assigned_identity.github.id
  audience            = ["api://AzureADTokenExchange"]
  issuer              = "https://token.actions.githubusercontent.com"
  subject             = each.value
}

# Contributor on the subscription lets CI create the platform resource group.
# To narrow this, create the resource group here and scope the role to it.
resource "azurerm_role_assignment" "github_contributor" {
  scope                = "/subscriptions/${var.subscription_id}"
  role_definition_name = "Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}

# Data-plane access to the state blobs (the backend uses Entra ID, not account keys).
resource "azurerm_role_assignment" "github_state" {
  scope                = azurerm_storage_account.state.id
  role_definition_name = "Storage Blob Data Contributor"
  principal_id         = azurerm_user_assigned_identity.github.principal_id
}
