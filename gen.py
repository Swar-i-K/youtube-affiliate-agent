"""
gen.py
------
Writes the narration with the free Groq model, then makes Python the judge:

  * every line is checked with the video agent's own content rules (a policy hit blocks its render step),
  * numbers must come from the facts (for a comparison: from THAT product's facts only),
  * first person ("I", "my") needs hands_on_notes; "we/our" is never allowed (you are not the brand),
  * keywords the facts do not support are kept out of the title and narration,
  * one repair round goes back to the model with the exact problems; lines that still fail are dropped,
  * the script is fitted to the video agent's length window (lowest-priority lines are cut first).

The model never decides which footage exists, what a number is, or whether a line is allowed.
"""
from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path

import llm
import rules

SECTIONS_SINGLE = ["hook", "context", "overview", "features", "in_use", "limits", "who_for", "who_skip", "verdict"]
SECTIONS_COMPARE = ["hook", "context", "overview", "product_1", "product_2", "product_3", "head_to_head", "who_for", "verdict"]
SOURCES = ("claim", "spec", "handson", "general")


def word_count(text: str) -> int:           # same counter as the video agent (adagent/common.py)
    return len(re.findall(r"[A-Za-z0-9'’\-]+", text))


# ------------------------------------------------------------------ length
def preset_for(project, cfg: dict) -> tuple:
    presets = cfg["length_presets"]
    key = "short" if cfg.get("length") == "short" else ("compare" if project.kind == "compare" else "review")
    return key, presets[key]


def estimate_minutes(segs: list, cfg: dict) -> float:
    """Same formula as the video agent's estimate_minutes, including the automatic sign-off (and the spoken disclosure in full mode)."""
    t = cfg["timing"]
    body = list(segs)
    if t.get("disclosure_mode") == "full":
        n_hook = 0
        for x in body:
            if x["section"] != "hook":
                break
            n_hook += 1
        n_hook = max(1, min(n_hook, 3))
        body = body[:n_hook] + [{"section": "disclosure", "text": "x " * t.get("disclosure_words", 24)}] + body[n_hook:]
    allsegs = body + [{"section": "outro", "text": "x " * t.get("outro_words", 22)}]
    words = sum(word_count(x["text"]) for x in allsegs)
    secs = words / (t["wpm"] / 60.0) + len(allsegs) * t["pause_sentence"]
    prev = None
    for x in allsegs:
        if prev and x["section"] != prev:
            secs += t["pause_section"]
        prev = x["section"]
    return (secs + t["lead_in"] + t["tail"]) / 60.0


def target_words(cfg: dict, target_minutes: float, n_sections: int = 8) -> tuple:
    t = cfg["timing"]
    words = 400
    for _ in range(4):
        lines = max(8, int(words / 15))
        secs = target_minutes * 60 - t["lead_in"] - t["tail"] - lines * t["pause_sentence"] - n_sections * t["pause_section"]
        words = max(120, int(secs * t["wpm"] / 60.0) - t.get("outro_words", 22))
    return words, max(8, int(words / 15))


# ------------------------------------------------------------------ prompts
def _facts_single(f: dict) -> dict:
    return {k: f.get(k) for k in ("product_name", "brand", "short_name", "category", "audience", "manufacturer_claims",
                                  "specs", "hands_on_notes", "pros", "cons", "not_for") if f.get(k)}


def build_context(project) -> dict:
    """Everything the prompt and the validators need, in one place."""
    if project.kind == "single":
        facts_blob = rules.facts_text(project.facts)
        items = {"1": rules.fact_items(project.facts)}
        handson = {"1": len(project.facts["hands_on_notes"])}
    else:
        facts_blob = " \n ".join(rules.facts_text(s.facts) for s in project.products) + " \n " + " ".join(project.facts.get("compare_points", []))
        items = {str(i): rules.fact_items(s.facts) for i, s in enumerate(project.products, 1)}
        handson = {str(i): len(s.facts["hands_on_notes"]) for i, s in enumerate(project.products, 1)}
    kws = project.facts.get("keywords", [])
    usable = [k for k in kws if rules.keyword_supported(k, facts_blob)]
    unusable = [k for k in kws if k not in usable]
    return {"facts_blob": facts_blob, "items": items, "handson": handson, "usable": usable, "unusable": unusable,
            "footage_ids": {f["id"]: f["description"] for f in project.footage + [x for s in project.products for x in s.footage]}}


