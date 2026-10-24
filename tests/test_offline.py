"""
Offline tests: no network, no API key.   Run:  python tests/test_offline.py
The model is faked with canned answers (including deliberately bad lines), so these tests prove the validation,
repair, length fitting and export logic and the compatibility with your video agent's own code,
not the quality of real Groq narration.
"""
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import exporters  # noqa: E402
import gen  # noqa: E402
import llm  # noqa: E402
import main  # noqa: E402
import project as proj  # noqa: E402
import rules  # noqa: E402

VIDEO_AGENT = ROOT.parent / "src" / "amazon-ad-agent"        # your uploaded video agent (for the compatibility test)

FACTS_A = """asin: B0TEST0001
product_name: "FrostGuard Under Door Draft Stopper 36 Inch"
brand: FrostGuard
short_name: FrostGuard
category: door draft stopper
audience: renters with drafty doors
affiliate_url: https://amzn.to/abc123
keywords: [door draft stopper, seals gaps under doors, stays in place, waterproof seal]
manufacturer_claims: ["Foam core fits gaps up to 2 inches", "Adhesive strip, no tools needed", "Seals gaps under doors"]
specs: ["Length: 36 inches", "Material: foam"]
hands_on_notes: ["stayed in place on a painted bedroom door", "foam compresses slightly when the door closes", "floor near the door felt less chilly", "adhesive did not grip well on a rough textured door"]
pros: ["easy to cut to length", "no drilling"]
cons: ["adhesive weak on rough doors"]
not_for: ["gaps wider than 2 inches"]
"""

FACTS_B = """asin: B0TEST0002
product_name: "SnugSeal Brush Door Sweep 40 Inch"
brand: SnugSeal
short_name: SnugSeal
category: door draft stopper
audience: renters with drafty doors
affiliate_url: https://amzn.to/def456
keywords: [door draft stopper]
manufacturer_claims: ["Brush strip covers gaps up to 3 inches", "Screw-free adhesive mount"]
specs: ["Length: 40 inches", "Material: nylon bristles"]
hands_on_notes: ["bristles follow an uneven floor", "mounting took one attempt", "brush looks bulky on a thin door"]
pros: ["handles uneven floors"]
cons: ["looks bulky"]
not_for: ["glass shower doors"]
"""

COMPARE = """title: "FrostGuard vs SnugSeal: door draft stoppers compared"
products: [B0TEST0001, B0TEST0002]
category: door draft stopper
audience: renters with drafty doors
keywords: [door draft stopper]
compare_points: ["gap size", "mounting"]
"""


def make_repo(facts_b=FACTS_B, with_compare=True, footage=False, fix_style=True):
    root = Path(tempfile.mkdtemp())
    (root / "projects").mkdir()
    shutil.copy(ROOT / "config.yaml", root / "config.yaml")
    shutil.copy(ROOT / "style_example.txt", root / "style_example.txt")
    for name, text in (("B0TEST0001", FACTS_A), ("B0TEST0002", facts_b)):
        d = root / "projects" / name
        d.mkdir()
        (d / "facts.yaml").write_text(text)
    if with_compare:
        d = root / "projects" / "COMPARE-DRAFTS"
        d.mkdir()
        (d / "compare.yaml").write_text(COMPARE)
    if footage:
        (root / "projects" / "B0TEST0001" / "footage.csv").write_text(
            "file,description\n02_door_gap.jpg,close up of a door gap\n01_front.jpg,product on white\nclip_install.mp4,applying the strip\n")
    return root


# ------------------------------------------------------------------ fake model
def seg(section, text, source="general", kws=(), p=2, prod="all", assets=(), visual="the product on the door"):
    return {"section": section, "text": text, "source": source, "keywords": list(kws), "priority": p, "product": prod,
            "asset_ids": list(assets), "visual": visual, "onscreen": ""}


