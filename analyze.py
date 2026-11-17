#!/usr/bin/env python3
"""
frictionmap analyzer

Reads a flow.json (a two-layer state machine extracted from source code),
validates every citation, runs graph-based friction checks, and writes:

  report.html    self-contained report with diagram and findings
  findings.json  machine-readable findings
  flow.mmd       Mermaid source (paste into GitHub markdown, Notion, etc.)

Usage:
  python3 analyze.py flow.json --repo path/to/repo --out out/
  python3 analyze.py flow.json --no-html          # checks only

Standard library only.
"""
import argparse
import collections
import heapq
import html
import json
import os
import re
import string
import sys

SEV_RANK = {"high": 0, "medium": 1, "low": 2, "info": 3}
DOWNGRADE = {"high": "medium", "medium": "low", "low": "low", "info": "info"}
STATE_KINDS = {"entry", "step", "loading", "error", "goal", "terminal"}
TRANS_KINDS = {"forward", "back", "retry", "failure", "redirect", "abandon"}


# ---------------------------------------------------------------- loading

def load_flow(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def parse_lines(spec):
    """'12' -> (12, 12); '12-40' -> (12, 40)."""
    m = re.fullmatch(r"\s*(\d+)\s*(?:-\s*(\d+))?\s*", str(spec))
    if not m:
        return None
    a = int(m.group(1))
    b = int(m.group(2) or a)
    return (a, b) if b >= a else None


def validate(flow, repo_dir):
    errors, warnings = [], []
    states = flow.get("states", [])
    ids = [s.get("id") for s in states]
    seen = set()
    for i in ids:
        if i in seen:
            errors.append(f"duplicate state id: {i}")
        seen.add(i)
    idset = set(ids)

    line_cache = {}

    def check_evidence(owner, evs, unverified):
        if not evs:
            if unverified:
                warnings.append(f"{owner}: marked unverified (no evidence)")
            else:
                errors.append(f"{owner}: no evidence. Cite a file and lines, or set \"unverified\": true")
            return
        for ev in evs:
            if "external" in ev:
                continue
            f = ev.get("file")
            rng = parse_lines(ev.get("lines", ""))
            if not f or not rng:
                errors.append(f"{owner}: evidence needs 'file' and 'lines' like '12-40' (got {ev})")
                continue
            if repo_dir:
                p = os.path.join(repo_dir, f)
                if p not in line_cache:
                    try:
                        with open(p, encoding="utf-8", errors="replace") as fh:
                            line_cache[p] = sum(1 for _ in fh)
                    except OSError:
                        line_cache[p] = None
                n = line_cache[p]
                if n is None:
                    errors.append(f"{owner}: cited file not found: {f}")
                elif rng[1] > n:
                    errors.append(f"{owner}: {f} has {n} lines, cited {ev['lines']}")

    for s in states:
        sid = s.get("id", "?")
        if s.get("layer") not in ("ui", "system"):
            errors.append(f"state {sid}: layer must be 'ui' or 'system'")
        if s.get("kind") not in STATE_KINDS:
            errors.append(f"state {sid}: kind must be one of {sorted(STATE_KINDS)}")
        check_evidence(f"state {sid}", s.get("evidence"), s.get("unverified"))

    for n, t in enumerate(flow.get("transitions", [])):
        tid = f"transition #{n + 1} ({t.get('from')} -> {t.get('to')})"
        for end in ("from", "to"):
            if t.get(end) not in idset:
                errors.append(f"{tid}: unknown state '{t.get(end)}'")
        if t.get("trigger") not in ("user", "system"):
            errors.append(f"{tid}: trigger must be 'user' or 'system'")
        if t.get("kind", "forward") not in TRANS_KINDS:
            errors.append(f"{tid}: kind must be one of {sorted(TRANS_KINDS)}")
        check_evidence(tid, t.get("evidence"), t.get("unverified"))

    for g in flow.get("global_transitions", []):
        if g.get("to") not in idset:
            errors.append(f"global transition: unknown target '{g.get('to')}'")
        check_evidence(f"global transition to {g.get('to')}", g.get("evidence"), g.get("unverified"))

    for m in flow.get("manual_findings", []):
        if m.get("severity") not in SEV_RANK:
            errors.append(f"manual finding '{m.get('title')}': severity must be high, medium, low or info")
        check_evidence(f"manual finding '{m.get('title')}'", m.get("evidence"), m.get("unverified"))

    if not any(s.get("kind") == "entry" for s in states):
        errors.append("no state with kind 'entry'")
    if not any(s.get("kind") == "goal" for s in states):
        errors.append("no state with kind 'goal'")
    return errors, warnings


# ---------------------------------------------------------------- graph

def build_graph(flow):
    states = {s["id"]: s for s in flow["states"]}
    out = collections.defaultdict(list)
    for t in flow.get("transitions", []):
        t.setdefault("kind", "forward")
        out[t["from"]].append(t)
    # expand global transitions (e.g. a header link on every screen)
    for g in flow.get("global_transitions", []):
        skip = set(g.get("except", [])) | {g["to"]}
        only = set(g.get("only", [])) or None
        for sid, s in states.items():
            if s["layer"] != g.get("from_layer", "ui") or sid in skip:
                continue
            if only is not None and sid not in only:
                continue
            if s["kind"] in ("goal", "terminal"):
                continue
            out[sid].append({
                "from": sid, "to": g["to"], "trigger": "user",
                "kind": g.get("kind", "abandon"), "label": g.get("label", ""),
                "evidence": g.get("evidence", []), "_global": True, "shortcut": g.get("shortcut", False),
            })
    return states, out


def bfs(starts, out, edge_ok=lambda t: True):
    seen = set(starts)
    q = collections.deque(starts)
    while q:
        u = q.popleft()
        for t in out.get(u, []):
            if edge_ok(t) and t["to"] not in seen:
                seen.add(t["to"])
                q.append(t["to"])
    return seen


def can_reach(targets, states, out, edge_ok):
    rev = collections.defaultdict(list)
    for u in states:
        for t in out.get(u, []):
            if edge_ok(t):
                rev[t["to"]].append(u)
    seen = set(targets)
    q = collections.deque(targets)
    while q:
        v = q.popleft()
        for u in rev[v]:
            if u not in seen:
                seen.add(u)
                q.append(u)
    return seen


def user_steps(start, goals, out):
    """Fewest user actions from start to any goal (system hops cost 0).
    Abandon edges are excluded. Returns (cost, path_of_transitions) or (None, [])."""
    dist = {start: 0}
    prev = {}
    pq = [(0, 0, start)]
    tie = 1
    while pq:
        d, _, u = heapq.heappop(pq)
        if d > dist.get(u, 1e9):
            continue
        if u in goals:
            path = []
            while u in prev:
                t = prev[u]
                path.append(t)
                u = t["from"]
            return d, list(reversed(path))
        for t in out.get(u, []):
            if t["kind"] == "abandon":
                continue
            # prefer success paths: failures/retries are never the happy path
            if t["kind"] in ("failure", "retry", "back") or t.get("shortcut"):
                continue
            nd = d + (1 if t["trigger"] == "user" else 0)
            if nd < dist.get(t["to"], 1e9):
                dist[t["to"]] = nd
                prev[t["to"]] = t
                heapq.heappush(pq, (nd, tie, t["to"]))
                tie += 1
    return None, []


# ---------------------------------------------------------------- checks

def adjust(sev, likelihood):
    return DOWNGRADE[sev] if likelihood == "rare" else sev


def run_checks(flow):
    states, out = build_graph(flow)
    entries = [s for s, v in states.items() if v["kind"] == "entry"]
    goals = {s for s, v in states.items() if v["kind"] == "goal"}
    progress = lambda t: t["kind"] != "abandon"

    reachable = bfs(entries, out)
    to_goal = can_reach(goals, states, out, progress)
    findings = []

    def add(check, sev, sid, title, detail, evidence, extra=None):
        s = states.get(sid, {})
        f = {
            "check": check,
            "severity": adjust(sev, s.get("likelihood")),
            "_base": sev,
            "state": sid,
            "title": title,
            "detail": detail,
            "evidence": evidence,
            "source": "graph",
        }
        if s.get("likelihood"):
            f["likelihood"] = s["likelihood"]
        if s.get("planted"):
            f["planted"] = s["planted"]
        if s.get("condition"):
            f["when"] = s["condition"]
        if extra:
            f.update(extra)
        findings.append(f)

    for sid, s in states.items():
        if s["kind"] in ("goal", "terminal"):
            continue
        outs = out.get(sid, [])
        prog = [t for t in outs if progress(t)]
        exits = [t for t in outs if not progress(t)]
        ev = s.get("evidence", [])
        label = s["label"]

        if sid not in reachable and s["kind"] != "entry":
            add("unreachable", "low", sid, f"Unreachable: {label}",
                "No path from any entry point leads here. Either dead code, or a transition is missing from the model.", ev)
            continue

        dead = not prog
        trapped = (not dead) and sid not in to_goal and s["kind"] != "entry"
        loading_stuck = s["kind"] == "loading" and not any(t["trigger"] == "system" for t in prog)

        if dead:
            escape = ", ".join(sorted({t.get("label") or t["to"] for t in exits})) or "none"
            if s["kind"] == "loading":
                title = f"Never-ending loading with no way forward: {label}"
            elif s["kind"] == "error":
                title = f"Error with no way to recover: {label}"
            else:
                title = f"Dead end: {label}"
            sev = "high" if s["kind"] in ("loading", "error") or s["layer"] == "system" else "medium"
            add("dead_end", sev, sid, title,
                f"Nothing on this screen moves the user forward. Only way out: {escape}.", ev)
        elif trapped:
            add("trap", "high", sid, f"Can't finish from here: {label}",
                "The user can still click things, but no path from this state reaches the goal without leaving the flow.", ev)

        if (s["kind"] == "error" and prog and not dead and not trapped
                and all(t["kind"] == "back" for t in prog)):
            sev = "high" if s.get("likelihood") == "common" else "medium"
            add("back_only_recovery", sev, sid, f"Recovery only by going back: {label}",
                "There is no retry here. To recover, the user has to find a way back to an earlier step: "
                + ", ".join(sorted({t.get("label") or t["to"] for t in prog})) + ".", ev)

        if loading_stuck and not dead:
            add("stuck_loading", "high", sid, f"Loading state nothing resolves: {label}",
                "No success, failure or timeout event ever leaves this state on its own. The user must notice and act.", ev)

        if s["layer"] == "system" and s.get("surfaced") is False:
            sev = "high" if s["kind"] == "error" else "medium"
            add("ghost_state", sev, sid, f"Ghost state: {label}",
                "This happens in the system, but the user sees nothing that tells them. Designs never show it because no screen exists for it.", ev)

    for t in flow.get("transitions", []):
        if t.get("kind") == "failure" and t.get("feedback") == "none":
            tgt = states[t["to"]]
            sev = "high" if t["to"] not in to_goal else "medium"
            # a system error state owns its own silent exit; otherwise the
            # screen the user lands on is where the problem shows up
            src = t["from"]
            owner = src if states[src]["layer"] == "system" and states[src]["kind"] == "error" else t["to"]
            add("silent_failure", sev, owner,
                f"Silent failure: {states[src]['label']} → {tgt['label']}",
                f"{t.get('label', 'A failure')} leads to “{tgt['label']}” with no message to the user.",
                t.get("evidence", []))
        if t.get("resets_progress"):
            back_to, _ = user_steps(t["to"], goals, out)
            extra = (f" The model counts {back_to} actions from there to the goal."
                     if back_to and t.get("count_steps", True) else "")
            add("progress_loss", "medium", t["from"],
                t.get("issue") or f"Progress loss: {t.get('label', states[t['from']]['label'])}",
                (t.get("note") or "This transition sends the user back over steps they already completed.") + extra,
                t.get("evidence", []))

    for m in flow.get("manual_findings", []):
        findings.append({
            "check": m.get("check", "review"),
            "severity": m["severity"],
            "state": m.get("state"),
            "title": m["title"],
            "detail": m.get("detail", ""),
            "evidence": m.get("evidence", []),
            "source": "review",
            **({"key": m["key"]} if m.get("key") else {}),
            **({"planted": m["planted"]} if m.get("planted") else {}),
            **({"when": m["when"]} if m.get("when") else {}),
            **({"fix": m["fix"]} if m.get("fix") else {}),
        })

    # attach suggested fixes written on states
    for f in findings:
        st = states.get(f.get("state") or "", {})
        if "fix" not in f and st.get("fix") and f["source"] == "graph":
            f["fix"] = st["fix"]

    issues = group_issues(flow, states, findings)

    paths = {}
    primary = flow.get("primary_entry")
    for e in ([primary] if primary in states else entries):
        cost, path = user_steps(e, goals, out)
        paths[e] = {"actions": cost, "path": path}

    return states, out, issues, paths


LIKELY_RANK = {"common": 0, None: 1, "edge": 2, "rare": 3}


def group_issues(flow, states, findings):
    """One root cause often trips several checks along a failure chain
    (ghost state -> silent failure -> stuck spinner). Group graph findings
    whose states are linked by system or failure transitions into one issue."""
    parent = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    flagged = {f["state"] for f in findings if f["source"] == "graph" and f.get("state")}
    for t in flow.get("transitions", []):
        if t["from"] in flagged and t["to"] in flagged and (
                t["trigger"] == "system" or t.get("kind") == "failure"):
            parent[find(t["from"])] = find(t["to"])

    clusters = collections.OrderedDict()
    singles = []
    for f in findings:
        if f["source"] == "graph" and f.get("state") and f["check"] != "progress_loss":
            clusters.setdefault(find(f["state"]), []).append(f)
        else:
            singles.append([f])

    issues = []
    for group in list(clusters.values()) + singles:
        members = {f["state"] for f in group if f.get("state")}
        likes = [states[s].get("likelihood") for s in members if s in states]
        rarest = max(likes, key=lambda x: LIKELY_RANK[x]) if likes else None
        # severity: worst base severity, downgraded once if the chain only happens rarely
        base = min((SEV_RANK[f["severity"]] if f["source"] != "graph" else
                    SEV_RANK[f.get("_base", f["severity"])] for f in group))
        sev = [k for k, v in SEV_RANK.items() if v == base][0]
        if any(f["source"] == "graph" for f in group):
            sev = adjust(sev, "rare" if rarest == "rare" else None)

        def lead_key(f):
            st = states.get(f.get("state") or "", {})
            return (st.get("layer") != "ui", SEV_RANK[f["severity"]])
        lead = sorted(group, key=lead_key)[0]
        lead_state = states.get(lead.get("state") or "", {})

        impact = next((states[s]["impact"] for s in [lead.get("state")] + sorted(members)
                       if s in states and states[s].get("impact")), None)
        seen, evidence = set(), []
        lead_ev = [{"_f": None, "evidence": states[sid].get("evidence", [])}
                   for sid in sorted(members) if sid in states and states[sid].get("impact")]
        for f in lead_ev + group:
            for ev in f["evidence"]:
                k = (ev.get("file") or ev.get("external"), ev.get("lines"))
                if k not in seen:
                    seen.add(k)
                    evidence.append(dict(ev))
        fix = next((f["fix"] for f in group if f.get("fix")), None) or next(
            (states[s]["fix"] for s in members if s in states and states[s].get("fix")), None)
        when = next((f["when"] for f in group if f.get("when")), None)
        title = (lead_state.get("issue") if lead["source"] == "graph" else None) or lead["title"]
        issue = {
            "severity": sev,
            "title": title,
            "detail": impact or lead["detail"],
            "checks": sorted({f["check"] for f in group}),
            "states": sorted(members),
            "lead_state": lead.get("state"),
            "evidence": evidence,
            "source": "graph" if any(f["source"] == "graph" for f in group) else "review",
            "likelihood": rarest,
            "findings": [f["title"] for f in group],
            "keys": sorted({f["key"] for f in group if f.get("key")}),
        }
        planted = {f.get("planted") for f in group}
        if None not in planted:
            issue["planted"] = ", ".join(sorted(planted))
        if fix:
            issue["fix"] = fix
        if when:
            issue["when"] = when
        issues.append(issue)

    issues.sort(key=lambda i: (SEV_RANK[i["severity"]], i["source"] != "graph",
                               LIKELY_RANK[i.get("likelihood")], i["title"]))
    nf = np_ = 0
    for i in issues:
        if i.get("planted"):
            np_ += 1
            i["id"] = f"P{np_}"
        else:
            nf += 1
            i["id"] = f"F{nf}"
    return issues


# ---------------------------------------------------------------- outputs

def evidence_link(ev, repo):
    if "external" in ev:
        return None
    url, commit = repo.get("url"), repo.get("commit")
    if not url:
        return None
    a, b = parse_lines(ev["lines"])
    ref = commit or "HEAD"
    frag = f"#L{a}" if a == b else f"#L{a}-L{b}"
    return f"{url.rstrip('/')}/blob/{ref}/{ev['file']}{frag}"


def mermaid(flow, states, out, findings):
    hide_planted = not flow.get("diagram_planted", not flow.get("ground_truth"))
    by_state = collections.defaultdict(list)
    for f in findings:
        if hide_planted and f.get("planted"):
            continue
        for sid in f.get("states") or []:
            by_state[sid].append(f)

    def nid(s):
        return re.sub(r"[^A-Za-z0-9_]", "_", s)

    def esc(t):
        return t.replace('"', "'").replace("<", "‹").replace(">", "›")

    def wrap(t, n):
        words, rows, cur = t.split(), [], ""
        for w in words:
            if cur and len(cur) + 1 + len(w) > n:
                rows.append(cur)
                cur = w
            else:
                cur = (cur + " " + w).strip()
        if cur:
            rows.append(cur)
        return "<br/>".join(rows)

    # handled errors (inline message + retry, no findings) are good news; hide them to cut clutter
    hidden = set()
    if flow.get("compact_diagram", True):
        for sid, s in states.items():
            prog = [t for t in out.get(sid, []) if t["kind"] != "abandon"]
            if (s["layer"] == "ui" and s["kind"] == "error" and not by_state.get(sid)
                    and prog and all(t["kind"] == "retry" for t in prog)):
                hidden.add(sid)
    flow["_hidden"] = [states[h]["label"] for h in sorted(hidden)]
    if hide_planted:
        hidden |= {sid for sid, s in states.items() if s.get("planted")}
        flow["_planted_hidden"] = True

    lines = ["flowchart TD"]
    for sid, s in states.items():
        if sid in hidden:
            continue
        fs = sorted(by_state.get(sid, []), key=lambda f: SEV_RANK[f["severity"]])
        tag = (" · " + ",".join(f["id"] for f in fs)) if fs else ""
        text = wrap(esc(s["label"]), 22) + (("<br/>" + tag.strip(" ·")) if tag else "")
        if s["layer"] == "system":
            shape = f'(["{text}"])'
        elif s["kind"] in ("entry", "goal", "terminal"):
            shape = f'(("{text}"))' if s["kind"] == "goal" else f'["{text}"]'
        else:
            shape = f'["{text}"]'
        lines.append(f"  {nid(sid)}{shape}")
    back_idx = []
    k = 0
    for t in flow.get("transitions", []):
        if t["from"] in hidden or t["to"] in hidden:
            continue
        lab = wrap(esc(t.get("label", "")), 18)
        arrow = "-->" if t["trigger"] == "user" else "-.->"
        lines.append(f'  {nid(t["from"])} {arrow}' + (f'|"{lab}"|' if lab else "") + f' {nid(t["to"])}')
        if t.get("kind") == "back":
            back_idx.append(k)
        k += 1
    if back_idx:
        lines.append(f"  linkStyle {','.join(map(str, back_idx))} stroke:#9a948b,stroke-width:1px")
    # classes
    groups = collections.defaultdict(list)
    for sid, s in states.items():
        if sid in hidden:
            continue
        fs = by_state.get(sid, [])
        worst = min((SEV_RANK[f["severity"]] for f in fs), default=9)
        if s["layer"] == "system" and s.get("surfaced") is False:
            cls = "ghost"
        elif worst == 0:
            cls = "high"
        elif worst == 1:
            cls = "medium"
        elif s["kind"] == "goal":
            cls = "goal"
        elif s["kind"] == "entry":
            cls = "entry"
        elif s["layer"] == "system":
            cls = "system"
        else:
            cls = "step"
        groups[cls].append(nid(sid))
    for cls, members in groups.items():
        lines.append(f"  class {','.join(members)} {cls}")
    return "\n".join(lines) + "\n"


def write_outputs(flow, states, findings, paths, mmd, outdir, make_html):
    os.makedirs(outdir, exist_ok=True)
    repo = flow.get("repo", {})
    for f in findings:
        for ev in f["evidence"]:
            link = evidence_link(ev, repo)
            if link:
                ev["link"] = link
    with open(os.path.join(outdir, "findings.json"), "w", encoding="utf-8") as fh:
        json.dump({"flow": flow.get("flow"), "repo": repo, "findings": findings,
                   "happy_paths": {k: {"actions": v["actions"],
                                       "path": [f'{t["from"]} -> {t["to"]}' for t in v["path"]]}
                                   for k, v in paths.items()}}, fh, indent=2)
    with open(os.path.join(outdir, "flow.mmd"), "w", encoding="utf-8") as fh:
        fh.write(mmd)
    if make_html:
        tpl_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "report_template.html")
        with open(tpl_path, encoding="utf-8") as fh:
            tpl = string.Template(fh.read())
        with open(os.path.join(outdir, "report.html"), "w", encoding="utf-8") as fh:
            fh.write(tpl.substitute(render_parts(flow, states, findings, paths, mmd)))


