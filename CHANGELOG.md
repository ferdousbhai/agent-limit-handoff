# Changelog

## 0.1.1

- Share installer logic for adding hooks without duplicates.
- Reuse fallback JSON handling and load installer fallback settings only when needed.
- Simplify handoff phase checks and derive prompt percentages from threshold constants.
- Remove redundant filesystem reads and test helpers.
- Keep Omarchy collectors and existing quota thresholds unchanged.

## 0.1.0

- Add per-task weekly quota handoffs for Codex, Claude Code, and Grok Build.
- Prepare drafts at 95% and request final handoffs at 99%.
- Add bounded retries, emergency recovery notes, and atomic session state.
- Preserve existing hooks and integrate with keep-going when present.
- Reject stale readings and isolate Grok from inherited Claude quota checks.
- Add regression tests and Linux CI on Python 3.11–3.13.

Known limits: checks run at hook boundaries; exhausted accounts may not produce
verified summaries. Grok hook behavior and desktop-client loading vary by version.