GOOD_SINGLE = [
    seg("hook", "Cold air creeping in under your door, and the whole room feels colder than it should?", p=1),
    seg("hook", "A door draft stopper is a simple way to tackle that.", kws=["door draft stopper"], p=1),
    seg("context", "Gaps at the bottom of a door let cold air slip in, and that is where much of the chill comes from.", p=2),
    seg("overview", "Meet the FrostGuard, a foam door draft stopper that comes in a 36 inch length.", "spec", ["door draft stopper"], p=1),
    seg("overview", "The brand says the foam core fits gaps up to 2 inches.", "claim", p=1),
    seg("features", "It sticks on with an adhesive strip, so there's no drilling and no tools needed, according to the listing.", "claim", p=1, assets=["A02"]),
    seg("features", "That makes it a good fit if you rent and can't make holes in your door.", p=2),
    seg("features", "In my use, it stayed in place on a painted bedroom door, which is exactly what you want!", "handson", ["stays in place"], p=1, assets=["A01"]),
    seg("features", "The foam compresses slightly when the door closes, so it seals gaps under doors without a fight.", "handson", ["seals gaps under doors"], p=2),
    seg("features", "It's easy to cut to length, so you can trim it to your door.", "spec", p=3),
    seg("in_use", "I put it on a bedroom door, and the floor near it felt less chilly.", "handson", p=2),
    seg("in_use", "If you've got a thick rug in front of the door, check the gap before you stick anything down.", p=3),
    seg("limits", "One downside: the adhesive did not grip well on a rough textured door.", "handson", p=1),
    seg("who_for", "If you're renting and want a no-tools fix, it's worth a look.", p=2),
    seg("who_skip", "If your gap is wider than 2 inches, this probably isn't the one for you.", "claim", p=2),
    seg("verdict", "So that's the FrostGuard: a simple, no-tools door draft stopper for smaller gaps!!", p=1),
    seg("verdict", "Take a look if that sounds like your door.", p=1),
]
BAD_LINES = [
    seg("features", "It's only $19.99 right now, a total steal.", "claim", p=2),
    seg("features", "We built our foam core to last.", "claim", p=2),
    seg("features", "I tested it for 30 days straight.", "handson", p=2),
    seg("features", "It's a completely waterproof seal.", "claim", p=2),
]
GOOD_BENEFITS = [{"benefit": "No tools needed", "fact": "Adhesive strip, no tools needed"},
                 {"benefit": "Invented benefit", "fact": "Self heating and quiet"}]


def single_json(bad=False, title="Door Draft Stopper: FrostGuard Hands-On First Look"):
    segs = [dict(s) for s in GOOD_SINGLE]
    if bad:
        i = next(k for k, s in enumerate(segs) if s["section"] == "features")
        segs[i:i] = [dict(s) for s in BAD_LINES]
    return {"title": title, "description_intro": "A calm hands-on look at a door draft stopper for renters. We cover how it attaches and where it struggles.",
            "tags": ["door draft stopper", "draft stopper for renters", "renter friendly", "door seal", "foam draft stopper",
                     "winter home tips", "cheap draft stopper", "stays in place"],
            "hashtags": ["#DoorDraftStopper", "#Renters", "#WinterHome", "bad tag!"],
            "segments": segs, "benefits": GOOD_BENEFITS,
            "recommendation": "Worth a look if you rent and have a small gap. Skip it if your gap is wider than 2 inches."}


