---
name: frictionmap
description: Reverse-engineer a real user flow (signup, login, onboarding, search, settings, sharing) from source code into a two-layer state machine of what users see and what the system does, then find friction designs miss - dead ends, endless spinners, ghost states, silent failures, loops that lose progress. Use when someone wants to understand how a flow actually works, map UX or tech flows from code, find edge cases or friction, or when Figma files are stale or missing.
---

# frictionmap

Designs show the flow someone intended. Code shows every flow that can happen. This skill reads the code, builds a state machine where every state and arrow cites the lines it came from, and runs graph checks that surface friction a design review can't see, because no screen exists for it.

It produces:

- `flow.json`: the state machine (schema in `schema.md`)
- `report.html`: diagram, issues worst-first, happy path, all evidence linked
- `findings.json`: machine-readable issues
- `flow.mmd`: Mermaid source; pastes straight into GitHub markdown or Notion

## 1. Scope the flow

Settle three things before reading code. Ask if the user hasn't said, otherwise infer and state your assumption.

- **The flow**: one journey, such as "new user signup" or "invite a teammate". Not "the app".
- **The goal state**: what "done" means, such as the first dashboard load or an accepted invite.
- **Entry points**: where users start. Always include the deep link or refresh case, because it's where most hidden states live.

Then locate the code: routes and pages, the components they render, where flow state lives (URL params, stores, local component state, server session), and the client functions that call the backend. If the backend is in the repo, include the handlers the flow calls.

Stack hints:

- **Next.js / React**: `app/**/page.tsx`, `"use server"` actions, `useSearchParams` step patterns, conditional renders (`cond ? A : B`, `cond && A`, early `return null`).
- **State libraries**: Redux reducers, Zustand stores, XState machines (read these directly; they are already state machines).
- **Mobile**: React Native navigators, SwiftUI `@State` with `NavigationStack`, Android navigation graphs and ViewModels.
- **Backend**: route handlers, status enums, webhooks, queue consumers, retries and timeouts.

## 2. Trace states

A **UI state** is something distinct the user can see and act on. Go through every component in the flow and list each render branch that changes what the user can do. Always hunt for the six states designs usually skip:

1. Loading or pending, and what ends it
2. Error, and whether it offers a way to recover
3. Empty or no data
4. Disabled controls with no explanation
5. Partial: a step that's open while its prerequisites are missing
6. Direct entry: deep link, refresh, back button, or a URL missing expected params

A **system state** is a backend or async condition: a request in flight, a record written, a job status, a webhook, a timeout. For each one, decide `surfaced`: does the user see anything distinct while it's true? `surfaced: false` on a failure is a ghost state, often the most valuable finding.

## 3. Trace transitions

For every handler (onClick, onSubmit, onChange, form action, redirect, effect, webhook), record:

- `from`, `to`, `trigger` (`user` or `system`) and `kind` (`forward`, `back`, `retry`, `failure`, `redirect`, `abandon`)
- the data it writes (`effects`)
- **every failure branch**: `.catch`, `try/catch`, error return values, non-2xx responses, a missing `else`

Missing handling is itself a transition. If a promise can reject with no catch, model a failure transition to whatever stays on screen, with `feedback: "none"`. Follow loading flags: if a flag is set true and any path never sets it false, that path leads to a stuck state. Check library behaviour that matters (for example, whether a button is disabled while loading) and cite it as external evidence.

Mark `shortcut: true` on transitions only returning users can take (saved progress), so the fewest-actions count reflects a new user.

## 4. Cite everything

Every state and transition needs `evidence`: file, line range, and a one-line note. **If you can't point to a line, it doesn't go in the graph.** Behaviour taken from a dependency gets `{"external": "<package and file>", "note": "..."}`. Anything inferred without a source gets `"unverified": true`, which the analyzer reports.

## 5. Write flow.json

Follow `schema.md`. Add the fields that make the report readable to a PM:

- On each state that has a problem: `likelihood` (`common`, `edge` or `rare`), `condition` (when it happens), `issue` (a short plain-language title), `impact` (what the user experiences, in two or three sentences) and `fix` (the smallest change that removes it).
- At the top: `headline` (the finding, as a sentence), `summary`, `primary_entry` and `repo` (`url` and `commit`, so evidence links resolve).

## 6. Run the analyzer

```bash
python3 analyze.py flow.json --repo <repo root> --out <output dir>
```

Standard library only. It fails on broken citations (missing file, line past the end of the file) and missing evidence. Fix those and rerun. Read `checks.md` for what each check means and how severity is set.

## 7. Review like a PM, verify like an engineer

- Open the cited lines for every high and medium issue and confirm the claim. When a check fires wrongly, fix the model (usually a missing transition, often a recovery path), never the report.
- Look for recoveries you missed. A component unmounting can reset stuck state, and an Edit link elsewhere on the page is a way back. Model them honestly. "Recovery only by going back" is still a finding.
- Add `manual_findings` for what the graph can't see: misleading copy, unexplained disabled controls, extra steps, unreachable feature code, dead guards.
- Keep severity honest. **High**: the user can't finish, or loses data or access. **Medium**: recoverable but confusing or costly. **Low**: polish. **Info**: code-level facts worth knowing.
- Don't claim what the code doesn't show. If the backend wasn't read, say so in `method_note`.

## 8. Present

Lead with the top three issues in plain language: what the user experiences, when, the evidence, and the fix. Then point to `report.html`. If the user wants to share it, it's a self-contained page (Mermaid loads from a CDN, with a text fallback).

## Rules

- Keep anything planted on purpose (test accounts, demo bugs) apart: set `planted` so it is reported separately from friction real users hit.
- Never invent a state or transition. Uncited means it's not in the graph.
- Model the code as written, not as intended. "TODO: handle error" means the error is unhandled.
- Static reading only. Don't run the app or call external services unless the user asks.
- For private codebases, keep code out of anything shared: cite paths and line numbers, not snippets.
