# Pending: re-add 7 missing Drive PDFs and regenerate their episodes

Open action items harvested from the June 21 episode-mismatch audit
(`WRONG_AUDIO_REPORT.md`, removed — full context in git history).

The intended PDF is **absent** from Drive for these 7 papers. Add the correct
PDF (Paperpile-style filename so `find_pdf` matches), with a recent modified
time so the 30-day filter picks it up:

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
pipeline reprocesses it. `add_episode` overwrites the episodes.json entry with
a fresh record (`audio_mismatch` defaults back to False), re-publishing it to
the feed; the fg-zettelkasten podcast block returns on its next rebuild.

Delete this file once all seven are regenerated.