def compare_json(bad=False, winner=False):
    S = [
        seg("hook", "Two ways to stop a door draft, and they work very differently.", p=1),
        seg("hook", "Let's see which door draft stopper suits your door.", kws=["door draft stopper"], p=1),
        seg("context", "Before you buy anything, measure the gap and check the floor.", p=2),
        seg("overview", "FrostGuard is a foam strip with an adhesive back.", "spec", p=1, prod="1"),
        seg("overview", "SnugSeal is a brush sweep with nylon bristles.", "spec", p=1, prod="2"),
        seg("product_1", "The brand says FrostGuard's foam core fits gaps up to 2 inches.", "claim", p=1, prod="1"),
        seg("product_1", "In my use it stayed in place on a painted bedroom door.", "handson", p=2, prod="1"),
        seg("product_2", "SnugSeal's brush strip covers gaps up to 3 inches, the brand says.", "claim", p=1, prod="2"),
        seg("product_2", "The bristles follow an uneven floor, which I liked.", "handson", p=2, prod="2"),
        seg("head_to_head", "On mounting, both use an adhesive back, so neither needs screws.", p=2),
        seg("who_for", "FrostGuard suits smaller gaps; SnugSeal suits uneven floors.", p=1),
        seg("verdict", "So pick by your door: small gap, FrostGuard; uneven floor, SnugSeal.", p=1),
    ]
    if winner:
        S.insert(10, seg("head_to_head", "SnugSeal beats FrostGuard here, hands down.", p=2))
    if bad:
        S.insert(6, seg("product_2", "SnugSeal also fits gaps up to 2 inches.", "claim", p=2, prod="2"))     # 2 inches belongs to product 1
    return {"title": "FrostGuard vs SnugSeal: Door Draft Stopper Compared",
            "description_intro": "A fair look at two door draft stopper styles for renters. We compare gap size and mounting.",
            "tags": ["door draft stopper", "frostguard vs snugseal", "draft stopper comparison", "door sweep", "renter friendly",
                     "winter home tips", "foam vs brush", "door seal"],
            "hashtags": ["#DoorDraftStopper", "#Comparison", "#Renters"], "segments": S,
            "comparison_points": [
                {"point": "Gap size", "facts": {"1": "Foam core fits gaps up to 2 inches", "2": "Brush strip covers gaps up to 3 inches"}},
                {"point": "Invented point", "facts": {"1": "Foam core fits gaps up to 2 inches", "2": "Heated bristles"}},
                {"point": "Only one side", "facts": {"1": "Length: 36 inches"}}],
            "recommendation": "Pick FrostGuard for a small gap and SnugSeal for an uneven floor."}


class FakeLLM:
    def __init__(self, bad=False, stubborn=False, compare_bad=False, winner=False, short=False):
        self.calls, self.prompts = [], []
        self.bad, self.stubborn, self.compare_bad, self.winner, self.short = bad, stubborn, compare_bad, winner, short

    def __call__(self, prompt, model, max_tokens, temperature=0.3):
        self.prompts.append(prompt)
        repair = "YOUR PREVIOUS ANSWER" in prompt
        self.calls.append("repair" if repair else "first")
        if "comparison_points" in prompt:
            return json.dumps(compare_json(bad=self.compare_bad and (not repair or self.stubborn), winner=self.winner and (not repair or self.stubborn)))
        data = single_json(bad=self.bad and (not repair or self.stubborn))
        if self.short:
            data["segments"] = data["segments"][:3]
        return json.dumps(data)


def with_llm(fake, fn):
    saved = llm.chat
    llm.chat = fake
    try:
        return fn()
    finally:
        llm.chat = saved


def run_proj(root, name, fake, cfg=None):
    out = Path(tempfile.mkdtemp())
    c = cfg or main.load_config(root / "config.yaml")
    s = with_llm(fake, lambda: main.run(root, cfg=c, only=name, out_base=out, today="2026-10-24"))
    return s, out


# ------------------------------------------------------------------ rules
def test_rules_match_video_agent():
    assert rules.check_text("Only $19.99 right now")[0] == ["mentions a price"]
    assert rules.check_text("customers say it is great")[0] == ["paraphrases customer reviews"]
    assert rules.check_text("a real game changer")[1] == ["hype phrase"]
    assert rules.check_text("It blocks drafts under the door.") == ([], [])
    if VIDEO_AGENT.exists():                       # identical lists, not just similar ones
        sys.path.insert(0, str(VIDEO_AGENT))
        import importlib
        vp = importlib.import_module("adagent.project")
        assert vp.POLICY_RULES == rules.POLICY_RULES and vp.STYLE_RULES == rules.STYLE_RULES
        sys.path.remove(str(VIDEO_AGENT))


