"""
exporters.py
------------
Per project, under output/<NAME>/:

  script.md                  copy to the VIDEO agent repo as projects/<NAME>/script.md (identical layout to its own script step)
  script_keywords_marked.md  the same script with *keywords* marked, for YOUR review only (never give this one to the video agent)
  youtube_upload.md          title, description intro, tags, hashtags
  shot_list.csv / .md        what viewers should see on every line, and which footage covers it
  video_brief.md             checks, keyword placement, comparison points / benefits, recommendation, what needs your eyes
  script.json                everything, machine-readable
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import rules


# ------------------------------------------------------------------ script.md (mirrors adagent/scriptgen.py script_to_md)
def script_to_md(script: dict, project) -> str:
    L = [f"# TITLE: {script.get('title', '')}", f"# INTRO: {script.get('description_intro', '')}",
         f"# TAGS: {', '.join(script.get('tags', []))}", f"# ANGLE: {script.get('angle', '')}"]
    if project.kind == "compare":
        L.append("# PRODUCTS: " + "; ".join(f"{i}={lab} ({sub.name})" for i, (sub, lab) in
                                            enumerate(zip(project.products, project.labels), 1)))
    L += ["#", "# HOW TO EDIT: change any wording, delete a line to cut it, add a line with a new id (e.g. [n1]).",
          "# Format:  [id] narration | product: 1-9/all | callout: up to 4 words | assets: A01,A02 | p: 1-3",
          "# The sign-off is added automatically; where the affiliate disclosure appears is set in config.yaml (disclosure: mode). Do not add prices, ratings or review quotes.",
          "# Edit this file in place (projects/%s/script.md), commit, then run the render step." % project.name, ""]
    cur = None
    for s in script["segments"]:
        if s["section"] != cur:
            cur = s["section"]
            L += ["", f"## {cur}"]
        text = s["text"].replace(" | ", ", ")
        opts = []
        if s.get("product") and s["product"] != "all":
            opts.append(f"product: {s['product']}")
        if s.get("onscreen"):
            opts.append(f"callout: {s['onscreen']}")
        if s.get("asset_ids"):
            opts.append("assets: " + ",".join(s["asset_ids"]))
        opts.append(f"p: {s.get('priority', 2)}")
        L.append(f"[{s['id']}] {text} | " + " | ".join(opts))
    notes = []
    notes.append(f"estimated length: {script['estimated_minutes']} min; similarity to earlier scripts: not checked "
                 "(this file was written by the YouTube affiliate agent)")
    for s in script["segments"]:
        for w in s.get("warn", []):
            notes.append(f"STYLE [{s['id']}]: {w}")
    for k in script.get("keyword_issues", []):
        notes.append(f"KEYWORD: {k}")
    for n in script.get("notes", []):
        notes.append(f"NOTE: {n}")
    notes = [n.replace("-->", "->") for n in notes]
    L += ["", "<!-- REVIEW NOTES (ignored when rendering)", *notes, "-->"]
    return "\n".join(L) + "\n"


def marked_md(script_md: str, keywords: list) -> str:
    out = []
    for line in script_md.splitlines():
        if line.startswith("[") and " | " in line:
            head, _, rest = line.partition("] ")
            text, sep, opts = rest.partition(" | ")
            line = f"{head}] {rules.mark_keywords(text, keywords)}{sep}{opts}"
        out.append(line)
    return "\n".join(out) + "\n"


# ------------------------------------------------------------------ YouTube text
def upload_md(script: dict, project) -> str:
    L = [f"# YouTube upload text: {project.name}", "",
         "Your video agent writes `description.txt` (its first line is the affiliate disclosure, then the product link(s)) and `title.txt` when it renders. "
         "Use `description.txt` as the base of the description box, then add the hashtags below at the end.", "",
         "## Title", "", script["title"], f"({len(script['title'])} characters)", "",
         "## Description intro (already in script.md as # INTRO, so the video agent includes it)", "", script["description_intro"], "",
         "## Tags (already in script.md as # TAGS)", "", ", ".join(script["tags"]), "",
         "## Hashtags (paste at the end of the description)", "", " ".join(script["hashtags"]) or "(none)", ""]
    if script["unusable_keywords"]:
        L += ["## Keywords NOT used", "",
              "The facts do not support these phrases, so they stay out of the title, narration and tags. Add a real fact (a spec or a "
              "hands_on_note) that backs the phrase, or drop it:", "", *[f"- {k}" for k in script["unusable_keywords"]], ""]
    return "\n".join(L)


# ------------------------------------------------------------------ shot list
def shot_rows(script: dict, project, have_footage: bool) -> list:
    foot = {f["id"]: f for f in project.footage + [x for s in project.products for x in s.footage]}
    rows = []
    for s in script["segments"]:
        ids = s.get("asset_ids") or []
        if ids:
            status = "matched"
        elif have_footage:
            status = "no footage listed for this line: film or capture the visual"
        else:
            status = "no footage list given (add footage.csv to match footage)"
        rows.append({"line": s["id"], "section": s["section"], "narration": " ".join(s["text"].split()[:12]) + ("..." if len(s["text"].split()) > 12 else ""),
                     "visual": s.get("visual", ""), "footage_ids": ",".join(ids),
                     "footage": "; ".join(f"{i}: {foot[i]['file']}" for i in ids if i in foot), "status": status})
    return rows


def write_shot_files(out: Path, rows: list) -> None:
    with open(out / "shot_list.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["line", "section", "narration", "visual", "footage_ids", "footage", "status"])
        w.writeheader()
        w.writerows(rows)
    L = ["# Shot list", "", "| Line | Section | Narration | What viewers should see | Footage | Status |", "|---|---|---|---|---|---|"]
    for r in rows:
        L.append(f"| {r['line']} | {r['section']} | {r['narration']} | {r['visual']} | {r['footage'] or '-'} | {r['status']} |")
    (out / "shot_list.md").write_text("\n".join(L) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ brief
def brief_md(script: dict, project, errors: list, warnings: list, today: str) -> str:
    lo, hi = script["length_window"]
    L = [f"# Video brief: {project.name} ({'comparison' if project.kind == 'compare' else 'single product'})", f"*Run date:* {today}", "",
         "## 1. Before you use script.md",
         f"- Estimated length **{script['estimated_minutes']} min**. The video agent's `{script['length_key']}` length accepts {lo}-{hi} min: "
         + ("choose **short** in its workflow's length box." if script["length_key"] == "short" else "leave its length on **auto**."),
         "- Copy `script.md` to the video agent repo as `projects/%s/script.md`, then run its **render** step. Do not run its script step with force, or it overwrites this file." % project.name,
         "- The affiliate disclosure and the sign-off line are added by the video agent. They are not in this script.",
         "- Give the video agent the SAME `facts.yaml` (and `compare.yaml`) as this agent used."]
    if warnings:
        L += ["", "### Warnings about your facts", *[f"- {w}" for w in warnings]]
    L += ["", "## 2. Keywords", "", "| Keyword | Role | Narration lines | In title | In intro | Within first 5 lines |", "|---|---|---|---|---|---|"]
    for k in script["keyword_report"]:
        L.append(f"| {k['keyword']} | {k['role']} | {k['narration_count']} (lines {', '.join(k['lines']) or '-'}) | "
                 f"{'yes' if k['in_title'] else 'NO'} | {'yes' if k['in_intro'] else 'no'} | {'yes' if k['in_first_5_lines'] else 'no'} |")
    if not script["keyword_report"]:
        L.append("| (no keyword is supported by the facts) | - | - | - | - | - |")
    if script["keyword_issues"]:
        L += ["", "**Still to fix by hand:**", *[f"- {k}" for k in script["keyword_issues"]]]
    if script["unusable_keywords"]:
        L += ["", "**Not used (the facts do not back these phrases):** " + ", ".join(script["unusable_keywords"])]

    ex = script["extras"]
    if project.kind == "single":
        L += ["", "## 3. Benefits to emphasise (each tied to one of your facts)"]
        L += ([f"- **{b['benefit']}**  (fact: {b['fact']})" for b in ex["benefits"]] or ["- none could be tied to a fact"])
    else:
        L += ["", "## 3. Comparison points (only points where every product has a fact)", ""]
        head = "| Point | " + " | ".join(project.labels) + " |"
        L += [head, "|---|" + "---|" * len(project.labels)]
        for c in ex["comparison_points"]:
            L.append(f"| {c['point']} | " + " | ".join(c["facts"][str(i)] for i in range(1, len(project.labels) + 1)) + " |")
        if not ex["comparison_points"]:
            L.append("| (no point is backed by facts for every product) |" + " |" * len(project.labels))
    L += ["", "## 4. Recommendation", "", ex["recommendation"] or "No recommendation passed the checks. Write your own from the verdict lines."]
    if project.kind == "compare":
        L += ["", "Winner allowed: " + ("yes (every product has 3+ hands_on_notes)." if project.pick_ok else "NO. The script stays neutral because not every product has 3+ hands_on_notes.")]

    flagged = [s for s in script["segments"] if s.get("warn")]
    L += ["", "## 5. Lines that need your eyes"]
    L += ([f"- [{s['id']}] {s['text'][:70]}...  ->  {'; '.join(s['warn'])}" for s in flagged] or ["- none"])
    if script["dropped"]:
        L += ["", "### Removed because they broke a hard rule", *[f"- {d['text'][:80]}... ({', '.join(d['why'])})" for d in script["dropped"]]]
    if script["cut"]:
        L += ["", "### Cut to fit the length window", *[f"- {c['text'][:90]}..." for c in script["cut"]]]
    if script["notes"]:
        L += ["", "## Run notes", *[f"- {n}" for n in script["notes"]]]
    L += ["", "## 6. Before you post",
          "- [ ] Read every line against your own product and footage. The agent only knows what is in facts.yaml.",
          "- [ ] Anything the script says in first person must be something you really did or saw.",
          "- [ ] The video agent's own REVIEW_CHECKLIST.md is the final gate."]
    return "\n".join(L) + "\n"


# ------------------------------------------------------------------ writer
def write_project(base: Path, project, script: dict, errors: list, warnings: list, today: str) -> dict:
    out = base / project.name
    out.mkdir(parents=True, exist_ok=True)
    md = script_to_md(script, project)
    (out / "script.md").write_text(md, encoding="utf-8")
    kws = [k["keyword"] for k in script["keyword_report"]]
    (out / "script_keywords_marked.md").write_text(marked_md(md, kws), encoding="utf-8")
    (out / "youtube_upload.md").write_text(upload_md(script, project), encoding="utf-8")
    have = bool(project.footage or any(s.footage for s in project.products))
    write_shot_files(out, shot_rows(script, project, have))
    (out / "video_brief.md").write_text(brief_md(script, project, errors, warnings, today), encoding="utf-8")
    (out / "script.json").write_text(json.dumps(script, indent=2, ensure_ascii=False), encoding="utf-8")
    return {"dir": str(out), "minutes": script["estimated_minutes"], "lines": len(script["segments"])}


def write_blocked(base: Path, project_name: str, errors: list, warnings: list) -> str:
    out = base / project_name
    out.mkdir(parents=True, exist_ok=True)
    L = [f"# {project_name}: not ready", "", "No script was written because of these problems:", "", *[f"- {e}" for e in errors]]
    if warnings:
        L += ["", "Also noted:", *[f"- {w}" for w in warnings]]
    L += ["", "Fix them in facts.yaml / compare.yaml and run again."]
    (out / "NOT_READY.md").write_text("\n".join(L) + "\n", encoding="utf-8")
    return str(out)
