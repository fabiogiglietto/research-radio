# Episode mismatch audit — 2026-06-21

Triggered by: titles/files/descriptions mismatch noticed on Spotify.

## Method
- `audio_url` filename always equals the episode `id`, and each `description`'s
  paper (APA citation) matches the live toread feed for that `id` — so the
  **file ↔ id ↔ description** are internally consistent across all 118 episodes
  (author-surname-in-authors check passed for all but `UnknownUnknown-db`,
  which simply has empty author metadata).
- The defective field is the catchy `title`. A batched LLM pass over all 118
  `(paper_title, episode_title)` pairs flagged 15.
- Because the episode title is generated **from the script** (= the audio), a
  title that names a different paper than the description is a signature of
  audio generated from the **wrong source PDF**. Confirmed by sending each
  flagged episode's actual MP3 (from GitHub Releases) to Gemini and asking which
  paper the hosts say they discuss.

## Result: 15 flagged → two classes

### A. Correct audio, title was just truncated/bad (10) — FIXED by relabel
Boyd2026-op, Swartz2026-zb, Rothut2026-wt, Achmann-Denkler2026-lx,
Rothut2026-or, Volpe2026-um, Waight2026-ts, Spampatti2026-kx, Mahl2026-hc,
Efstratiou2026-ij. Audio verified to match the described paper. New catchy
titles written to `docs/episodes.json`.

### B. WRONG AUDIO — relabel is NOT the fix; audio must be regenerated (8)
Each of these episodes' audio actually discusses a *different* paper. The
intended paper for these ids was **never correctly produced**.

| Broken episode (id → intended paper) | Audio actually discusses |
|---|---|
| Tornberg2025-ir → "When do parties lie?…" | "Towards a post-social media studies" (= Tornberg2026-lc) |
| Hepp2026-oi → "The imaginative landscape of AI…" | "Multi-platform analysis of electoral discourse…" (= Schulte2026-df) |
| Li2026-wq → "Does YouTube shape conspiracy mentality?" | "How deceptive online networks reached millions…" (= Appel2026-qr) |
| Shi2026-ko → "…AI-generated disclosure…Douyin" | "Defending fact-checking partnerships…" (= Farkas2026-lr) |
| Marwick2026-qd → "Disinformation as cultural narrative" | "Broken connections: fieldnotes from the old internet" (= Marwick2026-ss) |
| FitzGerald2025-nv → "The persistence of informational manipulation…" | "Bridging the Narrative Divide…" (Luceri, Ferrara et al.) |
| Cabbuag2024-me → "TikTok 'dogshows'…Philippines" | "The Post-API Age of Social Media Data Access" (Freelon et al.) |
| Costello2024-kp → "Durably reducing conspiracy beliefs…with AI" | "The Post-API Age of Social Media Data Access" (Freelon et al.) |

- The first 5 were found via the title↔description proxy. The last 3 had a
  **correct title and description** (title fell back to the paper title because
  `generate_episode_title` returned nothing) and were caught only by a
  catalog-wide audio sweep: a byte-range download of each episode's first ~80s
  was sent to Gemini ("which paper do the hosts discuss?"). **All 118 episodes'
  audio were checked this way** (20 full + 98 intro-only); these 8 are the only
  mismatches.
- The broken MP3s are distinct files (different md5) from the correct episodes
  of the papers they accidentally discuss — independently generated, just from
  the wrong source text.

## Root cause — CONFIRMED against the live Drive folder
For each wrong episode, `drive_client.find_pdf(paper)` was re-run against the
real Paperpile folder and the matched file's text extracted:

| id | file `find_pdf` matches now | content |
|---|---|---|
| Tornberg2025-ir | "Törnberg and Rogers 2026 - Towards a Post-Social Media Studies.pdf" | the *other* Törnberg paper |
| Hepp2026-oi | "Hepp 2026 - The imaginative landscape of AI…​.pdf" | **Schulte's electoral-discourse paper** (mis-attached PDF) |
| Li2026-wq | "Fattorini et al. 2026 - Italians' attitudes towards AI….pdf" | a third, unrelated paper |
| Shi2026-ko | "Farkas and Bengtsson 2026 - Defending fact-checking partnerships….pdf" | Farkas's paper |
| Marwick2026-qd | "Marwick 2026 - Broken connections….pdf" | the *other* Marwick paper |
| FitzGerald2025-nv | (None) | intended PDF absent now |
| Cabbuag2024-me | (None) | intended PDF absent now |
| Costello2024-kp | (not in current feed) | — |

**Two compounding causes:**
1. **`find_pdf` matches the wrong file.** Scoring is title-substring +50,
   author-surname-substring +30, year +20, with gate `best_score >= 50`. So
   (a) author+year alone (30+20) clears the gate with **no title match**, and
   (b) the surname check is a raw substring, so short names ("Li", "Shi") match
   inside unrelated filenames. When the intended PDF is missing or a same-author
   sibling exists, it returns the wrong PDF instead of `None`.
