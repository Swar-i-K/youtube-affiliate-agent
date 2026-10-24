"""
rules.py
--------
Deterministic checks. Nothing here uses the model, so every verdict is traceable.

POLICY_RULES / STYLE_RULES are copied word for word from your video agent (adagent/project.py).
Its render step REFUSES a script with a policy hit, so this agent checks the same patterns first.
If you change the rules in the video agent, paste the new lists here.
"""
from __future__ import annotations

import re

from project import PLACEHOLDER_RE  # noqa: F401  (re-exported for callers)

POLICY_RULES = [
    (r"\$\s?\d|\b\d[\d,.]*\s?(usd|dollars?|bucks|euros?|pounds?)\b", "mentions a price"),
    (r"\b\d(\.\d)?\s*(out of 5|/5|stars?)\b|\bfive[- ]star\b|\bstar rating", "mentions a star rating"),
    (r"\b\d[\d,]*\s+(customer\s+)?(reviews|ratings)\b", "mentions a review/rating count"),
    (r"\b(customers?|reviewers?|buyers?|shoppers?)\s+(say|said|love|loved|report|reported|complain|praise|rave)", "paraphrases customer reviews"),
    (r"\bamazon'?s\s+choice\b|\bbest[- ]?sell(er|ing)\b|\b#\s?1\b", "uses an Amazon badge/ranking"),
    (r"\b(on sale|discounts?|coupons?|limited[- ]time|\d+\s?% off|percent off|price drop|lowest price|cheapest|free shipping|prime day|black friday|cyber monday)\b|\b(great|good|best|amazing|hot)\s+deals?\b", "uses price/promotion language"),
    (r"\b(walmart|target|best buy|ebay|aliexpress|temu|etsy)\b", "names another retailer"),
    (r"\b(cures?|heals?|clinically proven|medically proven|fda[- ]approved)\b", "makes a health claim"),
]
STYLE_RULES = [
    (r"\b(cheap|cheaper|cheapest|affordable|budget|pricey|expensive|bargain)\b", "price wording: prices change and are not allowed to be quoted, so avoid describing price level"),
    (r"\b(game[- ]changer|revolutionary|must[- ]have|life[- ]changing|miracle|guaranteed?)\b", "hype phrase"),
    (r"\bin today'?s video\b|\bsmash (that|the) like\b", "generic YouTube cliche"),
    (r"\bperfect for everyone\b|\bevery(one|body) needs\b", "overclaim"),
    (r"\b(better than|worse than|beats|outperforms?|clear winner|the winner|hands down)\b", "comparative claim: keep only if your own hands_on_notes support it"),
]


def check_text(text: str) -> tuple:
    policy, style = [], []
    for pat, why in POLICY_RULES:
        if re.search(pat, text, re.I):
            policy.append(why)
    for pat, why in STYLE_RULES:
        if re.search(pat, text, re.I):
            style.append(why)
    return policy, style


# ------------------------------------------------------------------ extra checks of this agent
FIRST_PERSON = re.compile(r"\b(i|i'm|i've|i'd|i'll|me|my|mine|myself)\b", re.I)          # needs hands_on_notes
BRAND_VOICE = re.compile(r"\b(we|we're|we've|we'll|our|ours|us)\b", re.I)                    # never: the creator is not the brand
ATTRIBUTION = re.compile(r"\b(the brand says|brand says|according to|the listing|listing says|manufacturer|the maker|claims?|"
                         r"designed to|made to|built to|is meant to|it says|advertised)\b", re.I)
ABSOLUTES = ["airtight", "air tight", "waterproof", "weatherproof", "water resistant", "instantly", "in a flash", "in seconds",
             "in minutes", "seamless", "perfect", "completely", "always", "never", "100%", "no more", "forever", "permanent",
             "unbreakable", "innovative", "save money", "saves money", "cost you a fortune", "guaranteed", "foolproof"]
WINNER_WORDS = re.compile(r"\b(better|best|worse|wins?|beats?|outperforms?|winner|superior)\b", re.I)
NUM_TOKEN = re.compile(r"\$?\d[\d,]*(?:\.\d+)?")
STOP = set("""a an the of in on for to and or with without that your you it its is are be this these those at by as from
into so but if then than too very can will just not no""".split())


