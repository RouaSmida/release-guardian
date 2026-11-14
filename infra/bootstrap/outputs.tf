output "state_resource_group_name" {
  description = "Resource group of the Terraform state (backend setting resource_group_name)."
  value       = azurerm_resource_group.state.name
}

output "state_storage_account_name" {
  description = "Storage account of the Terraform state (backend setting storage_account_name)."
  value       = azurerm_storage_account.state.name
}

output "state_container_name" {
  description = "Blob container of the Terraform state (backend setting container_name)."
  value       = azurerm_storage_container.state.name
}

output "github_client_id" {
  description = "Set as the GitHub Actions variable AZURE_CLIENT_ID."
  value       = azurerm_user_assigned_identity.github.client_id
}

output "github_tenant_id" {
  description = "Set as the GitHub Actions variable AZURE_TENANT_ID."
  value       = azurerm_user_assigned_identity.github.tenant_id
}
