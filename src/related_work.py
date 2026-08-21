"""
Pick papers the show has already covered that genuinely speak to a new paper.

Three stages, cheapest first:

1. Pool + prefilter (no LLM). Candidates are our own published episodes. Where
   the vault already has a note for the new paper, its `## Connections` section
   seeds the shortlist — fg-zettelkasten wrote that with the whole vault in
   view, so it beats anything we can compute. BM25 over the summaries fills the
   remaining slots, and covers the case where no note exists yet.
2. One Claude call turns the top candidates into a small, anchored brief.
3. `claude_script_generator` weaves the brief into the dialogue.

The pool is deliberately narrow: only papers with a *published, non-own*
episode, i.e. the exact predicate `generate_podcast_feed` uses to decide what
reaches Spotify and Apple. A host saying "we covered that one" has to be
pointing at something the listener can actually go and play.

Every failure path returns an empty list — a paper with no connections is
scripted exactly as it was before this module existed. A forced connection is
worse than none.
"""

import json
import math
import re
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

from src.kasten_client import spoken_reference

# Episode titles are stored with the show name in front. On air the hosts are
# already saying the show's name, so the prefix has to come off before it goes
# into the brief — otherwise they announce "our episode Eff-Gee's Research
# Radio: ..." in the middle of Eff-Gee's Research Radio.
_SHOW_PREFIX = "FG's Research Radio:"

# Candidates handed to the model. Enough for a real choice, small enough that
# the call stays around 8k input tokens.
SHORTLIST_SIZE = 15
# Below this there is no meaningful selection to make.
MIN_POOL = 2
# Key claims quoted per candidate in the shortlist prompt.
_CLAIMS_PER_CANDIDATE = 3

_RELATIONSHIPS = (
    "extends", "tension", "shared-method", "shared-context",
    "precursor", "application",
)

_WORD = re.compile(r"[a-z][a-z'-]{2,}")

# Frequent in every abstract in this corpus, so they carry no signal.
_STOPWORDS = frozenset("""
the and for that with this from are was were has have had not but which their
its they them then than when where what who whom how why can could would should
may might must will shall about into over under between among across through
during before after above below more most less least such some any each other
another both few many much own same very also only just even still yet
research paper study studies article authors author findings finding results
result data analysis analyses using used use based approach approaches
""".split())


def _tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(text.lower()) if w not in _STOPWORDS]


def _candidate_text(entry: dict) -> str:
    return " ".join(
        [entry.get("title", ""), entry.get("abstract", "")]
        + list(entry.get("key_claims") or [])
    )


def _bm25_rank(query: str, candidates: dict, limit: int,
               own_topics: Optional[list] = None) -> list[str]:
    """Return candidate keys ranked by BM25 against `query`, best first.

    Okapi BM25 with the usual k1/b. The corpus is a few hundred short academic
    summaries, so this runs in milliseconds and needs no dependency.

    `own_topics` are the new paper's topic slugs when the vault has already
    assigned them. Sharing a register is weak evidence on its own — the
    registers are broad — so it nudges the lexical score rather than gating it.
    """
    k1, b = 1.5, 0.75
    docs = {key: _tokens(_candidate_text(entry)) for key, entry in candidates.items()}
    docs = {key: toks for key, toks in docs.items() if toks}
    if not docs:
        return []
    avgdl = sum(len(toks) for toks in docs.values()) / len(docs)

    doc_freq = Counter()
    for toks in docs.values():
        doc_freq.update(set(toks))

    n_docs = len(docs)
    query_terms = Counter(_tokens(query))
    scores = {}
    for key, toks in docs.items():
        counts = Counter(toks)
        length = len(toks)
        score = 0.0
        for term, q_count in query_terms.items():
            freq = counts.get(term)
            if not freq:
                continue
            df = doc_freq[term]
            idf = math.log(1 + (n_docs - df + 0.5) / (df + 0.5))
            norm = freq * (k1 + 1) / (freq + k1 * (1 - b + b * length / avgdl))
            # Dampen query-side repetition so a term repeated in the abstract
            # does not dominate the whole ranking.
            score += idf * norm * (1 + math.log(q_count))
        if score > 0:
            if own_topics:
                shared = len(set(own_topics) & set(candidates[key].get("topics") or []))
                score *= 1 + 0.15 * shared
            scores[key] = score
    return sorted(scores, key=scores.get, reverse=True)[:limit]


def _episode_name(title: str) -> str:
    """Episode title as a host would say it, without the show-name prefix."""
    if title.startswith(_SHOW_PREFIX):
        return title[len(_SHOW_PREFIX):].strip()
    return title