def clean_text(t: str) -> str:
    """Plain text for the voice and the on-screen captions: no markdown, no emoji, no pipes, no look-alike hyphens."""
    t = re.sub(r"[*_`#~>]+", "", t)
    t = re.sub(r"[\U0001F000-\U0001FAFF\u2600-\u27BF\uFE0F]", "", t)
    for bad in ("\u2010", "\u2011", "\u2012", "\u2043"):
        t = t.replace(bad, "-")
    t = t.replace("\u00a0", " ").replace("\u202f", " ")
    t = re.sub(r"\s*\|\s*", ", ", t)
    t = re.sub(r"([!?])\1+", r"\1", t)             # "!!" -> "!"
    t = re.sub(r"\s+", " ", t).strip()
    return t


def num_norm(tok: str) -> str:
    return tok.replace(",", "").lstrip("$").rstrip(".")


def numbers_in(text: str) -> set:
    return {num_norm(t) for t in NUM_TOKEN.findall(text) if num_norm(t)}


def words(text: str) -> list:
    out = []
    for w in re.findall(r"[a-z0-9$%']+", text.lower()):
        w = w.strip("'")
        if len(w) > 3 and w.endswith("s") and not w.endswith("ss"):
            w = w[:-1]
        if w and w not in STOP:
            out.append(w)
    return out


def fact_items(facts: dict) -> list:
    items = []
    for k in ("manufacturer_claims", "specs", "hands_on_notes", "pros", "cons", "not_for"):
        items += [str(x) for x in (facts.get(k) or [])]
    for k in ("product_name", "brand", "short_name", "category", "audience"):
        if facts.get(k):
            items.append(str(facts[k]))
    return items


def facts_text(facts: dict) -> str:
    return " \n ".join(fact_items(facts))


def numbers_ok(text: str, allowed_text: str) -> bool:
    """Every whole number in `text` must appear as a whole number in the allowed facts ("3" does not match "36")."""
    return numbers_in(text) <= numbers_in(allowed_text)


def supported_by(text: str, items: list, threshold: float = 0.6) -> bool:
    """`text` is backed by ONE fact item: most of its content words appear in that item and its numbers do too."""
    tw = set(words(text))
    if not tw:
        return False
    for it in items:
        iw = set(words(it))
        if len(tw & iw) / len(tw) >= threshold and numbers_in(text) <= numbers_in(it):
            return True
    return False


# ------------------------------------------------------------------ keywords
def norm_phrase(t: str) -> str:
    t = (t or "").lower().replace("\u2019", "'")
    t = re.sub(r"[-\u2013\u2014]+", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def count_phrase(text: str, phrase: str) -> int:
    p = norm_phrase(phrase)
    if not p:
        return 0
    pat = r"(?<![a-z0-9])" + r"\s+".join(re.escape(w) for w in p.split()) + r"(?![a-z0-9])"
    return len(re.findall(pat, norm_phrase(text)))


def keyword_supported(keyword: str, facts_blob: str, threshold: float = 0.7) -> bool:
    """A keyword may be used in narration/title only if the facts back its content words (it must not imply an unsupported claim)."""
    kw = set(words(keyword))
    if not kw:
        return False
    return len(kw & set(words(facts_blob))) / len(kw) >= threshold


def mark_keywords(text: str, keywords: list) -> str:
    """Wrap each keyword occurrence in *...* (longest first, no nesting). Used only for the review copy."""
    spans = []
    for k in sorted({k for k in keywords if k}, key=lambda k: -len(k)):
        pat = r"(?<![A-Za-z0-9])" + r"[\s-]+".join(re.escape(w) for w in k.split()) + r"(?![A-Za-z0-9])"
        for m in re.finditer(pat, text, re.I):
            if not any(m.start() < e and m.end() > s for s, e in spans):
                spans.append((m.start(), m.end()))
    out, last = [], 0
    for s, e in sorted(spans):
        out += [text[last:s], "*", text[s:e], "*"]
        last = e
    out.append(text[last:])
    return "".join(out)
