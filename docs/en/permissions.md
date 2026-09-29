<div align="right">English（this page） | [简体中文](../zh/permissions.md)</div>

# Permissions: four roles and "my way of running"

| Capability | owner | admin | member | viewer |
|---|---|---|---|---|
| Browse banks & public run history | yes | yes | yes | yes |
| Launch runs / view own runs | yes | yes | yes | no |
| View everyone's runs | yes | yes | no | no |
| Create private banks | yes | yes | yes | no |
| Create/manage public banks | yes | yes | no | no |
| User management | yes | yes (not owner) | no | no |
| Platform settings | yes | no | no | no |
| Change owner password | deploy-machine CLI only | no | no | no |

Accounts: owner is bootstrapped at first deploy (password changeable only via the deploy
machine: `docker exec agentgate-web agentgate passwd owner`); admins create members in the
User Center; viewers can self-register (toggle in Platform Settings). User Center also holds
per-user AI-enhancement settings.

On public banks every member/admin keeps a personal config ("my way of running", on the bank
detail page): per-case level override and enable/disable, plus one-click restore-to-defaults.
At launch, personal config applies first, then the run's level filter. Private banks have no
such layer — creators edit cases directly.

Personal config only affects who runs what and at which level; gold and judging rules are
shared. Per-user run history appears in the bank overview for comparison.
