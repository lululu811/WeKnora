# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

## Creating the labels

These five labels are not created automatically. If `gh issue edit --add-label` reports a missing label, create it once:

```bash
gh label create needs-triage    --repo lululu811/WeKnora --color F9D0C4 --description "Maintainer needs to evaluate this issue"
gh label create needs-info      --repo lululu811/WeKnora --color F9D0C4 --description "Waiting on reporter for more information"
gh label create ready-for-agent --repo lululu811/WeKnora --color B7F5BD --description "Fully specified, ready for an AFK agent"
gh label create ready-for-human --repo lululu811/WeKnora --color B7F5BD --description "Requires human implementation"
gh label create wontfix         --repo lululu811/WeKnora --color D4C5F9 --description "Will not be actioned"
```