def e(x):
    return html.escape(str(x), quote=True)


def render_parts(flow, states, findings, paths, mmd):
    repo = flow.get("repo", {})
    real = [f for f in findings if not f.get("planted")]
    planted = [f for f in findings if f.get("planted")]
    counts = collections.Counter(f["severity"] for f in real)
    ghosts = sum(1 for s in states.values() if s["layer"] == "system" and s.get("surfaced") is False
                 and not s.get("planted"))
    ui_n = sum(1 for s in states.values() if s["layer"] == "ui")
    sys_n = len(states) - ui_n
    best = min((p["actions"] for p in paths.values() if p["actions"] is not None), default=None)

    flow_name = flow.get("flow", "Flow")
    headline = flow.get("headline") or (
        f"{flow_name}: {counts['high']} high-severity friction points found in the code"
    )

    def ev_html(evs):
        bits = []
        for ev in evs:
            if "external" in ev:
                bits.append(f'<li><span class="path">{e(ev["external"])}</span> <span class="note">{e(ev.get("note", ""))}</span></li>')
                continue
            loc = f'{ev["file"]}:{ev["lines"]}'
            short = re.sub(r"^src/", "", loc)
            a = f'<a href="{e(ev["link"])}">{e(short)}</a>' if ev.get("link") else e(short)
            bits.append(f'<li><span class="path">{a}</span> <span class="note">{e(ev.get("note", ""))}</span></li>')
        return "<ul class=\"ev\">" + "".join(bits) + "</ul>"

    def row(f):
        st = states.get(f.get("lead_state") or "", {})
        where = e(st.get("label", "")) if st else ""
        when = f'<p class="when">When: {e(f["when"])}</p>' if f.get("when") else ""
        fix = f'<p class="fix">Fix: {e(f["fix"])}</p>' if f.get("fix") else ""
        src = "graph check" if f["source"] == "graph" else "code review"
        like = {"common": "happens often", "edge": "edge case", "rare": "rare"}.get(f.get("likelihood"))
        if like:
            src += " \u00b7 " + like
        if f.get("planted"):
            src += " \u00b7 planted for " + f["planted"]
        checks = ", ".join(c.replace("_", " ") for c in f["checks"] if c not in ("review", "code"))
        return (
            f'<tr id="{f["id"]}" class="sev-{f["severity"]}">'
            f'<td data-l="#" class="fid">{f["id"]}</td>'
            f'<td data-l="Severity"><span class="pill {f["severity"]}">{f["severity"]}</span></td>'
            f'<td data-l="Finding" class="what"><strong>{e(f["title"])}</strong><p>{e(f["detail"])}</p>{when}{fix}'
            f'<p class="src">{src}{(" \u00b7 " + e(checks)) if checks else ""}{(" \u00b7 " + where) if where else ""}</p></td>'
            f'<td data-l="Evidence">{ev_html(f["evidence"])}</td></tr>'
        )

    rows = [row(f) for f in real]

    # ground truth: bugs the project planted on purpose, and how each showed up
    gt_rows, found = [], 0
    for g in flow.get("ground_truth", []):
        ref = g["ref"]
        hit = next((f for f in findings if ref in f.get("states", []) or ref in f.get("keys", [])), None)
        if hit:
            how = (f'<a href="#{hit["id"]}">{hit["id"]}</a> \u00b7 ' +
                   e(", ".join(c.replace("_", " ") for c in hit["checks"]) if hit["source"] == "graph" else "code review"))
            status = "Flagged"
            found += 1
        elif ref in states:
            how = "Mapped as its own state, with a visible message"
            status = "Mapped"
            found += 1
        else:
            how, status = "Not in the model", "Missed"
        gt_rows.append(f'<tr><td data-l="Test account"><code>{e(g["persona"])}</code></td>'
                       f'<td data-l="Planted bug">{e(g["bug"])}</td>'
                       f'<td data-l="Status"><span class="pill {"high" if status == "Missed" else "info"}">{status}</span></td>'
                       f'<td data-l="How it showed up">{how}</td></tr>')
    planted_section = ""
    if gt_rows:
        prow = "\n".join(row(f) for f in planted)
        planted_section = (
            '<section class="band" style="padding-top:8px"><div class="wrap">'
            '<h2 class="sec">Ground truth: the planted bugs</h2>'
            f'<p class="sec-sub">{e(repo.get("name", "This project"))} plants bugs on purpose for its test accounts. '
            f'Here is how each one showed up in the map: {found} of {len(gt_rows)} surfaced.</p>'
            '<div class="scroll"><table class="stack gt"><thead><tr><th>Test account</th><th>Planted bug</th><th>Status</th><th>How it showed up</th></tr></thead>'
            f'<tbody>{"".join(gt_rows)}</tbody></table></div>'
            + (f'<details class="pd"><summary>Details for the {len(planted)} planted issues the checks flagged</summary>'
               '<div class="scroll"><table class="stack"><colgroup><col class="c-id"><col class="c-sev"><col><col class="c-ev"></colgroup>'
               f'<tbody>{prow}</tbody></table></div></details>' if planted else "")
            + '</div></section>'
        )

    path_html = []
    for entry, p in paths.items():
        if p["actions"] is None:
            continue
        steps = [f'<li><span class="who {t["trigger"]}">{t["trigger"]}</span> '
                 f'{e(t.get("label") or "")} <span class="arrow">\u2192</span> {e(states[t["to"]]["label"])}</li>'
                 for t in p["path"]]
        path_html.append(f'<div class="path-card"><h3>Happy path from {e(states[entry]["label"])}: {p["actions"]} actions</h3><ol>{"".join(steps)}</ol></div>')

    commit = repo.get("commit", "")
    repo_line = e(repo.get("name", ""))
    if repo.get("url"):
        repo_line = f'<a href="{e(repo["url"])}">{repo_line}</a>'
    if commit:
        repo_line += f' at <code>{e(commit[:7])}</code>'

    if gt_rows:
        fig3 = f'<b>{found}/{len(gt_rows)}</b><span>planted bugs surfaced</span>'
    else:
        fig3 = f'<b>{ghosts}</b><span>ghost states with no screen</span>'

    return {
        "title": e(flow.get("title") or f"{flow_name} friction map"),
        "headline": e(headline),
        "summary": e(flow.get("summary", "")),
        "repo_line": repo_line,
        "n_states": len(states),
        "n_ui": ui_n,
        "n_sys": sys_n,
        "n_trans": len(flow.get("transitions", [])),
        "n_high": counts["high"],
        "n_issues": len(real),
        "fig3": fig3,
        "goal_phrase": e(flow.get("goal_phrase", "fewest actions to the goal")),
        "best": best if best is not None else "\u2013",
        "rows": "\n".join(rows),
        "issues_sub": e("Friction any user can hit. " if gt_rows else "") + "Each issue groups every check that fired along one failure chain. Numbers match the labels in the diagram. Every claim links to the lines of code it came from.",
        "planted_section": planted_section,
        "paths": "\n".join(path_html),
        "mmd": mmd.replace("</", "<\\/"),
        "hidden_note": e((("Handled errors hidden to keep the diagram readable: " + ", ".join(flow.get("_hidden", [])) + ". Each shows a message and lets the user retry. ") if flow.get("_hidden") else "")
                         + ("Bugs planted for test accounts are listed in their own table below, not drawn. " if flow.get("_planted_hidden") else "")),
        "generated_note": e(flow.get("method_note", "Extracted by reading source code statically. Every state and arrow cites the lines it came from.")),
    }


