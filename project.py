"""
project.py
----------
Reads the SAME input files your video agent reads, so you copy them across unchanged:

  projects/<ASIN>/facts.yaml        one product (single-product video)
  projects/<NAME>/compare.yaml      2-3 products (comparison video); each product has its own projects/<ASIN>/facts.yaml
  projects/<ASIN>/footage.csv       OPTIONAL: file,description  (what your footage shows, so lines can be matched to it)

Naming, labels and the "may I pick a winner" rule mirror the video agent (adagent/project.py).
List videos ("Top N") are not supported by this agent yet.
"""
from __future__ import annotations

import csv
import re
from dataclasses import dataclass, field
from pathlib import Path

import yaml

ASIN_RE = re.compile(r"^[A-Z0-9][A-Z0-9_-]{2,39}$")
AMAZON_LINK_RE = re.compile(r"^https://((www\.)?amazon\.[a-z.]{2,6}/|amzn\.(to|eu|asia)/|a\.co/)", re.I)
IMG_EXT = {".jpg", ".jpeg", ".png", ".webp", ".bmp"}
VID_EXT = {".mp4", ".mov", ".m4v", ".webm", ".mkv", ".avi"}
PLACEHOLDER_RE = re.compile(r"\bTODO\b|X{5,}", re.I)
FACT_LISTS = ["manufacturer_claims", "specs", "hands_on_notes", "pros", "cons", "not_for"]
FENCE_RE = re.compile(r"^\s*```[A-Za-z0-9_-]*\s*$")


class ProjectError(Exception):
    pass


@dataclass
class Project:
    name: str
    dir: Path
    kind: str                                    # "single" | "compare"
    facts: dict = field(default_factory=dict)
    products: list = field(default_factory=list)  # compare: [Project]
    labels: list = field(default_factory=list)
    pick_ok: bool = False
    footage: list = field(default_factory=list)   # [{"id","file","description"}]
    conf: dict = field(default_factory=dict)


def normalize_keys(d: dict) -> dict:
    def norm(k):
        return re.sub(r"[\s-]+", "_", str(k).strip().lower())
    out = {norm(k): v for k, v in d.items()}
    if len(out) == 1:
        (only,) = out.values()
        if isinstance(only, dict) and any(k in {norm(x) for x in only} for k in ("product_name", "affiliate_url", "asin")):
            out = {norm(k): v for k, v in only.items()}
    return out


def read_yaml(path: Path) -> dict:
    raw = path.read_text(encoding="utf-8-sig")
    text = "\n".join(l for l in raw.splitlines() if not FENCE_RE.match(l))
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as e:
        mark = getattr(e, "problem_mark", None)
        where = f" (line {mark.line + 1})" if mark is not None else ""
        raise ProjectError(f"{path.parent.name}/{path.name} has a formatting error{where}: "
                           f"{getattr(e, 'problem', None) or 'invalid YAML'}")
    if data is None:
        return {}
    if not isinstance(data, dict):
        raise ProjectError(f"{path.parent.name}/{path.name} must contain 'name: value' lines.")
    return data


def as_list(v) -> list:
    if not v:
        return []
    if isinstance(v, str):
        return [x.strip() for x in re.split(r"[\n]", v) if x.strip()]
    return [str(x).strip() for x in v if str(x).strip()]


def keywords_of(v) -> list:
    if not v:
        return []
    items = re.split(r"[,\n]", v) if isinstance(v, str) else list(v)
    out, seen = [], set()
    for k in items:
        k = str(k).strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


def label_of(facts: dict, name: str) -> str:
    return str(facts.get("short_name") or facts.get("brand") or " ".join(str(facts.get("product_name", name)).split()[:2])).strip()


