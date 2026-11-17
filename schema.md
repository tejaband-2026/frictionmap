# flow.json schema

A two-layer state machine. `ui` states are what the user sees; `system` states are what the backend or async code is doing. Transitions connect them.

```jsonc
{
  "flow": "Signup",                       // short name
  "headline": "A failed email check freezes signup ...", // the main finding, as a sentence (optional)
  "summary": "One paragraph for the report header",
  "primary_entry": "landing",             // entry used for the fewest-actions path
  "goal_phrase": "fewest clicks to a working account", // label for that figure
  "repo": {"name": "org/repo", "url": "https://github.com/org/repo", "commit": "<sha>"},
  "method_note": "What was and wasn't read",
  "compact_diagram": true,                // hide handled errors (message + retry) in the diagram

  "states": [{
    "id": "verify_failed_stuck",          // unique, snake_case
    "label": "Verification failed, button stuck",
    "layer": "ui",                        // "ui" | "system"
    "kind": "error",                      // entry | step | loading | error | goal | terminal
    "surfaced": false,                    // system states only: does the user see anything distinct?
    "likelihood": "common",               // common | edge | rare  (rare lowers severity one level)
    "condition": "When it happens",
    "issue": "Short plain-language title for the report",
    "impact": "What the user experiences, 2-3 sentences",
    "fix": "Smallest change that removes it",
    "planted": "test_user",               // optional: bug planted on purpose for a test account; reported separately
    "evidence": [
      {"file": "src/components/verify.tsx", "lines": "109-122", "note": "Error path never resets submitting"},
      {"external": "@acme/ui 4.2.1 button.js", "note": "Disabled while loading"}
    ]
  }],

  "transitions": [{
    "from": "email_step", "to": "sys_send_code",
    "trigger": "user",                    // "user" | "system"
    "kind": "forward",                    // forward | back | retry | failure | redirect | abandon
    "label": "Send code",
    "condition": "Only when ...",
    "feedback": "none",                   // failures: "none" | "inline" | "page"
    "effects": ["writes user.email"],
    "resets_progress": false,             // sends the user back over completed steps
    "count_steps": true,                  // with resets_progress: report actions-to-goal from the target
    "note": "Why it matters (used by progress-loss findings)",
    "issue": "Title for a progress-loss finding",
    "shortcut": false,                    // only returning users can take it; excluded from fewest-actions
    "evidence": [{"file": "...", "lines": "79-108"}]
  }],

  "global_transitions": [{               // exits available on every screen, e.g. a header link
    "to": "home", "label": "Logo link home", "kind": "abandon",
    "from_layer": "ui", "except": ["direct_link"],   // or "only": [...] to list states
    "shortcut": false,
    "evidence": [{"file": "...", "lines": "14-26"}]
  }],

  "manual_findings": [{                   // friction the graph can't detect
    "title": "Expired invite shows a 404",
    "severity": "medium",                 // high | medium | low | info
    "state": "invite_404",
    "check": "review",                    // free-form tag
    "detail": "...", "when": "...", "fix": "...",
    "key": "optional id, for ground_truth refs", "planted": "optional persona",
    "evidence": [{"file": "...", "lines": "74-81"}]
  }],

  "ground_truth": [{                      // optional: known bugs, to score how many the map surfaced
    "persona": "test_user", "bug": "Code never arrives", "ref": "<state id or manual finding key>"
  }]
}
```

## Modelling tips

- **Entry states** are where users arrive (`kind: "entry"`). Add a direct-link entry for any flow that reads its step from the URL.
- **System steps** sit between a user action and the next screen: `address_form --submit--> sys_save_address --success--> delivery`. Give each failure its own edge.
- **Failure with no UI**: model a `system` state with `surfaced: false`, then a `failure` edge with `feedback: "none"` to whatever stays on screen.
- **Leaving the flow** (a "Back to home" link) is an `abandon` edge. It doesn't count as recovery, so a state whose only exit is abandoning is reported as a dead end.
- **Unmount resets**: if leaving a step remounts a component and clears stuck state, that is a real `back` path. Model it.
- Keep labels short (they wrap in the diagram at about 22 characters).