def _covered_episodes() -> dict:
    """Map bibtex key -> Episode for every episode a listener can actually play.

    Same predicate as `feed_generator.generate_podcast_feed`: published, not an
    own-paper episode (those are deliberately withheld from the RSS feed), and
    not flagged as carrying the wrong audio.
    """
    from src.feed_generator import load_episodes

    now = datetime.now(timezone.utc)
    return {
        episode.id.split(":", 1)[-1]: episode
        for episode in load_episodes()
        if episode.pub_date <= now and not episode.own and not episode.audio_mismatch
    }


def _query_text(paper_title: str, summary: Optional[dict],
                content_text: str = "", paper_text: str = "") -> str:
    """Best available description of the new paper, for ranking.

    Preference order matters: the structured summary is written for exactly
    this purpose, the feed abstract is a real abstract, and the head of the
    extracted PDF is mostly title page, affiliations and journal boilerplate —
    poor ranking material, so it is the last resort.
    """
    parts = [paper_title]
    if summary:
        parts.append(summary.get("abstract", ""))
        parts.extend(_as_list(summary.get("key_claims")))
        parts.extend(_as_list(summary.get("findings")))
    elif content_text:
        parts.append(content_text)
    elif paper_text:
        parts.append(paper_text[:4000])
    return " ".join(p for p in parts if p)


def _as_list(value) -> list:
    if not value:
        return []
    if isinstance(value, str):
        return [value]
    return [str(item) for item in value]


def _render_candidates(keys: list[str], bundle: dict, episodes: dict,
                       curated: frozenset = frozenset()) -> str:
    blocks = []
    for key in keys:
        entry = bundle[key]
        flag = ("\n  NOTE: the vault already links this paper to the one under "
                "discussion." if key in curated else "")
        claims = (entry.get("key_claims") or [])[:_CLAIMS_PER_CANDIDATE]
        claim_lines = "\n".join(f"  - {c}" for c in claims)
        episode = episodes[key]
        blocks.append(
            f"[{key}]\n"
            f"  Title: {entry['title']}\n"
            f"  Spoken reference: {spoken_reference(entry)}\n"
            f"  Our episode: \"{_episode_name(episode.title)}\" "
            f"({episode.pub_date.strftime('%B %Y')})\n"
            f"  Key claims (this is ALL you know about this paper):\n{claim_lines}"
            + flag
        )
    return "\n\n".join(blocks)


def _build_prompt(paper_title: str, query: str, candidates: str, max_n: int) -> str:
    return f"""You are the research producer for "FG's Research Radio", a podcast on \
computational social science, platform studies and misinformation research.

The hosts are about to record an episode on the paper below. Your job is to find \
which papers from the show's back catalogue genuinely speak to it, so the hosts can \
refer to those earlier episodes in passing.

THIS EPISODE'S PAPER
Title: {paper_title}
{query}

BACK CATALOGUE (papers the show has already covered)
{candidates}

Pick at most {max_n} connections. Judge substance, not surface topic overlap: a real \
connection is one where knowing the earlier paper changes how a listener understands \
this one. Classify each as exactly one of:
- extends          — this paper builds on or generalises the earlier one
- tension          — the two disagree, or one complicates the other's conclusion
- shared-method    — the same method or measurement strategy, applied differently
- shared-context   — the same population, platform, election or event
- precursor        — the earlier paper set up the problem this one takes on
- application      — this paper puts the earlier one's idea to work

Rules:
- Returning an EMPTY list is a correct and expected answer. Most papers have one \
or two real connections; some have none. A forced connection is worse than none, \
and vague pairings ("both are about social media") are forced connections.
- You know nothing about a back-catalogue paper beyond the key claims listed above. \
Do not use outside knowledge of it, and do not infer findings it might have.
- Every connection must be anchored in one specific claim on each side.
- A candidate marked NOTE is one the research vault has already linked to this paper. Treat that as a strong hint, not an instruction: it means someone judged the two related, but you still have to find the specific claims that carry the link, and you should drop it if you cannot.

Respond with JSON only, no prose, no code fence:

{{"connections": [
  {{"bibtex_key": "<key exactly as bracketed above>",
    "relationship": "<one of the six labels>",
    "claim": "<one sentence stating precisely what the relationship is>",
    "anchor_new": "<the claim from THIS episode's paper the link rests on>",
    "anchor_related": "<verbatim, one of the key claims listed for that paper>"}}
]}}"""