def build_prompt(project, cfg: dict, ctx: dict, style_text: str, n_words: int, n_lines: int) -> str:
    v = cfg["voice"]
    primary = ctx["usable"][0] if ctx["usable"] else ""
    secondary = ctx["usable"][1: 1 + cfg["keywords"]["secondary_max"]]
    footage = "\n".join(f"{i}: {d}" for i, d in ctx["footage_ids"].items()) or "(none provided: use [] for asset_ids)"
    year = date.today().year
    if project.kind == "single":
        facts = json.dumps(_facts_single(project.facts), ensure_ascii=False, indent=1)
        sections = ("Section order and what each does (use these exact section names):\n"
                    "  hook (2-3 lines): the viewer's everyday problem, in their own words. No greeting. Start in the middle of a thought.\n"
                    "  context (1-3 lines): why the problem matters; general knowledge only.\n"
                    "  overview (2-4 lines): the solution: what this product is and what it is designed to do (attribute the brand's words).\n"
                    "  features (5-10 lines): HOW it works and the practical benefit of each feature, one idea per line, each tied to a fact.\n"
                    "  in_use (2-5 lines): realistic use cases from the facts and hands_on_notes; general use cases only as 'if you...'.\n"
                    "  limits (1-3 lines): honest limits from cons/not_for. Skip only if there is nothing real to say.\n"
                    "  who_for (1-2 lines) and who_skip (1-2 lines, only if not_for or cons support it).\n"
                    "  verdict (2-3 lines): the enthusiastic close: who should take a look and why, tied to facts. No sign-off, no 'link below' line.")
        schema_extra = '"benefits": [{"benefit": "<short>", "fact": "<copied from a FACTS item>"}],'
        rec_hint = "<2-3 sentences: who should take a look and who should skip, tied to facts>"
        prod_rule = ""
    else:
        facts = json.dumps([{"label": lab, **_facts_single(s.facts)} for lab, s in zip(project.labels, project.products)],
                           ensure_ascii=False, indent=1)
        n = len(project.products)
        sections = (f"Section order (use these exact names): hook (2-3 lines: the question a buyer is asking, no greeting), context (what to look for), "
                    f"overview (one line per product: its label and what it is), "
                    + ", ".join(f"product_{i}" for i in range(1, n + 1)) +
                    " (each 4-8 lines: key facts, hands-on notes, honest limits, with enthusiasm for what is genuinely good), "
                    "head_to_head (6-12 lines: same-point comparisons, ONLY where EVERY product has a fact for the point), "
                    "who_for (which kind of buyer each product suits), verdict (honest, enthusiastic summary following rule 4).")
        schema_extra = ('"comparison_points": [{"point": "<what is compared>", "facts": {"1": "<copied from product 1 FACTS>", "2": "<...>"}}],')
        rec_hint = "<2-3 sentences: which kind of buyer each product suits, tied to facts; name a winner only if PICK_ALLOWED is true>"
        prod_rule = ('\n- Every fact belongs to exactly ONE product. Say the product label whenever you state a product-specific detail; never move or blend facts. '
                     'Compare a point only if EVERY product has a fact for it.'
                     f'\n- PICK_ALLOWED is {str(project.pick_ok).lower()}. '
                     + ('You may say which product you would pick for which need, only tied to a hands_on_note.' if project.pick_ok else
                        'Do NOT pick a winner; say which kind of buyer each product suits. Words like better, best, wins, beats are not allowed.')
                     + '\n- "product" is "1", "2" or "3" for a line about ONE product (its own product_N section always uses that number) and "all" otherwise.')
    seg_product = '"product": "1|2|3|all", ' if project.kind == "compare" else ""
    return f"""You write the narration for an enthusiastic, natural YouTube product video: {project.facts.get('product_name')}.
A human creator reviews every script, an AI voice reads it, and the creator posts it.

STYLE REFERENCE (rhythm and energy only). Words between *asterisks* mark where keywords were placed. Copy the flow
(customer problem -> product solution -> how it works -> practical benefits -> relevant use cases -> enthusiastic close),
the short punchy lines and the warm, conversational American English. NEVER copy its product, claims or facts, and never output asterisks:
-----
{style_text.strip()}
-----

FACTS (the ONLY source of product facts):
{facts}

AVAILABLE FOOTAGE (id: what it shows):
{footage}

KEYWORDS you may use (exact wording; these are phrases viewers search for):
  primary: {primary or '(none)'}
  secondary: {', '.join(secondary) or '(none)'}
KEYWORDS you must NOT use anywhere because the facts do not support them: {', '.join(ctx['unusable']) or '(none)'}
CURRENT YEAR: {year}

{sections}

Write about {n_lines} lines and about {n_words} words in total.

Return ONE JSON object and nothing else:
{{
 "title": "<under 70 characters, starts with or contains the primary keyword, no price, no clickbait>",
 "description_intro": "<2-3 plain sentences describing the video; work in 2-4 keywords; no price>",
 "tags": ["<8-12 relevant tags>"],
 "hashtags": ["<3-5 hashtags like #drafts, no spaces>"],
 "segments": [
  {{"id": 1, "section": "<section name>", {seg_product}"text": "<narration>", "visual": "<what viewers should see>", "onscreen": "", "asset_ids": [], "source": "claim|spec|handson|general", "keywords": ["<keywords used in this line>"], "priority": 1}}
 ],
 {schema_extra}
 "recommendation": "{rec_hint}"
}}
priority: 1 = essential, 2 = useful, 3 = nice to have (cut first if the video runs long).

HARD RULES
- Product facts come ONLY from FACTS. Never invent or infer specs, materials, numbers, features, compatibility, durability or results. If a detail is not in FACTS, do not state it.
- manufacturer_claims are the brand's words: attribute them lightly and naturally ("the brand says", "it's designed to", "according to the listing"). Never present them as your own findings.
- First person ("I", "in my use") ONLY when supported by hands_on_notes. Never "we" or "our": you are not the brand. Without hands_on_notes stay neutral ("if you're someone who...").
- General guidance about the problem or the product category is allowed (source "general") but must not assert anything about THIS product.
- NEVER mention prices, price level (cheap, affordable, budget), saving money, discounts, deals, coupons, star ratings, review counts, what customers or reviewers say, best seller, Amazon's Choice, other retailers, health claims, or guarantees.
- Enthusiasm comes from energy, vivid verbs and short punchy lines, not from hype words. Avoid: game-changer, revolutionary, must-have, life-changing, miracle, perfect for everyone, "in today's video". At most {v['max_exclamations']} exclamation marks in the whole script.
- Do not use absolute promises (airtight, waterproof, instantly, no more, always, never) unless FACTS use that exact claim.
- Each line is 1-3 sentences and {v['min_words_per_line']}-{v['max_words_per_line']} words; mix very short lines with longer ones. Write for the ear: spell out units, no emojis, no markdown, no abbreviations a voice would mispronounce. Use digits only for numbers that appear in FACTS.
- onscreen: at most 4 words and only a fact from FACTS, otherwise "".
- asset_ids: 1-3 ids from AVAILABLE FOOTAGE that match what the line says; [] if none fits. Never describe something the footage does not show.
- visual: one short direction of what the viewer sees (no faces, no prices, no star ratings, no Amazon pages, no other brands).
- Keywords: put the primary keyword in the title, in the description_intro, and in at least 2 narration lines (one within the first 5 lines). Work each secondary keyword into 1-2 relevant lines. Never stuff, and never let a keyword imply a claim FACTS do not support.
- Do not write a disclosure line or a sign-off: both are added automatically.{prod_rule}
"""


