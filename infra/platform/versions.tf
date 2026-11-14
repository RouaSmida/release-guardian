terraform {
  required_version = ">= 1.9.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.9"
    }
  }

  # Remote state in the storage account created by infra/bootstrap.
  # The account details are passed at init time (see backend.hcl.example),
  # so no environment-specific value is committed.
  backend "azurerm" {
    key              = "platform.tfstate"
    use_azuread_auth = true
  }
}

provider "azurerm" {
  subscription_id = var.subscription_id

  features {}
}
