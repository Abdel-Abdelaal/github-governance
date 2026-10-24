terraform {
  required_version = "~> 1.16.0"
  required_providers {
    github = {
      source  = "integrations/github"
      version = "~> 6.13.0"
    }
    http = {
      source  = "hashicorp/http"
      version = "~> 3.5"
    }
  }
  cloud {
    organization = "abdel-abdelaal"
    workspaces {
      name = "github-governance"
    }
  }
}
