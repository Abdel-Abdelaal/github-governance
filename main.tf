locals {
  inventory = yamldecode(file("${path.module}/repos.yaml"))
  repos = {
    for name, settings in local.inventory.repos : name => merge(local.inventory.defaults, settings)
  }
  labels = yamldecode(data.http.labels.response_body)
}

# A reviewed revision avoids changing Terraform input behind a PR's back.
data "http" "labels" {
  url = "https://raw.githubusercontent.com/Abdel-Abdelaal/.github/847149b510b01874fb505cbb4688149ecf9cb62d/.github/labels.yml"
  request_headers = {
    Accept = "text/plain"
  }
}

module "repo" {
  for_each = local.repos
  source   = "./modules/repo"
  name     = each.key
  settings = each.value
  labels   = local.labels
}