# ---------------------------------------------------------------- cli

def main():
    ap = argparse.ArgumentParser(description="Find friction in a flow state machine.")
    ap.add_argument("flow", help="path to flow.json")
    ap.add_argument("--repo", help="repo root, used to verify every citation")
    ap.add_argument("--out", default="frictionmap-out", help="output directory")
    ap.add_argument("--no-html", action="store_true")
    args = ap.parse_args()

    flow = load_flow(args.flow)
    errors, warnings = validate(flow, args.repo)
    for w in warnings:
        print(f"warning: {w}", file=sys.stderr)
    if errors:
        print("Model has problems. Fix these and run again:", file=sys.stderr)
        for err in errors:
            print(f"  - {err}", file=sys.stderr)
        sys.exit(1)

    states, out, findings, paths = run_checks(flow)
    mmd = mermaid(flow, states, out, findings)
    write_outputs(flow, states, findings, paths, mmd, args.out, not args.no_html)

    c = collections.Counter(f["severity"] for f in findings if not f.get("planted"))
    print(f"{flow.get('flow', 'flow')}: {len(states)} states, {len(flow.get('transitions', []))} transitions")
    print(f"issues: {c['high']} high, {c['medium']} medium, {c['low']} low"
          + (f" (+{sum(1 for f in findings if f.get('planted'))} planted)" if any(f.get("planted") for f in findings) else ""))
    for f in findings:
        print(f"  {f['id']:>4} [{f['severity']:<6}] {f['title']}")
    for entry, p in paths.items():
        if p["actions"] is not None:
            print(f"fewest actions from {entry} to goal: {p['actions']}")
    print(f"wrote {args.out}/findings.json, flow.mmd" + ("" if args.no_html else ", report.html"))


if __name__ == "__main__":
    main()