def load_footage(folder: Path, prefix: str = "") -> list:
    """footage.csv: file,description[,id]. Ids follow the video agent: sorted file names -> A01, A02 ...
    (so footage.csv must list EVERY photo/clip in that assets folder). A prefix such as P1- is added for comparisons."""
    f = folder / "footage.csv"
    if not f.exists():
        return []
    rows = []
    with open(f, newline="", encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            name = (r.get("file") or "").strip()
            if name and Path(name).suffix.lower() in (IMG_EXT | VID_EXT):
                rows.append({"file": name, "description": (r.get("description") or "").strip(),
                             "id": (r.get("id") or "").strip().upper()})
    names = [r["file"] for r in rows]
    if len(set(names)) != len(names):
        raise ProjectError(f"{folder.name}/footage.csv lists the same file twice.")
    out = []
    for i, r in enumerate(sorted(rows, key=lambda r: r["file"]), 1):
        out.append({"file": r["file"], "description": r["description"] or Path(r["file"]).stem.replace("_", " "),
                    "id": prefix + (r["id"] or f"A{i:02d}")})
    return out


def _load_single(root: Path, name: str, prefix: str = "") -> Project:
    name = name.strip().upper()
    d = root / "projects" / name
    if not d.exists():
        raise ProjectError(f"Folder projects/{name}/ not found.")
    f = d / "facts.yaml"
    if not f.exists():
        raise ProjectError(f"projects/{name}/facts.yaml is missing.")
    facts = normalize_keys(read_yaml(f))
    facts["keywords"] = keywords_of(facts.get("keywords"))
    for k in FACT_LISTS:
        facts[k] = as_list(facts.get(k))
    return Project(name=name, dir=d, kind="single", facts=facts, labels=[label_of(facts, name)],
                   footage=load_footage(d, prefix))


def load_project(root: Path, name: str) -> Project:
    name = name.strip().upper()
    d = root / "projects" / name
    if not ASIN_RE.match(name):
        raise ProjectError(f"'{name}' is not a valid project name (3-40 letters, digits, - or _).")
    if (d / "list.yaml").exists():
        raise ProjectError(f"projects/{name} is a list project (Top N). This agent supports single and comparison videos only.")
    cfile = d / "compare.yaml"
    if not cfile.exists():
        return _load_single(root, name)

    conf = normalize_keys(read_yaml(cfile))
    ids = []
    for item in conf.get("products") or []:
        ids.append(str((item.get("id") or item.get("asin")) if isinstance(item, dict) else item).strip().upper())
    if not 2 <= len(ids) <= 3:
        raise ProjectError(f"projects/{name}/compare.yaml: 'products' must list 2 to 3 product folders (found {len(ids)}).")
    if len(set(ids)) != len(ids) or "" in ids or name in ids:
        raise ProjectError(f"projects/{name}/compare.yaml: a product is listed twice, empty, or is this folder itself.")
    subs = [_load_single(root, i, prefix=f"P{n}-") for n, i in enumerate(ids, 1)]
    labels = [label_of(s.facts, s.name) for s in subs]
    if len({l.lower() for l in labels}) != len(labels):
        raise ProjectError("Two products have the same short label. Give each facts.yaml a different short_name.")
    kw = keywords_of(conf.get("keywords")) or keywords_of([k for s in subs for k in s.facts["keywords"]])[:12]
    facts = {
        "asin": name,
        "product_name": conf.get("title") or " vs ".join(labels),
        "category": conf.get("category") or subs[0].facts.get("category", ""),
        "audience": conf.get("audience") or subs[0].facts.get("audience", ""),
        "compare_points": as_list(conf.get("compare_points")),
        "notes": str(conf.get("notes", "") or ""),
        "keywords": kw,
    }
    return Project(name=name, dir=d, kind="compare", facts=facts, products=subs, labels=labels,
                   pick_ok=all(len(s.facts["hands_on_notes"]) >= 3 for s in subs),
                   footage=load_footage(d, "X-"), conf=conf)


def list_projects(root: Path) -> list:
    pdir = root / "projects"
    if not pdir.exists():
        return []
    return sorted(p.name for p in pdir.iterdir()
                  if p.is_dir() and not p.name.startswith(("_", ".")) and ((p / "facts.yaml").exists() or (p / "compare.yaml").exists() or (p / "list.yaml").exists()))


def fact_count(facts: dict) -> int:
    return sum(len(facts.get(k) or []) for k in FACT_LISTS)


def validate_single(p: Project) -> tuple:
    f, errors, warnings = p.facts, [], []
    for key in ("product_name", "category", "audience", "affiliate_url"):
        v = str(f.get(key) or "").strip()
        if not v:
            errors.append(f"facts.yaml: '{key}' is required.")
        elif PLACEHOLDER_RE.search(v):
            errors.append(f"facts.yaml: '{key}' still holds a placeholder ({v}). Fill it in.")
    if str(f.get("asin", "")).upper() != p.name:
        errors.append(f"facts.yaml: asin must equal the folder name ({p.name}).")
    url = str(f.get("affiliate_url") or "")
    if url and not PLACEHOLDER_RE.search(url) and not AMAZON_LINK_RE.match(url):
        errors.append("facts.yaml: affiliate_url must be an Amazon link (amazon.*, amzn.to, amzn.eu or a.co).")
    if url and "amazon." in url and "tag=" not in url:
        warnings.append("affiliate_url has no tag=YOURID-20 parameter, so it will not earn commission.")
    for key in ("brand", "short_name"):
        if PLACEHOLDER_RE.search(str(f.get(key) or "")):
            errors.append(f"facts.yaml: '{key}' still holds a placeholder. Fill it in.")
    n = fact_count(f)
    if n == 0:
        errors.append("facts.yaml has no facts (manufacturer_claims, specs, hands_on_notes, pros, cons, not_for). "
                      "The agent will not invent any. Add at least 8 real facts.")
    elif n < 8:
        warnings.append(f"Only {n} facts supplied. A full script needs about 12+ real facts; with fewer, the script stays short or generic.")
    if not f.get("hands_on_notes"):
        warnings.append("No hands_on_notes: the script cannot use first person (\"I\", \"in my use\"), and a listing read-aloud is "
                        "what YouTube's inauthentic-content policy targets. Add your own observations.")
    if len(f.get("keywords") or []) > 30:
        warnings.append("More than 30 keywords: keep the best 10-15.")
    return errors, warnings


def validate(p: Project) -> tuple:
    if p.kind == "single":
        return validate_single(p)
    errors, warnings = [], []
    for s in p.products:
        e, w = validate_single(s)
        errors += [f"{s.name}: {x}" for x in e]
        warnings += [f"{s.name}: {x}" for x in w]
    if not p.facts.get("category"):
        errors.append("compare.yaml: add 'category:' (what kind of product these are).")
    if not p.pick_ok:
        warnings.append("Not every product has 3+ hands_on_notes, so the script stays neutral: no winner, only who each product suits.")
    return errors, warnings