# ------------------------------------------------------------------ validation
def _ask(prompt: str, cfg: dict, temperature: float = 0.6) -> dict:
    last = None
    for _ in range(2):
        try:
            return llm.extract_json(llm.chat(prompt, cfg["llm"]["model"], cfg["llm"].get("max_tokens", 6000), temperature))
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            last = e
    raise RuntimeError(f"model output could not be used after retry: {last}")


def items_for(ctx: dict, prod: str) -> list:
    if prod in ctx["items"]:
        return ctx["items"][prod]
    return [x for it in ctx["items"].values() for x in it]


def _allowed_numbers_text(project, ctx: dict, prod: str) -> str:
    if project.kind == "single" or prod == "all":
        return ctx["facts_blob"]
    return " \n ".join(ctx["items"].get(prod, []))


def section_list(project) -> list:
    if project.kind == "single":
        return SECTIONS_SINGLE
    n = len(project.products)
    return ["hook", "context", "overview"] + [f"product_{i}" for i in range(1, n + 1)] + ["head_to_head", "who_for", "verdict"]


def renumber(segs: list) -> None:
    for i, x in enumerate(segs, 1):
        x["id"] = str(i)


def validate_segments(raw: list, project, ctx: dict, cfg: dict) -> list:
    """Clean every line and attach x["fix"] (must be repaired or dropped) and x["warn"] (shown to the creator)."""
    order = {x: i for i, x in enumerate(section_list(project))}
    v = cfg["voice"]
    segs = []
    for s in raw or []:
        if not isinstance(s, dict):
            continue
        section = str(s.get("section", "")).strip().lower()
        text = rules.clean_text(str(s.get("text", "")))
        if section not in order or not text:
            continue
        prod = "all"
        if project.kind == "compare":
            m = re.fullmatch(r"product_(\d)", section)
            prod = m.group(1) if m else str(s.get("product", "all")).strip().lower()
            if prod not in ["all"] + [str(i) for i in range(1, len(project.products) + 1)]:
                prod = "all"
        fix, warn = [], []
        pol, sty = rules.check_text(text)
        fix += [f"policy: {p}" for p in pol]
        warn += [f"style: {x}" for x in sty]
        if not rules.numbers_ok(text, _allowed_numbers_text(project, ctx, prod)):
            fix.append("contains a number that is not in the facts" + (f" of product {prod}" if prod != "all" else ""))
        if rules.BRAND_VOICE.search(text):
            fix.append("speaks as the brand (we/our/us); you are the creator, not the brand")
        if rules.FIRST_PERSON.search(text):
            if project.kind == "single":
                has = ctx["handson"]["1"] > 0
            else:
                has = (ctx["handson"].get(prod, 0) > 0) if prod != "all" else project.pick_ok
            if not has:
                fix.append("first person without hands_on_notes to support it")
        if project.kind == "compare" and not project.pick_ok and rules.WINNER_WORDS.search(text):
            fix.append("picks a winner but not every product has 3+ hands_on_notes")
        source = s.get("source") if s.get("source") in SOURCES else "general"
        if source == "claim" and not rules.ATTRIBUTION.search(text):
            warn.append("brand claim without attribution (the brand says / designed to)")
        low = text.lower()
        for a in rules.ABSOLUTES:
            if a in low and a not in ctx["facts_blob"].lower():
                warn.append(f"absolute wording '{a}' is not in the facts")
        for u in ctx["unusable"]:
            if rules.count_phrase(text, u):
                fix.append(f"uses the keyword '{u}', which the facts do not support")
        wc = word_count(text)
        if wc > v["max_words_per_line"] + 6:
            warn.append(f"long line ({wc} words)")
        if wc < v["min_words_per_line"]:
            warn.append(f"very short line ({wc} words)")

        onscreen = rules.clean_text(str(s.get("onscreen", "")))
        if onscreen and (len(onscreen.split()) > 4 or not rules.supported_by(onscreen, items_for(ctx, prod), 0.8)):
            onscreen = ""
        ids = []
        for a in s.get("asset_ids") or []:
            a = str(a).strip().upper()
            if a not in ctx["footage_ids"]:
                continue
            if project.kind == "compare" and prod != "all" and not (a.startswith(f"P{prod}-") or a.startswith("X-")):
                continue
            ids.append(a)
        try:
            pr = int(s.get("priority", 2))
        except (TypeError, ValueError):
            pr = 2
        segs.append({"id": "", "section": section, "product": prod, "text": text, "onscreen": onscreen,
                     "asset_ids": ids[:3], "source": source, "priority": min(3, max(1, pr)),
                     "visual": rules.clean_text(str(s.get("visual", "")))[:200],
                     "keywords": [str(k) for k in (s.get("keywords") or [])][:6], "fix": fix, "warn": warn})
    segs.sort(key=lambda x: order[x["section"]])          # canonical section order, stable inside a section
    renumber(segs)
    return segs


