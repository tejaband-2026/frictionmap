# frictionmap

**Your designs show the flow someone intended. Your code shows every flow that can actually happen.**

frictionmap is an agent skill that reads a codebase, rebuilds a user flow (signup, login, onboarding, search, settings) as a state machine, and finds the friction no design file shows: dead ends, spinners that never resolve, failures the user never hears about, and loops that throw away progress.

Every state and every arrow cites the file and lines it came from, so an engineer can check any claim in seconds.

![frictionmap run on SauceDemo](poster.png)

## Why

As a PM, when I need to understand how an existing flow really works, the Figma is often stale, missing, or only covers the happy path. The source of truth is the code. Reading it by hand catches the real end-to-end flow, the edge cases and how data gets updated, but it takes days. frictionmap turns that into a repeatable method.

## What it finds

| Check | What the user experiences |
| --- | --- |
| Dead end | Nothing on screen moves them forward |
| Trap | They can click, but can never finish |
| Stuck loading | A spinner or frozen screen nothing resolves |
| Ghost state | Something failed or changed in the system, and the screen doesn't show it |
| Silent failure | An error happened, with no message |
| Back-only recovery | No retry; the only fix is going back a step |
| Progress loss | They redo steps they already finished |

Plus a code review for what a graph can't see: misleading copy, unreachable feature code, contradictions in the code. Details in [checks.md](checks.md).

## Example: SauceDemo, with a built-in answer key

[SauceDemo](https://www.saucedemo.com) is Sauce Labs' practice app for test automation. Its source ([saucelabs/sample-app-web](https://github.com/saucelabs/sample-app-web)) plants bugs on purpose for test accounts like `problem_user` and `error_user`. That makes it a fair test.

Mapping login, browsing and the cart (29 states, 51 transitions):

- **12 planted bugs surfaced**, each tied to the lines that cause it.
- **3 serious problems any user can hit**, outside the planted ones:
  1. **Sessions end 10 minutes after login, even mid-use.** The cookie expiry is fixed at login and nothing extends it. The next click lands on a login screen that reads like you did something wrong.
  2. **A mistyped URL shows a blank page.** There's no catch-all route: no header, no menu, no way out.
  3. **You can add an item that doesn't exist, and can't remove it.** The not-found page still offers Add to cart. The badge counts the item, but the cart has no row for it.
- **Plus:** logging back in forgets where you were, logging out leaves your cart for the next person, and after Reset App State the buttons still say Remove.

Full report: [example-saucedemo-report.html](example-saucedemo-report.html) · Model: [flow.json](example-saucedemo-flow.json)

## Use it

Copy this folder into your agent's skills directory (for Claude Code: `~/.claude/skills/frictionmap`). It follows the open [Agent Skills](https://agentskills.io) format, so it works with other compatible agents too. Then ask:

> Map our signup flow from the landing page to the first dashboard and find the friction.

The agent follows [SKILL.md](SKILL.md): it scopes the flow, traces states and transitions with citations, writes `flow.json`, then runs:

```bash
python3 analyze.py flow.json --repo path/to/repo --out out/
```

The analyzer uses the Python standard library only. It verifies every citation against the repo, runs the checks and writes `report.html`, `findings.json` and `flow.mmd`. The `.mmd` file is Mermaid source that renders directly in GitHub markdown.

## Limits

- It reads code statically. It doesn't run your app, so frequencies are estimates (`common`, `edge`, `rare`), not measurements.
- The model is only as good as the trace. That's why every line is cited and the analyzer refuses uncited claims.
- On a private codebase, share the report, not the code. Reports cite paths and line numbers, not code snippets.

## License

MIT
