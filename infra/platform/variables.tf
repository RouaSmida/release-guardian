variable "subscription_id" {
  description = "Azure subscription ID. Pass it through TF_VAR_subscription_id; never commit it."
  type        = string
}

variable "location" {
  description = "Azure region for all resources."
  type        = string
  default     = "westeurope"
}

variable "prefix" {
  description = "Short name prefix for all resources (lowercase letters, digits and hyphens)."
  type        = string
  default     = "relguard"

  validation {
    condition     = can(regex("^[a-z][a-z0-9-]{2,11}$", var.prefix))
    error_message = "The prefix must be 3-12 characters: lowercase letters, digits and hyphens, starting with a letter."
  }
}

variable "environment" {
  description = "Environment name, used in resource names and tags."
  type        = string
  default     = "demo"
}

variable "kubernetes_version" {
  description = "AKS Kubernetes version. null uses the current AKS default."
  type        = string
  default     = null
}

variable "node_vm_size" {
  description = "VM size of the single node pool. Standard_B2s (2 vCPU, 4 GiB) is the cheapest size that runs the demo; use Standard_B2ms (8 GiB) for the full observability stack."
  type        = string
  default     = "Standard_B2s"
}

variable "node_count" {
  description = "Number of nodes. One node keeps the cost minimal (no high availability)."
  type        = number
  default     = 1

  validation {
    condition     = var.node_count >= 1 && var.node_count <= 3
    error_message = "The node count must be between 1 and 3 to keep the demo cost-conscious."
  }
}

variable "enable_container_insights" {
  description = "Send container logs and metrics to Log Analytics (ingestion is billed per GB)."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Log Analytics retention in days (30 is the minimum and is included in the price)."
  type        = number
  default     = 30
}

variable "log_daily_quota_gb" {
  description = "Daily ingestion cap for Log Analytics in GB, as a cost guard."
  type        = number
  default     = 0.5
}

variable "tags" {
  description = "Additional tags applied to every resource."
  type        = map(string)
  default     = {}
}