def keyword_issues(segs: list, title: str, intro: str, ctx: dict, cfg: dict) -> tuple:
    """Placement report + list of problems the model can fix."""
    primary = ctx["usable"][0] if ctx["usable"] else ""
    secondary = ctx["usable"][1: 1 + cfg["keywords"]["secondary_max"]]
    rep, issues = [], []
    for role, kws in (("primary", [primary] if primary else []), ("secondary", secondary)):
        for k in kws:
            lines = [s["id"] for s in segs if rules.count_phrase(s["text"], k)]
            total = sum(rules.count_phrase(s["text"], k) for s in segs)
            in_title, in_intro = bool(rules.count_phrase(title, k)), bool(rules.count_phrase(intro, k))
            early = any(int(i) <= 5 for i in lines)
            rep.append({"keyword": k, "role": role, "narration_count": total, "lines": lines, "in_title": in_title,
                        "in_intro": in_intro, "in_first_5_lines": early})
            if role == "primary":
                if total < 2:
                    issues.append(f"use the primary keyword '{k}' in at least 2 narration lines (found {total})")
                if not early:
                    issues.append(f"put the primary keyword '{k}' in one of the first 5 lines")
                if not in_title:
                    issues.append(f"the title must contain the primary keyword '{k}'")
                if total > 4:
                    issues.append(f"the primary keyword '{k}' appears {total} times; keep it to 4 or fewer")
            else:
                if total < 1:
                    issues.append(f"work the secondary keyword '{k}' into one relevant line")
                if total > 2:
                    issues.append(f"the secondary keyword '{k}' appears {total} times; keep it to 2 or fewer")
    return rep, issues


