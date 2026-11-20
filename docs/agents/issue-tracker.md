# GitHub Issues

Project specifications and implementation tickets live in GitHub Issues for `zhangzhefang-github/data-agent`. Use `gh` from the repository root.

## Issue operations

- Read an issue with `gh issue view <number> --json number,title,body,labels,comments`.
- List issues with `gh issue list --state open --json number,title,body,labels`. Add `--label` to filter by role.
- Publish an issue or spec with `gh issue create --title "<title>" --body-file <file>`. Use the configured triage label when the skill requests one.
- Link an implementation ticket to its parent with `gh api --method POST repos/{owner}/{repo}/issues/<parent>/sub_issues -F sub_issue_id=<child-db-id>`. If sub-issues are unavailable, put `Part of #<parent>` at the top of the child body.
- Record a blocking dependency with `gh api --method POST repos/{owner}/{repo}/issues/<child>/dependencies/blocked_by -F issue_id=<blocker-db-id>`. If native dependencies are unavailable, list the blocking issue numbers under a **Blocked by** heading.
- Resolve a database ID with `gh api repos/{owner}/{repo}/issues/<number> --jq .id`. Use the returned numeric ID in the two link operations above.
- Comment with `gh issue comment <number> --body-file <file>`. Change labels with `gh issue edit <number> --add-label "<label>"` or `--remove-label`. Close with `gh issue close <number>`.

## Triage surface

PRs as a request surface: no.

## Wayfinding operations

- Publish the decision map with `wayfinder:map`. Link decision tickets as sub-issues and label each `wayfinder:<type>`.
- Find available tickets among open, unassigned children whose `issue_dependencies_summary.blocked_by` is zero. With textual dependencies, check that every listed blocker is closed.
- Claim a ticket with `gh issue edit <number> --add-assignee @me`. On resolution, post the decision, close the ticket, and link the resolution from the map.