2. **At least one genuinely mis-attached PDF in Drive**: the file *named*
   "Hepp 2026 - The imaginative landscape of AI" actually *contains* Schulte's
   "Multi-Platform Analysis of Electoral Discourse". A `find_pdf` fix cannot
   catch this — the file content itself is wrong and must be re-uploaded in
   Paperpile/Drive.

**Implication:** regeneration will reproduce the bug until BOTH `find_pdf` is
hardened (require a real title-token match; gate on title, not author+year) AND
the mis-attached Drive PDFs are corrected.

## Propagation to fg-zettelkasten — CONFIRMED
The downstream vault (`fabiogiglietto/fg-zettelkasten`) embeds a `## Podcast`
section in each paper note from research-radio's `episodes.json`
(`src/episodes_client.py` → `src/note_builder.render_podcast_block`). It joins
purely on `id`, so **7 of the 8** wrong episodes already have notes linking the
wrong-content audio (MP3 + Spotify + Apple); `Costello2024-kp` has no note.
The note heading is the correct paper, but the linked audio (and the Apple slug,
e.g. `…the-end-of-social-media…` on the "When do parties lie?" note) is the
wrong episode.

`episodes_client.fetch_episodes` does **not** look at `audio_mismatch`, so a
re-run would re-link them. Fix (one line, in the fg-zettelkasten repo):
in `fetch_episodes`, `continue` when `ep.get("audio_mismatch")` — flagged
episodes then drop out of the map, `render_podcast_block(None)` returns "", and
the `## Podcast` block is stripped on the next note rebuild / `backfill_podcasts`
run (the `content_hash` includes `podcast_linked`, so the change is detected).

### Action taken for the 5 (pulled from feed + flagged)
- Each of the 5 is flagged `"audio_mismatch": true` in `docs/episodes.json`.
- `generate_podcast_feed()` now excludes `audio_mismatch` episodes, so they no
  longer appear in `docs/feed.xml` (Spotify/Apple). Feed dropped 115 → 110 items.
- Their `episodes.json` entries are kept (with the corrected intended-paper
  title) so they can be regenerated. `validate_sync.py` was updated to treat
  flagged episodes as intentionally-withheld.
- **Still TODO (needs Drive access):** verify/replace the source PDF for each of
  the 5 ids in the Paperpile Drive folder, then regenerate their audio and
  clear the `audio_mismatch` flag. The website (`docs/index.html`) reads
  `episodes.json` directly and can consume the same flag to hide them.

## Fixes applied to the root cause
- **`src/drive_client.find_pdf` hardened** (this repo): replaced the loose
  scoring (which passed on author+year alone and used raw surname substrings)
  with a **title-token-overlap gate** (≥0.6 of the paper's title words must
  appear in the filename); author/year are tie-breakers only; surname match is
  whole-token, ≥3 chars. Validated against the live Drive folder: the 7 still-
  broken papers now return `None` (no false sibling match) instead of a wrong
  PDF, Hepp2026-oi matches its (now-corrected) file, and the 5 known-correct
  sibling episodes still match at 0.90–1.00 overlap.
- **fg-zettelkasten** PR #14 (`fix/skip-audio-mismatch-episodes`): `fetch_episodes`
  skips `audio_mismatch` episodes, removing the wrong `## Podcast` blocks on the
  next note rebuild.
- **Hepp2026-oi Drive PDF**: re-attached by the author — verified the file now
  contains the correct paper.

## Action still required by the author (Drive + regeneration)
The intended PDF is **absent** from Drive for these 7 — add the correct PDF
(Paperpile-style filename so `find_pdf` matches), with a recent modified time so
the 30-day filter picks it up:

| id | expected Drive filename |
|---|---|
| Tornberg2025-ir | `Törnberg et al. 2026 - When do parties lie? Misinformation and radical-right populism across 26 countries.pdf` |
| Li2026-wq | `Li et al. 2026 - Does YouTube shape conspiracy mentality?….pdf` |
| Shi2026-ko | `Shi 2026 - The Governance-Embedded Interactive Media Effect:…Douyin.pdf` |
| Marwick2026-qd | `Marwick et al. 2026 - Disinformation as cultural narrative:….pdf` |
| FitzGerald2025-nv | `FitzGerald et al. 2025 - The persistence of informational manipulation….pdf` |
| Cabbuag2024-me | `Cabbuag et al. 2025 - TikTok 'dogshows'…Philippines.pdf` |
| Costello2024-kp | **not in the current toread feed** — re-add to toread first, then a PDF |

Then, to regenerate each: remove its id from `data/processed.json` so the
pipeline reprocesses it. `add_episode` overwrites the episodes.json entry with a
fresh record (`audio_mismatch` defaults back to False), re-publishing it to the
feed; the fg-zettelkasten podcast block returns on its next rebuild.

## Other fixes applied this pass
- `docs/episodes.json`: 4 descriptions had HTML entities (`&#x27;`) unescaped.
- `src/feed_generator.py`: `html.unescape()` the paper title/episode title in
  `create_episode_from_paper` (prevents recurrence).
- `src/claude_script_generator.py`: `generate_episode_title` now rejects empty /
  truncated / dangling-colon / <3-word titles (falls back to paper title) and
  strips a stray "FG's Research Radio:" prefix.
- `docs/feed.xml` regenerated.