def title_problems(title: str, intro: str, ctx: dict) -> list:
    out = []
    if not title or len(title) > 70:
        out.append(f"title must be 1-70 characters (now {len(title or '')})")
    for label, t in (("title", title), ("description_intro", intro)):
        pol, _ = rules.check_text(t or "")
        out += [f"{label} breaks a content rule: {p}" for p in pol]
        for u in ctx["unusable"]:
            if rules.count_phrase(t or "", u):
                out.append(f"{label} uses the keyword '{u}', which the facts do not support")
        nums = {n for n in rules.numbers_in(t or "") if not re.fullmatch(r"20\d\d", n)}
        if nums and not nums <= rules.numbers_in(ctx["facts_blob"]):
            out.append(f"{label} contains a number that is not in the facts")
    return out


def clean_tags(tags: list, ctx: dict) -> list:
    out, seen = [], set()
    for t in tags or []:
        t = rules.clean_text(str(t)).lower().lstrip("#")
        if not t or len(t) > 40 or t in seen or rules.check_text(t)[0]:
            continue
        if any(rules.count_phrase(t, u) for u in ctx["unusable"]):
            continue
        seen.add(t)
        out.append(t)
    for k in ctx["usable"]:                     # top up with supported keywords
        if len(out) >= 8:
            break
        if k.lower() not in seen:
            seen.add(k.lower())
            out.append(k.lower())
    return out[:12]


def clean_hashtags(tags: list, ctx: dict) -> list:
    out, seen = [], set()
    for t in tags or []:
        t = "#" + re.sub(r"[^A-Za-z0-9]", "", str(t))
        if len(t) < 3 or len(t) > 30 or t.lower() in seen:
            continue
        if rules.check_text(t[1:])[0] or any(rules.count_phrase(re.sub(r"(?<!^)([A-Z])", r" \1", t[1:]), u) for u in ctx["unusable"]):
            continue
        seen.add(t.lower())
        out.append(t)
    if len(out) < 3:
        for k in ctx["usable"]:
            h = "#" + "".join(w.capitalize() for w in re.findall(r"[A-Za-z0-9]+", k))[:28]
            if len(h) >= 3 and h.lower() not in seen:
                seen.add(h.lower())
                out.append(h)
            if len(out) >= 3:
                break
    return out[:5]