def test_clean_text_and_numbers():
    assert rules.clean_text("**Hi** | there 😀 now") == "Hi, there now"
    assert rules.numbers_ok("fits 2 inches", "Foam core fits gaps up to 2 inches")
    assert not rules.numbers_ok("fits 3 inches", "Length: 36 inches and 2 inches")        # "3" must not match "36"
    assert rules.FIRST_PERSON.search("In my use it held") and not rules.FIRST_PERSON.search("It holds")
    assert rules.BRAND_VOICE.search("our foam") and not rules.BRAND_VOICE.search("a house")


def test_keyword_helpers():
    blob = "Foam core fits gaps up to 2 inches. Seals gaps under doors. door draft stopper"
    assert rules.keyword_supported("door draft stopper", blob)
    assert rules.keyword_supported("seals gaps under doors", blob)
    assert not rules.keyword_supported("waterproof seal", blob)
    assert rules.mark_keywords("A door draft stopper helps. Door-draft stopper too.", ["door draft stopper"]) == \
        "A *door draft stopper* helps. *Door-draft stopper* too."
    assert rules.count_phrase("Door-draft stopper!", "door draft stopper") == 1


# ------------------------------------------------------------------ project loading
def test_load_projects_and_footage_ids():
    root = make_repo(footage=True)
    p = proj.load_project(root, "B0TEST0001")
    assert p.kind == "single" and p.facts["keywords"][0] == "door draft stopper"
    assert [(f["id"], f["file"]) for f in p.footage] == [("A01", "01_front.jpg"), ("A02", "02_door_gap.jpg"), ("A03", "clip_install.mp4")]
    c = proj.load_project(root, "COMPARE-DRAFTS")
    assert c.kind == "compare" and c.labels == ["FrostGuard", "SnugSeal"] and c.pick_ok is True
    assert proj.list_projects(root) == ["B0TEST0001", "B0TEST0002", "COMPARE-DRAFTS"]


def test_unfinished_skeleton_is_blocked():
    skeleton = FACTS_A.replace("brand: FrostGuard", 'brand: "TODO"').replace("https://amzn.to/abc123", "https://amzn.to/XXXXXXX")
    root = make_repo()
    (root / "projects" / "B0TEST0001" / "facts.yaml").write_text(skeleton)
    s, out = run_proj(root, "B0TEST0001", FakeLLM())
    assert s["results"][0]["status"] == "blocked"
    assert "placeholder" in (out / "B0TEST0001" / "NOT_READY.md").read_text()
    empty = "asin: B0TEST0001\nproduct_name: X thing\ncategory: c\naudience: a\naffiliate_url: https://amzn.to/abc\n"
    (root / "projects" / "B0TEST0001" / "facts.yaml").write_text(empty)
    s, _ = run_proj(root, "B0TEST0001", FakeLLM())
    assert s["results"][0]["status"] == "blocked" and "no facts" in s["results"][0]["detail"]


def test_list_project_is_refused():
    root = make_repo(with_compare=False)
    d = root / "projects" / "TOP-LIST"
    d.mkdir()
    (d / "list.yaml").write_text("products: [B0TEST0001]\n")
    s, _ = run_proj(root, "TOP-LIST", FakeLLM())
    assert s["results"][0]["status"] == "failed" and "list project" in s["results"][0]["detail"]


# ------------------------------------------------------------------ single product script
def test_single_script_clean_first_time():
    root = make_repo(footage=True)
    fake = FakeLLM()
    s, out = run_proj(root, "B0TEST0001", fake)
    assert s["status"] == "success" and fake.calls == ["first"]                  # nothing to repair, no wasted call
    sc = json.loads((out / "B0TEST0001" / "script.json").read_text())
    assert [x["id"] for x in sc["segments"]] == [str(i) for i in range(1, len(sc["segments"]) + 1)]
    sections = [x["section"] for x in sc["segments"]]
    assert sections == sorted(sections, key=gen.SECTIONS_SINGLE.index)            # canonical section order
    text = " ".join(x["text"] for x in sc["segments"])
    assert "*" not in text and "|" not in text
    assert text.count("!") <= 2 and "!." not in text and "!!" not in text         # limit respected, no ugly leftovers
    assert sc["segments"][7]["asset_ids"] == ["A01"]                              # real footage ids kept
    assert all(a in ("A01", "A02", "A03") for x in sc["segments"] for a in x["asset_ids"])


