# Platform: resource group, Log Analytics and a cost-conscious AKS cluster.
#
# COST WARNING: applying this creates billable Azure resources (one VM, a load
# balancer, a public IP, disks, log ingestion). Run `terraform destroy` when
# you are done. See docs/RUNBOOK.md.

locals {
  name = "${var.prefix}-${var.environment}"

  tags = merge(
    {
      project     = "release-guardian"
      environment = var.environment
      managed_by  = "terraform"
    },
    var.tags,
  )
}

resource "azurerm_resource_group" "main" {
  name     = "${local.name}-rg"
  location = var.location
  tags     = local.tags
}

resource "azurerm_log_analytics_workspace" "main" {
  name                = "${local.name}-logs"
  resource_group_name = azurerm_resource_group.main.name
  location            = azurerm_resource_group.main.location
  sku                 = "PerGB2018"
  retention_in_days   = var.log_retention_days
  daily_quota_gb      = var.log_daily_quota_gb
  tags                = local.tags
}

resource "azurerm_kubernetes_cluster" "main" {
  name                      = "${local.name}-aks"
  resource_group_name       = azurerm_resource_group.main.name
  location                  = azurerm_resource_group.main.location
  dns_prefix                = local.name
  kubernetes_version        = var.kubernetes_version
  sku_tier                  = "Free"
  oidc_issuer_enabled       = true
  workload_identity_enabled = true
  tags                      = local.tags

  default_node_pool {
    name            = "system"
    vm_size         = var.node_vm_size
    node_count      = var.node_count
    os_disk_size_gb = 32
    tags            = local.tags
  }

  # The cluster manages its own Azure resources with a managed identity:
  # no service principal secret to store or rotate.
  identity {
    type = "SystemAssigned"
  }

  network_profile {
    network_plugin      = "azure"
    network_plugin_mode = "overlay"
    load_balancer_sku   = "standard"
  }

  dynamic "oms_agent" {
    for_each = var.enable_container_insights ? [1] : []

    content {
      log_analytics_workspace_id      = azurerm_log_analytics_workspace.main.id
      msi_auth_for_monitoring_enabled = true
    }
  }
}