def limit_exclamations(segs: list, limit: int) -> int:
    n, changed = 0, 0
    for s in segs:
        out = []
        for ch in s["text"]:
            if ch == "!":
                n += 1
                if n > limit:
                    ch = "."
                    changed += 1
            out.append(ch)
        s["text"] = "".join(out).replace("..", ".")
    return changed


def fit_length(segs: list, cfg: dict, lo: float, hi: float) -> tuple:
    """Cut priority-3 then priority-2 lines (never the first hook line or the last verdict line) until under the maximum."""
    cut, segs = [], list(segs)
    for pr in (3, 2):
        while estimate_minutes(segs, cfg) > hi - 0.1:
            last_verdict = next((x for x in reversed(segs) if x["section"] == "verdict"), None)
            cands = [x for x in segs if x["priority"] == pr and x is not segs[0] and x is not last_verdict]
            if not cands:
                break
            victim = cands[-1] if pr == 3 else cands[len(cands) // 2]
            segs.remove(victim)
            cut.append(victim)
    return segs, cut


# ------------------------------------------------------------------ top level
def validate_extras(raw: dict, project, ctx: dict) -> dict:
    out = {"benefits": [], "comparison_points": [], "recommendation": None}
    allitems = [x for it in ctx["items"].values() for x in it]
    if project.kind == "single":
        for b in raw.get("benefits") or []:
            ben, fact = rules.clean_text(str(b.get("benefit", ""))), rules.clean_text(str(b.get("fact", "")))
            if ben and fact and rules.supported_by(fact, allitems, 0.8) and not rules.check_text(ben)[0]:
                out["benefits"].append({"benefit": ben[:160], "fact": fact[:200]})
    else:
        n = len(project.products)
        for c in raw.get("comparison_points") or []:
            facts = c.get("facts") if isinstance(c.get("facts"), dict) else {}
            pt = rules.clean_text(str(c.get("point", "")))
            if not pt or any(str(i) not in facts for i in range(1, n + 1)):
                continue
            row = {}
            for i in range(1, n + 1):
                t = rules.clean_text(str(facts[str(i)]))
                if not rules.supported_by(t, ctx["items"][str(i)], 0.8):
                    row = None
                    break
                row[str(i)] = t[:200]
            if row:
                out["comparison_points"].append({"point": pt[:120], "facts": row})
    rec = rules.clean_text(str(raw.get("recommendation") or ""))
    if rec:
        pol, _ = rules.check_text(rec)
        bad_winner = project.kind == "compare" and not project.pick_ok and rules.WINNER_WORDS.search(rec)
        if pol or bad_winner or rules.BRAND_VOICE.search(rec) or not rules.numbers_ok(rec, ctx["facts_blob"]):
            out["rec_dropped"] = True
        else:
            out["recommendation"] = rec[:500]
    return out


def make_script(project, cfg: dict, style_text: str) -> dict:
    ctx = build_context(project)
    key, preset = preset_for(project, cfg)
    lo, hi, target = preset["min_minutes"], preset["max_minutes"], preset["target_minutes"]
    n_words, n_lines = target_words(cfg, target)
    notes = []
    prompt = build_prompt(project, cfg, ctx, style_text, n_words, n_lines)
    raw = _ask(prompt, cfg)
    segs = validate_segments(raw.get("segments"), project, ctx, cfg)
    title = rules.clean_text(str(raw.get("title", "")))
    intro = rules.clean_text(str(raw.get("description_intro", "")))

    # ---- one repair round: the exact problems go back to the model
    fixes = [f"line [{x['id']}] ({x['text'][:60]}...): " + "; ".join(x["fix"]) for x in segs if x["fix"]]
    _, kissues = keyword_issues(segs, title, intro, ctx, cfg)
    tissues = title_problems(title, intro, ctx)
    present = {x["section"] for x in segs}
    structure = [f"missing section '{x}'" for x in ("hook", "verdict") if x not in present]
    if fixes or kissues or tissues or structure:
        feedback = "\n- ".join(fixes + kissues + tissues + structure)
        repair = (f"{prompt}\n\nYOUR PREVIOUS ANSWER:\n{json.dumps(raw, ensure_ascii=False)}\n\n"
                  f"Fix ONLY these problems, keep everything else, and return the COMPLETE corrected JSON:\n- {feedback}\n")
        try:
            raw2 = _ask(repair, cfg, 0.3)
            segs2 = validate_segments(raw2.get("segments"), project, ctx, cfg)
            n_bad = lambda ss: sum(1 for x in ss if x["fix"])   # noqa: E731
            if segs2 and n_bad(segs2) <= n_bad(segs):
                raw, segs = raw2, segs2
                title = rules.clean_text(str(raw2.get("title", ""))) or title
                intro = rules.clean_text(str(raw2.get("description_intro", ""))) or intro
            else:
                notes.append("The repair attempt was not better than the first draft, so the first draft was kept.")
        except llm.LLMUnavailable:
            raise
        except Exception as e:  # noqa: BLE001
            notes.append(f"Repair attempt failed: {e}")

    # ---- lines that still break a hard rule are removed, never shipped
    dropped = [{"text": x["text"], "why": x["fix"]} for x in segs if x["fix"]]
    segs = [x for x in segs if not x["fix"]]
    if dropped:
        notes.append(f"{len(dropped)} line(s) still broke a hard rule after the repair round and were removed: "
                     + "; ".join(f"'{d['text'][:50]}...' ({', '.join(d['why'])})" for d in dropped))
    if not segs:
        raise RuntimeError("no usable lines were left after the checks")
    renumber(segs)

    if not title:
        title = str(project.facts.get("product_name", ""))[:70]
        notes.append("The model gave no title, so the product name is used. Write a better one.")
    for t in title_problems(title, intro, ctx):
        notes.append(f"Title/description problem left unresolved: {t}")

    changed = limit_exclamations(segs, cfg["voice"]["max_exclamations"])
    if changed:
        notes.append(f"{changed} extra exclamation mark(s) became full stops (limit {cfg['voice']['max_exclamations']}).")

    # ---- the video agent's length window
    est, cut = estimate_minutes(segs, cfg), []
    if est > hi - 0.1:
        segs, cut = fit_length(segs, cfg, lo, hi)
        renumber(segs)
        notes.append(f"Script was {est:.1f} min; {len(cut)} low-priority line(s) were cut to fit the {hi} minute limit.")
        est = estimate_minutes(segs, cfg)
    if est < lo + 0.1:
        more = int((lo + 0.2 - est) * 60 * cfg["timing"]["wpm"] / 60)
        notes.append(f"Script is about {est:.1f} min and the video agent's '{key}' length needs at least {lo}. Add about {more} words "
                     "by adding facts or hands_on_notes to facts.yaml and rerunning, or choose a different length.")

    kw_report, kw_left = keyword_issues(segs, title, intro, ctx, cfg)
    extras = validate_extras(raw, project, ctx)
    if extras.pop("rec_dropped", False):
        notes.append("The model's recommendation broke a rule (price, winner, brand voice or an unsupported number) and was dropped.")
    return {
        "title": title, "description_intro": intro, "tags": clean_tags(raw.get("tags"), ctx),
        "hashtags": clean_hashtags(raw.get("hashtags"), ctx),
        "angle": "problem_solution" if project.kind == "single" else "comparison",
        "segments": segs, "keyword_report": kw_report, "keyword_issues": kw_left,
        "unusable_keywords": ctx["unusable"], "usable_keywords": ctx["usable"], "extras": extras,
        "estimated_minutes": round(est, 1), "length_key": key, "length_window": [lo, hi],
        "cut": [{"id": c["id"], "text": c["text"]} for c in cut], "dropped": dropped, "notes": notes,
    }