def test_bad_lines_are_repaired_then_removed_if_stubborn():
    root = make_repo()
    fake = FakeLLM(bad=True)
    s, out = run_proj(root, "B0TEST0001", fake)
    assert fake.calls == ["first", "repair"]
    assert any("mentions a price" in p and "speaks as the brand" in p for p in [fake.prompts[1]])      # exact problems sent back
    text = " ".join(x["text"] for x in json.loads((out / "B0TEST0001" / "script.json").read_text())["segments"])
    for banned in ("19.99", "We built", "30 days", "waterproof"):
        assert banned not in text

    stubborn = FakeLLM(bad=True, stubborn=True)
    s, out = run_proj(root, "B0TEST0001", stubborn)
    sc = json.loads((out / "B0TEST0001" / "script.json").read_text())
    text = " ".join(x["text"] for x in sc["segments"])
    for banned in ("19.99", "We built", "30 days", "waterproof"):
        assert banned not in text                                                  # nothing unsafe is ever shipped
    assert len(sc["dropped"]) == 4 and any("removed" in n for n in sc["notes"])


def test_no_hands_on_notes_means_no_first_person():
    root = make_repo()
    facts = "\n".join(l for l in FACTS_A.splitlines() if not l.startswith("hands_on_notes"))
    (root / "projects" / "B0TEST0001" / "facts.yaml").write_text(facts)
    s, out = run_proj(root, "B0TEST0001", FakeLLM())
    sc = json.loads((out / "B0TEST0001" / "script.json").read_text())
    assert not any(rules.FIRST_PERSON.search(x["text"]) for x in sc["segments"])
    brief = (out / "B0TEST0001" / "video_brief.md").read_text()
    assert "No hands_on_notes" in brief


def test_keywords_unsupported_are_kept_out():
    root = make_repo()
    fake = FakeLLM()
    s, out = run_proj(root, "B0TEST0001", fake)
    sc = json.loads((out / "B0TEST0001" / "script.json").read_text())
    assert "waterproof seal" in sc["unusable_keywords"]
    assert "waterproof seal" not in sc["tags"] and "waterproof seal" not in sc["description_intro"].lower()
    assert "WATERPROOF SEAL" not in fake.prompts[0].split("KEYWORDS you may use")[1].split("KEYWORDS you must NOT")[0].upper()
    up = (out / "B0TEST0001" / "youtube_upload.md").read_text()
    assert "Keywords NOT used" in up and "waterproof seal" in up
    rep = {k["keyword"]: k for k in sc["keyword_report"]}
    assert rep["door draft stopper"]["in_title"] and rep["door draft stopper"]["in_first_5_lines"] and rep["door draft stopper"]["narration_count"] >= 2
    assert sc["hashtags"][:3] == ["#DoorDraftStopper", "#Renters", "#WinterHome"]
    assert all(h.startswith("#") and h[1:].isalnum() for h in sc["hashtags"]) and len(sc["hashtags"]) <= 5      # "bad tag!" became a clean tag


def test_extras_must_be_backed_by_facts():
    root = make_repo()
    s, out = run_proj(root, "B0TEST0001", FakeLLM())
    ex = json.loads((out / "B0TEST0001" / "script.json").read_text())["extras"]
    assert [b["benefit"] for b in ex["benefits"]] == ["No tools needed"]            # the invented one is dropped
    assert ex["recommendation"]


def test_marked_copy_is_separate_and_only_for_review():
    root = make_repo()
    s, out = run_proj(root, "B0TEST0001", FakeLLM())
    plain = (out / "B0TEST0001" / "script.md").read_text()
    marked = (out / "B0TEST0001" / "script_keywords_marked.md").read_text()
    assert "*" not in plain and "*door draft stopper*" in marked.lower()
    assert marked.replace("*", "") == plain


