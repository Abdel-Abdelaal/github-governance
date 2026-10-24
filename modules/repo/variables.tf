variable "name" {
  type = string
}

variable "settings" {
  type = object({
    description                     = string
    visibility                      = string
    has_wiki                        = bool
    has_projects                    = bool
    allow_squash_merge              = bool
    allow_merge_commit              = bool
    allow_rebase_merge              = bool
    delete_branch_on_merge          = bool
    allow_auto_merge                = bool
    allow_update_branch             = bool
    squash_merge_commit_title       = string
    squash_merge_commit_message     = string
    vulnerability_alerts            = bool
    secret_scanning                 = bool
    secret_scanning_push_protection = bool
    required_checks                 = list(string)
  })
}

variable "labels" {
  type = list(object({
    name        = string
    color       = string
    description = string
  }))
}
