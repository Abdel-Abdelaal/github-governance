resource "github_repository" "this" {
  name                        = var.name
  description                 = var.settings.description
  visibility                  = var.settings.visibility
  has_issues                  = true
  has_wiki                    = var.settings.has_wiki
  has_projects                = var.settings.has_projects
  allow_squash_merge          = var.settings.allow_squash_merge
  allow_merge_commit          = var.settings.allow_merge_commit
  allow_rebase_merge          = var.settings.allow_rebase_merge
  delete_branch_on_merge      = var.settings.delete_branch_on_merge
  allow_auto_merge            = var.settings.allow_auto_merge
  allow_update_branch         = var.settings.allow_update_branch
  squash_merge_commit_title   = var.settings.squash_merge_commit_title
  squash_merge_commit_message = var.settings.squash_merge_commit_message
  vulnerability_alerts        = var.settings.vulnerability_alerts
  archive_on_destroy          = true

  security_and_analysis {
    secret_scanning {
      status = var.settings.secret_scanning ? "enabled" : "disabled"
    }
    secret_scanning_push_protection {
      status = var.settings.secret_scanning_push_protection ? "enabled" : "disabled"
    }
  }

  lifecycle {
    prevent_destroy = true
  }
}

resource "github_repository_ruleset" "main" {
  name        = "protect-main"
  repository  = github_repository.this.name
  target      = "branch"
  enforcement = "active"
  conditions {
    ref_name {
      include = ["~DEFAULT_BRANCH"]
      exclude = []
    }
  }
  rules {
    deletion                = true
    non_fast_forward        = true
    required_linear_history = true
    pull_request {
      required_approving_review_count   = 0
      dismiss_stale_reviews_on_push     = true
      require_code_owner_review         = false
      require_last_push_approval        = false
      required_review_thread_resolution = true
      allowed_merge_methods             = ["squash"]
    }
    required_status_checks {
      strict_required_status_checks_policy = true
      do_not_enforce_on_create             = false
      dynamic "required_check" {
        for_each = var.settings.required_checks
        content {
          context        = required_check.value
          integration_id = 15368
        }
      }
    }
  }
}

resource "github_issue_labels" "this" {
  repository = github_repository.this.name
  dynamic "label" {
    for_each = { for label in var.labels : label.name => label }
    content {
      name        = label.value.name
      color       = label.value.color
      description = label.value.description
    }
  }
}

resource "github_repository_dependabot_security_updates" "this" {
  repository = github_repository.this.name
  enabled    = true
}
