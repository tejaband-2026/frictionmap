# Checks

The analyzer runs these on the graph. Abandon edges (leaving the flow) never count as a way forward.

| Check | Fires when | What the user experiences | Base severity |
| --- | --- | --- | --- |
| `dead_end` | A non-goal state has no way forward, only abandon exits or none | Nothing on screen moves them on; they reload or leave | High for loading, error and system states; medium otherwise |
| `trap` | The user can still act, but no path reaches the goal | They click around and never finish | High |
| `stuck_loading` | A loading state has no system event (success, failure, timeout) that ends it | A spinner or skeleton that never resolves on its own | High |
| `ghost_state` | A system state has `surfaced: false` | Something happened (a failure, a partial write) and the screen doesn't change | High for errors, medium otherwise |
| `silent_failure` | A `failure` edge has `feedback: "none"` | An error occurred and nothing says so | High if the target can't reach the goal, else medium |
| `back_only_recovery` | An error state's only ways forward go back to earlier steps | No retry; recovering means finding an Edit link | Medium; high when `likelihood: common` |
| `progress_loss` | An edge has `resets_progress: true` | They redo steps they already finished | Medium |
| `unreachable` | No entry reaches the state | Dead code, or a missing transition in the model | Low |

**Likelihood.** `rare` lowers severity one level. `common` raises `back_only_recovery` to high. `edge` only labels the issue.

**Grouping.** One root cause often fires several checks along a chain (ghost state → silent failure → stuck spinner). Findings on states linked by system or failure edges are merged into one issue. The issue takes the worst severity and is titled from the screen the user sees.

**Fewest actions.** This counts user actions from `primary_entry` to the goal. System hops cost nothing, and failure, retry, back and shortcut edges are skipped. It's a floor, not a measured average.

**What the graph can't see** goes in `manual_findings`: misleading copy, unexplained disabled controls, slow but working steps, feature code that can't be reached, and contradictions in the code such as dead guards.

**Planted bugs and ground truth.** Practice apps and demos often plant bugs on purpose for test accounts. Mark those states and findings with `planted`, so they're reported separately from friction any real user can hit. List known bugs in `ground_truth`, and the report scores how many the map surfaced and how each one showed up.
