# YouTube Affiliate Agent ($0)

Writes the **voice-over `script.md`** for your video agent, plus the YouTube **title, description intro, tags, hashtags**, **comparison points**,
a **recommendation** and a **shot list**. It never uploads, renders or spends anything.

## How it connects to your video agent
Both agents read the **same input files**. You copy files, nothing runs automatically.
```
research agent  ->  video_agent/projects/<ASIN>/facts.yaml (skeleton)  ->  you fill in your own notes + affiliate link
                                      |                                          |
                          this agent (projects/ folder)  <---- copy the same facts.yaml / compare.yaml
                                      |
                      output/<ASIN>/script.md  ->  copy to the VIDEO agent repo as projects/<ASIN>/script.md  ->  run its RENDER step
```
`script.md` follows the video agent's own layout exactly (title/intro/tags/angle header, `## section` order, `[id] text | callout | assets | p:` lines).
The test suite writes the file, reads it back with the **video agent's own code** (`md_to_script`, `assemble`, `script_to_md`, `check_text`) and checks that the layout is identical
and no line breaks its content rules.

## Setup (once)
1. Put these files in the root of a GitHub repo and add the secret `GROQ_API_KEY` (Settings -> Secrets and variables -> **Actions**).
2. Copy your finished project folders into `projects/`:
   - single product: `projects/<ASIN>/facts.yaml`
   - comparison: `projects/<NAME>/compare.yaml` **and** each product's `projects/<ASIN>/facts.yaml`
   - optional: `projects/<ASIN>/footage.csv` with columns `file,description`. List **every** photo/clip in that product's `assets/` folder in the video agent
     (ids A01, A02... are given by sorted file name, exactly like the video agent does, so the list must be complete). Without it, `assets:` stays empty and the shot list tells you what to film.
3. Run **Actions -> YouTube Affiliate Agent**. Pick `project` (a folder name or `all`) and `length`.

## Length: choose the same in the video agent
The video agent refuses scripts outside its length window. Your reference voice-over is ad-length (about 2-3 minutes), so the default is **short** (about 3 minutes,
the video agent's 1.5-4.8 minute window). Choose **short** in the video agent's workflow too. With `auto`, a single product targets about 5 minutes and a comparison about 7.5.
If the script ends up too short, `video_brief.md` says how many words to add (add facts or hands_on_notes and rerun).

## Files (`output/<NAME>/`)
| File | Use |
|---|---|
| `script.md` | **Copy to the video agent** as `projects/<NAME>/script.md`. No asterisks, no markdown, no disclosure line, no sign-off (the video agent adds those) |
| `script_keywords_marked.md` | Same script with `*keywords*` marked like your reference voice-over. **For your review only**: the voice and captions would read the asterisks, so never give this one to the video agent |
| `youtube_upload.md` | Title, description intro, tags, hashtags. Use the video agent's `description.txt` as the base (its first line is the affiliate disclosure), then add the hashtags |
| `shot_list.csv` / `.md` | For each line: what viewers should see, which footage covers it, or "film this" |
| `video_brief.md` | Keyword placement, comparison points or benefits, recommendation, lines that need your eyes, removed lines |
| `script.json` | Everything, machine-readable |
`output/summary.csv` lists every project with its status. A project with unfinished facts gets `NOT_READY.md` listing what to fix.

## What keeps it accurate
- **Facts only.** The model gets your `facts.yaml` and nothing else. Numbers must appear in the facts (for a comparison: in THAT product's facts, so one product's number can never land on the other). Brand claims are attributed ("the brand says", "it's designed to").
- **First person only with `hands_on_notes`.** "I" and "my" lines are removed when there are none. "We / our" is never allowed: you are not the brand.
- **Keywords must be backed by the facts.** A keyword the facts do not support (for example "waterproof" when no fact says so) stays out of the title, narration, tags and hashtags, and is listed in `youtube_upload.md`.
  The primary keyword goes in the title, in at least 2 lines and within the first 5 lines; secondary keywords once or twice each. Placement is reported, not guessed.
- **The video agent's own content rules** (prices, star ratings, review quotes, promo words, other retailers, health claims) are copied word for word. A line that breaks one gets one repair round, then is removed.
- **Winners:** only when every product has 3+ hands_on_notes, same as the video agent.
- **Enthusiasm** comes from energy and short punchy lines like your example, with at most 2 exclamation marks (`voice.max_exclamations`). Your example is used for rhythm and tone only; none of its product claims are reused.

## Honest limits
- It cannot see your footage. It matches lines only to the files you list in `footage.csv`, and says so in the shot list when nothing matches.
- Narration quality depends on how many real facts you supply. With fewer than about 8 facts the script stays short or generic, and the report says so.
- List videos ("Top N") are not supported yet: single product and comparison only.
- The video agent's own script step would overwrite your `script.md`. Do not run it with force after copying this file; run only the render step.
- The video agent is being fixed by another AI. This agent only writes the script; it does not depend on the video agent's render code, but if its format changes, `rules.py` (content rules) and `exporters.py` (script layout) must follow.
- Groq's free tier is rate limited; the agent waits and retries. If a request is rejected as too large, lower `llm.max_tokens` or use `length: short`.
- Upload, description disclosure and the final review checklist stay with the video agent and with you.

## Tests
`python tests/test_offline.py` (no network or API key). The model is faked, including deliberately bad lines, so they prove the checks, repair and export logic and the compatibility with
your video agent's code (when its folder is next to this one), not the quality of real Groq narration. The first real run is the live test.