def test_shot_list_flags_missing_footage():
    root = make_repo(footage=True)
    s, out = run_proj(root, "B0TEST0001", FakeLLM())
    rows = (out / "B0TEST0001" / "shot_list.csv").read_text()
    assert "matched" in rows and "film or capture the visual" in rows
    root2 = make_repo()
    s, out2 = run_proj(root2, "B0TEST0001", FakeLLM())
    assert "add footage.csv" in (out2 / "B0TEST0001" / "shot_list.csv").read_text()


def test_length_cut_and_short_warning():
    root = make_repo()
    cfg = main.load_config(root / "config.yaml")
    cfg["length_presets"]["short"] = {"min_minutes": 1.0, "max_minutes": 1.6, "target_minutes": 1.3}
    s, out = run_proj(root, "B0TEST0001", FakeLLM(), cfg)
    sc = json.loads((out / "B0TEST0001" / "script.json").read_text())
    assert sc["estimated_minutes"] <= 1.6 and sc["cut"]
    assert sc["segments"][-1]["section"] == "verdict" and sc["segments"][0]["section"] == "hook"
    s, out = run_proj(root, "B0TEST0001", FakeLLM(short=True))
    notes = json.loads((out / "B0TEST0001" / "script.json").read_text())["notes"]
    assert any("needs at least" in n for n in notes)


# ------------------------------------------------------------------ comparison
def test_compare_script_blends_nothing():
    root = make_repo()
    fake = FakeLLM(compare_bad=True)
    s, out = run_proj(root, "COMPARE-DRAFTS", fake)
    assert s["status"] == "success" and fake.calls == ["first", "repair"]
    sc = json.loads((out / "COMPARE-DRAFTS" / "script.json").read_text())
    assert not any("also fits gaps up to 2 inches" in x["text"] for x in sc["segments"])      # product 1's number on product 2: gone
    for x in sc["segments"]:
        if x["section"].startswith("product_"):
            assert x["product"] == x["section"][-1]
    pts = sc["extras"]["comparison_points"]
    assert [p["point"] for p in pts] == ["Gap size"]                                          # invented / one-sided points dropped
    md = (out / "COMPARE-DRAFTS" / "script.md").read_text()
    assert "# PRODUCTS: 1=FrostGuard (B0TEST0001); 2=SnugSeal (B0TEST0002)" in md and "| product: 1 |" in md


def test_winner_needs_three_hands_on_notes_each():
    thin = FACTS_B.replace('hands_on_notes: ["bristles follow an uneven floor", "mounting took one attempt", "brush looks bulky on a thin door"]',
                           'hands_on_notes: ["bristles follow an uneven floor"]')
    root = make_repo(facts_b=thin)
    assert proj.load_project(root, "COMPARE-DRAFTS").pick_ok is False
    s, out = run_proj(root, "COMPARE-DRAFTS", FakeLLM(winner=True, stubborn=True))
    sc = json.loads((out / "COMPARE-DRAFTS" / "script.json").read_text())
    assert not any(rules.WINNER_WORDS.search(x["text"]) for x in sc["segments"])
    assert "NO. The script stays neutral" in (out / "COMPARE-DRAFTS" / "video_brief.md").read_text()
    # with enough notes the same words are not blocked by the pick rule (style warning only)
    root2 = make_repo()
    s, out2 = run_proj(root2, "COMPARE-DRAFTS", FakeLLM(winner=True, stubborn=True))
    assert s["status"] == "success"


# ------------------------------------------------------------------ failures
def test_no_api_key_fails_clearly_without_writing_a_script():
    root = make_repo()

    def no_key(*a, **k):
        raise llm.LLMUnavailable("GROQ_API_KEY is not set")
    s, out = run_proj(root, "B0TEST0001", no_key)
    assert s["status"] == "failed" and "GROQ_API_KEY" in s["results"][0]["detail"]
    assert not (out / "B0TEST0001" / "script.md").exists()


