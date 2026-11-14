variable "subscription_id" {
  description = "Azure subscription ID. Pass it on the command line or through TF_VAR_subscription_id; never commit it."
  type        = string
}

variable "location" {
  description = "Azure region for the state storage and the GitHub identity."
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

variable "github_repository" {
  description = "GitHub repository allowed to authenticate through OIDC, as owner/name."
  type        = string
  default     = "RouaSmida/release-guardian"
}

variable "github_branch" {
  description = "Branch whose workflows may authenticate (besides pull requests)."
  type        = string
  default     = "main"
}

variable "tags" {
  description = "Tags applied to every resource."
  type        = map(string)
  default     = {}
}
