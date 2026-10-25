locals {
  inventory = yamldecode(file("${path.module}/repos.yaml"))
  repos = {
    for name, settings in local.inventory.repos : name => merge(local.inventory.defaults, settings)
  }
  labels = yamldecode(data.http.labels.response_body)
}

# A reviewed revision avoids changing Terraform input behind a PR's back.
data "http" "labels" {
  url = "https://raw.githubusercontent.com/Abdel-Abdelaal/.github/3f91a23103c44b27c58f493dfd799e6a442c7056/.github/labels.yml"
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
