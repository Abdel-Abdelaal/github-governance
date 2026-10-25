import {
  to = module.repo[".github"].github_repository.this
  id = ".github"
}

import {
  to = module.repo[".github"].github_repository_ruleset.main
  id = ".github:25798850"
}

import {
  to = module.repo[".github"].github_issue_labels.this
  id = ".github"
}

import {
  to = module.repo["platform-workflows"].github_repository.this
  id = "platform-workflows"
}

import {
  to = module.repo["platform-workflows"].github_repository_ruleset.main
  id = "platform-workflows:25798228"
}

import {
  to = module.repo["platform-workflows"].github_issue_labels.this
  id = "platform-workflows"
}

import {
  to = module.repo["shortlink"].github_repository.this
  id = "shortlink"
}

import {
  to = module.repo["shortlink"].github_repository_ruleset.main
  id = "shortlink:25798232"
}

import {
  to = module.repo["shortlink"].github_issue_labels.this
  id = "shortlink"
}

import {
  to = module.repo["github-governance"].github_repository.this
  id = "github-governance"
}

import {
  to = module.repo["github-governance"].github_repository_ruleset.main
  id = "github-governance:25799136"
}

import {
  to = module.repo["github-governance"].github_issue_labels.this
  id = "github-governance"
}

# ello and youtube-knowledge-distillation were enrolled after the bootstrap apply.
# Their protect-main rulesets were created on 2026-10-25 with the module's exact
# rules, so a reviewed apply adopts them instead of creating duplicates.
import {
  to = module.repo["ello"].github_repository.this
  id = "ello"
}

import {
  to = module.repo["ello"].github_repository_ruleset.main
  id = "ello:25834725"
}

import {
  to = module.repo["ello"].github_issue_labels.this
  id = "ello"
}

import {
  to = module.repo["youtube-knowledge-distillation"].github_repository.this
  id = "youtube-knowledge-distillation"
}

import {
  to = module.repo["youtube-knowledge-distillation"].github_repository_ruleset.main
  id = "youtube-knowledge-distillation:25834726"
}

import {
  to = module.repo["youtube-knowledge-distillation"].github_issue_labels.this
  id = "youtube-knowledge-distillation"
}

# agent-dispatcher was created from the fleet starter on 2026-10-25 and its
# protect-main ruleset bootstrapped with the module's rules, like ello above.
import {
  to = module.repo["agent-dispatcher"].github_repository.this
  id = "agent-dispatcher"
}

import {
  to = module.repo["agent-dispatcher"].github_repository_ruleset.main
  id = "agent-dispatcher:25836122"
}

import {
  to = module.repo["agent-dispatcher"].github_issue_labels.this
  id = "agent-dispatcher"
}
