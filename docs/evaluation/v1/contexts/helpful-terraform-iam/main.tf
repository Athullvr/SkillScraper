# The concrete starting IAM permissions and cross-account trust policy were not
# supplied for this fixture. No permissions, account IDs, or trust rules are
# invented here.

variable "starting_role_policy_json" {
  type = string
}

variable "starting_trust_policy_json" {
  type = string
}