# ------------------------------------------------------------------ compatibility with the REAL video agent
COMPAT = r'''
import json, sys
from adagent import project as P, scriptgen as S, common as C
name, preset = sys.argv[1], sys.argv[2]
p = P.load_project(name)
errors, warnings = P.validate_facts(p)
md = (p.dir / "script.md").read_text()
script = S.md_to_script(md)
cfg = C.load_config()
cfg["length"].update(cfg["length_presets"][preset])
segs = S.assemble(script, p, cfg)
bad = []
for s in segs:
    pol, _ = P.check_text(s["text"] + " " + s.get("onscreen", ""))
    if pol:
        bad.append([s["id"], pol])
est = S.estimate_minutes(segs, cfg)
script["review"] = None
regen = S.script_to_md(script, p)
print(json.dumps({"errors": errors, "bad": bad, "est": est, "lo": cfg["length"]["min_minutes"], "hi": cfg["length"]["max_minutes"],
                  "same_layout": regen.split("\n<!--")[0].strip() == md.split("\n<!--")[0].strip(), "n": len(script["segments"]),
                  "sections": [s["section"] for s in script["segments"]]}))
'''


def _video_check(project_name, out, root, preset):
    tmp = Path(tempfile.mkdtemp()) / "video"
    shutil.copytree(VIDEO_AGENT, tmp, ignore=shutil.ignore_patterns(".git", "__pycache__", "tests", "notebooks"))
    for d in (root / "projects").iterdir():
        if (d / "facts.yaml").exists() or (d / "compare.yaml").exists():
            shutil.copytree(d, tmp / "projects" / d.name, dirs_exist_ok=True)
    shutil.copy(out / project_name / "script.md", tmp / "projects" / project_name / "script.md")
    r = subprocess.run([sys.executable, "-c", COMPAT, project_name, preset], cwd=tmp, capture_output=True, text=True)
    assert r.returncode == 0, r.stderr[-1500:]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_script_md_is_read_by_the_real_video_agent():
    if not VIDEO_AGENT.exists():
        print("  (skipped: video agent folder not found)")
        return
    root = make_repo(footage=True)
    s, out = run_proj(root, "B0TEST0001", FakeLLM(bad=True))
    r = _video_check("B0TEST0001", out, root, "short")
    assert r["same_layout"], "script.md layout differs from the video agent's own writer"
    assert r["bad"] == [] and r["n"] >= 12
    assert abs(r["est"] - json.loads((out / "B0TEST0001" / "script.json").read_text())["estimated_minutes"]) < 0.25
    assert r["lo"] <= r["est"] <= r["hi"]
    assert r["sections"][0] == "hook" and r["sections"][-1] == "verdict"
    assert not [e for e in r["errors"] if "required" in e or "asin" in e]


def test_compare_script_md_is_read_by_the_real_video_agent():
    if not VIDEO_AGENT.exists():
        print("  (skipped: video agent folder not found)")
        return
    root = make_repo()
    cfg = main.load_config(root / "config.yaml")
    cfg["length"] = "auto"
    cfg["length_presets"]["compare"] = {"min_minutes": 1.0, "max_minutes": 8.8, "target_minutes": 3.0}
    s, out = run_proj(root, "COMPARE-DRAFTS", FakeLLM(), cfg)
    r = _video_check("COMPARE-DRAFTS", out, root, "short")
    assert r["same_layout"] and r["bad"] == []
    assert all("has no photos/clips" in e for e in r["errors"])          # the only complaint is about missing footage files


if __name__ == "__main__":
    tests = [(n, f) for n, f in sorted(globals().items()) if n.startswith("test_") and callable(f)]
    failed = 0
    for name, fn in tests:
        try:
            fn()
            print(f"PASS  {name}")
        except Exception as e:  # noqa: BLE001
            failed += 1
            import traceback
            print(f"FAIL  {name}: {e!r}")
            traceback.print_exc()
    print(f"\n{len(tests) - failed}/{len(tests)} passed")
    sys.exit(1 if failed else 0)