def _parse_response(raw: str, shortlist: list[str], max_n: int) -> list[dict]:
    """Pull the connections array out of the model's reply, forgivingly."""
    text = raw.strip()
    if text.startswith("```"):
        text = text.split("\n", 1)[-1].rsplit("```", 1)[0]
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end == -1:
        return []
    try:
        payload = json.loads(text[start:end + 1])
    except ValueError:
        return []
    allowed = set(shortlist)
    picks = []
    for item in payload.get("connections") or []:
        if not isinstance(item, dict):
            continue
        key = item.get("bibtex_key")
        # A key outside the shortlist is a hallucination, not a suggestion.
        if key not in allowed or any(p["bibtex_key"] == key for p in picks):
            continue
        if item.get("relationship") not in _RELATIONSHIPS:
            continue
        if not item.get("claim") or not item.get("anchor_related"):
            continue
        picks.append({
            "bibtex_key": key,
            "relationship": item["relationship"],
            "claim": item["claim"],
            "anchor_new": item.get("anchor_new", ""),
            "anchor_related": item["anchor_related"],
        })
        if len(picks) >= max_n:
            break
    return picks


def select_related(
    paper_id: str,
    paper_title: str,
    bundle: Optional[dict],
    script_generator,
    model: str,
    summary: Optional[dict] = None,
    content_text: str = "",
    paper_text: str = "",
    max_n: int = 3,
) -> list[dict]:
    """Return an anchored connections brief for `paper_id`, possibly empty.

    `script_generator` is the ClaudeScriptGenerator, reused for its client and
    its retry-on-transient-error wrapper rather than opening a second client.
    """
    if not bundle:
        return []

    episodes = _covered_episodes()
    own_key = paper_id.split(":", 1)[-1]
    candidates = {
        key: entry for key, entry in bundle.items()
        if key in episodes and key != own_key
    }
    if len(candidates) < MIN_POOL:
        print(f"  Related work: only {len(candidates)} covered papers to draw on, skipping")
        return []

    query = _query_text(paper_title, summary, content_text, paper_text)
    if len(_tokens(query)) < 20:
        print("  Related work: too little text to match on, skipping")
        return []

    # The vault usually has a note for this paper by the time we podcast it —
    # the podcast queue runs many papers deep while fg-zettelkasten's cron
    # sweeps the whole feed — so its `## Connections` section is normally
    # available, and it is a curated judgement made with the whole vault in
    # view. Seed the shortlist with it, keep BM25 for the rest, and fall back
    # to BM25 alone when no note exists yet.
    own_note = bundle.get(own_key) or {}
    curated = [k for k in own_note.get("connections", []) if k in candidates]
    ranked = _bm25_rank(query, candidates, SHORTLIST_SIZE, own_note.get("topics"))
    shortlist = curated + [k for k in ranked if k not in curated]
    shortlist = shortlist[:SHORTLIST_SIZE]
    if len(shortlist) < MIN_POOL:
        print("  Related work: prefilter found no comparable papers, skipping")
        return []
    print(f"  Related work: {len(shortlist)} candidates "
          f"({len(curated)} linked by the vault, {len(shortlist) - len(curated)} by relevance)")

    prompt = _build_prompt(
        paper_title, query[:6000],
        _render_candidates(shortlist, bundle, episodes, frozenset(curated)), max_n,
    )
    try:
        raw = script_generator.complete(
            model=model,
            # The default connections model is Opus, which runs adaptive
            # thinking, and thinking counts against max_tokens. The JSON itself
            # is under 1000 tokens; the rest is headroom so a long deliberation
            # cannot truncate the reply into unparseable JSON.
            max_tokens=8000,
            messages=[{"role": "user", "content": prompt}],
        )
    except Exception as exc:
        print(f"  Related work: connections call failed ({exc})")
        return []

    picks = _parse_response(raw or "", shortlist, max_n)

    # Episode identity is stamped in from our own records, never generated —
    # an invented episode title is a hallucination a listener can check.
    for pick in picks:
        entry = bundle[pick["bibtex_key"]]
        episode = episodes[pick["bibtex_key"]]
        pick["title"] = entry["title"]
        pick["spoken_reference"] = spoken_reference(entry)
        pick["episode_title"] = _episode_name(episode.title)
        pick["episode_pub_date"] = episode.pub_date.strftime("%B %Y")

    if picks:
        print(f"  Related work: {len(picks)} connection(s) — "
              + ", ".join(f"{p['bibtex_key']} ({p['relationship']})" for p in picks))
    else:
        print("  Related work: no connection worth making")
    return picks
