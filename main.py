"""
main.py
-------
YouTube affiliate agent. For every project folder in projects/ (or the one named in the workflow):

  1. load facts.yaml (single product) or compare.yaml (2-3 products), the SAME files your video agent reads
  2. refuse to write anything if the facts are unfinished (placeholders, no facts, bad affiliate link)
  3. write script.md in the video agent's exact layout, plus title, description, tags, hashtags,
     comparison points, a recommendation and a shot list
  4. check every line with the video agent's own content rules

Nothing is uploaded, rendered or spent.
"""
from __future__ import annotations

import csv
import json
import os
import sys
from datetime import date
from pathlib import Path

import yaml

import exporters
import gen
import llm
import project as proj

ROOT = Path(__file__).parent


def load_config(path: Path = ROOT / "config.yaml") -> dict:
    cfg = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg.setdefault("length", "short")
    cfg.setdefault("voice", {})
    for k, v in {"max_exclamations": 2, "min_words_per_line": 4, "max_words_per_line": 34, "style_file": "style_example.txt"}.items():
        cfg["voice"].setdefault(k, v)
    cfg.setdefault("keywords", {}).setdefault("secondary_max", 4)
    cfg.setdefault("llm", {"model": "openai/gpt-oss-120b", "max_tokens": 6000})
    cfg.setdefault("timing", {})
    for k, v in {"wpm": 155, "pause_sentence": 0.40, "pause_section": 0.75, "lead_in": 0.8, "tail": 4.0, "outro_words": 22,
                 "disclosure_mode": "description_only", "disclosure_words": 24}.items():
        cfg["timing"].setdefault(k, v)
    cfg.setdefault("length_presets", {
        "review": {"min_minutes": 3.5, "max_minutes": 8.0, "target_minutes": 5.0},
        "compare": {"min_minutes": 5.5, "max_minutes": 8.8, "target_minutes": 7.5},
        "short": {"min_minutes": 1.5, "max_minutes": 4.8, "target_minutes": 3.0}})
    if os.environ.get("LENGTH") in ("short", "auto"):
        cfg["length"] = os.environ["LENGTH"]
    return cfg


def run(root: Path = ROOT, cfg: dict | None = None, only: str | None = None, out_base: Path | None = None,
        today: str | None = None) -> dict:
    cfg = cfg or load_config(root / "config.yaml")
    today = today or date.today().isoformat()
    out_base = out_base or root / "output"
    only = (only if only is not None else os.environ.get("PROJECT", "all")).strip()
    style_path = root / cfg["voice"]["style_file"]
    style_text = style_path.read_text(encoding="utf-8") if style_path.exists() else ""

    names = proj.list_projects(root) if only.lower() in ("", "all") else [only.upper()]
    if not names:
        print("No projects found: put facts.yaml or compare.yaml folders in projects/.")
        return {"status": "failed: no projects", "results": []}

    results = []
    for name in names:
        r = {"project": name}
        try:
            p = proj.load_project(root, name)
            errors, warnings = proj.validate(p)
            if errors:
                r.update(status="blocked", detail="; ".join(errors), dir=exporters.write_blocked(out_base, name, errors, warnings))
            else:
                script = gen.make_script(p, cfg, style_text)
                w = exporters.write_project(out_base, p, script, errors, warnings, today)
                r.update(status="success", dir=w["dir"], minutes=w["minutes"], lines=w["lines"],
                         keyword_issues=len(script["keyword_issues"]), notes=len(script["notes"]))
        except llm.LLMUnavailable as e:
            r.update(status="failed", detail=f"{e}. Add GROQ_API_KEY as an Actions secret.")
        except (proj.ProjectError, RuntimeError, ValueError) as e:
            r.update(status="failed", detail=str(e))
        results.append(r)
        print(f"{name}: {r['status']}" + (f" ({r.get('minutes')} min, {r.get('lines')} lines)" if r["status"] == "success" else f" - {r.get('detail', '')[:200]}"))

    out_base.mkdir(parents=True, exist_ok=True)
    with open(out_base / "summary.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["project", "status", "minutes", "lines", "keyword_issues", "detail"])
        for r in results:
            w.writerow([r["project"], r["status"], r.get("minutes", ""), r.get("lines", ""), r.get("keyword_issues", ""), r.get("detail", "")])
    ok = sum(1 for r in results if r["status"] == "success")
    summary = {"status": "success" if ok == len(results) else ("partial" if ok else "failed"), "processed": ok,
               "total": len(results), "results": results}
    (root / "run_summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary


if __name__ == "__main__":
    s = run()
    sys.exit(0 if s["status"] in ("success", "partial") else 1)
