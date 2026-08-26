# Contributing

## Issue workflow

GitHub Issues is the source of truth for planned work and progress. Before
starting a change:

1. Find an existing issue or create one with the appropriate issue form.
2. Make the goal, acceptance criteria, dependencies, and non-goals explicit.
3. Wait for or apply the appropriate triage label:
   - `needs-triage`: maintainer review is pending;
   - `needs-info`: the issue needs more information;
   - `ready-for-agent`: fully specified for autonomous implementation;
   - `ready-for-human`: requires human implementation or judgment;
   - `wontfix`: the repository will not pursue the issue.
4. Create a branch tied to the issue number, for example
   `feature/12-live-wpm`, `fix/18-pause-timing`, or
   `chore/24-update-tooling`.
5. Open a pull request whose body includes `Closes #<issue-number>`.

Keep decisions and progress that matter to the work on the GitHub issue or its
pull request. Local `.scratch/` files are temporary and are not project status.

## Verification

Run the backend checks from the repository root:

```bash
uv run --directory backend --locked pytest
uv run --directory backend --locked mypy app tests spikes
uv run --directory backend --locked ruff check app tests spikes
```
