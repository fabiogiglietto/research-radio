"""
Claude Script Generator - writes the two-host podcast dialogue script.

Replaces Gemini for script generation; Gemini is kept only for TTS. The output
format contract is unchanged: plain-text lines prefixed "Host:" / "Cohost:",
which GeminiAudioGenerator.generate_audio() consumes directly for multi-speaker
synthesis.
"""

import random
import time
from typing import Optional

import anthropic

# HTTP statuses worth retrying (rate limit, overload, transient server errors).
_RETRYABLE_STATUS = {429, 500, 502, 503, 504, 529}

# fg-zettelkasten summary fields used to scaffold the discussion (in order).
_SUMMARY_FIELDS = ("key_claims", "contributions", "methods", "findings", "framing")


def _format_summary(summary: dict) -> str:
    """Render an fg-zettelkasten structured summary as a prompt scaffold."""
    lines = []
    for field in _SUMMARY_FIELDS:
        value = summary.get(field)
        if not value:
            continue
        if isinstance(value, list):
            value = "; ".join(str(v) for v in value)
        lines.append(f"{field.replace('_', ' ').title()}: {value}")
    return "\n".join(lines)


class ClaudeScriptGenerator:
    """Generates podcast scripts and episode titles with the Claude API."""

    MAX_RETRIES = 3
    RETRY_BASE_DELAY = 2  # seconds

    def __init__(self, api_key: Optional[str], model: str,
                 title_model: Optional[str] = None):
        """Initialize with the script model id and an optional Anthropic API key.

        Pass `api_key=None` (the CI path) to construct the SDK client zero-arg
        so it resolves whatever credential the environment provides — an API
        key, or a Workload Identity Federation token exchange. Passing an
        explicit api_key pins tier 1 of the SDK's credential chain, which
        "always overrides everything else" and would defeat federation.

        `title_model` (default: same as `model`) handles only the short
        episode-title call — a cheap model is fine there."""
        self.client = anthropic.Anthropic(api_key=api_key) if api_key \
            else anthropic.Anthropic()
        self.model = model
        self.title_model = title_model or model

    def _complete_with_retry(self, **kwargs) -> str:
        """Call messages.create with retry on transient API errors.

        Returns the concatenated text of the response. Uses the Anthropic SDK's
        typed exceptions (and their .status_code) rather than string matching.
        """
        for attempt in range(self.MAX_RETRIES):
            try:
                response = self.client.messages.create(**kwargs)
                return "".join(
                    block.text
                    for block in response.content
                    if getattr(block, "type", None) == "text"
                )
            except anthropic.APIStatusError as e:
                retryable = e.status_code in _RETRYABLE_STATUS
                if retryable and attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"  Retrying in {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(delay)
                    continue
                raise
            except anthropic.APIConnectionError as e:
                if attempt < self.MAX_RETRIES - 1:
                    delay = self.RETRY_BASE_DELAY * (2 ** attempt)
                    print(f"  Retrying in {delay}s (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}")
                    time.sleep(delay)
                    continue
                raise

    def generate_script(
        self,
        paper_text: str,
        paper_title: str,
        host_name: str = "Kore",
        cohost_name: str = "Charon",
        summary: Optional[dict] = None,
    ) -> Optional[str]:
        """
        Generate a podcast conversation script from paper text.

        `summary`, when given, is the fg-zettelkasten structured summary; it
        scaffolds the discussion while the full paper text remains the source
        of fidelity. Returns formatted dialogue like:
        Host: Welcome to Research Radio...
        Cohost: Great to be here...
        """
        # ~5% chance of including self-aware AI humor in this episode
        ai_humor_guideline = ""
        if random.random() < 0.05:
            ai_humor_guideline = "\n- Include one or two brief, self-aware jokes about being AI-generated hosts — e.g., a playful quip about mispronunciations, audio glitches, or the quirks of AI-generated podcasts. Keep it light, natural, and don't overdo it."

        # Optional scaffold: a structured summary from the fg-zettelkasten vault.
        scaffold = ""
        if summary:
            digest = _format_summary(summary)
            if digest:
                scaffold = (
                    "\nA structured summary of this paper, from the curated "
                    "research vault, is provided as a scaffold. Use it to shape "
                    "the arc of the discussion around the key claims, findings "
                    "and framing — but ground every specific detail (numbers, "
                    "methods, quotes) in the full paper content below.\n\n"
                    f"Structured summary:\n{digest}\n"
                )

        prompt = f"""You are a podcast script writer. Create an engaging episode of "FG's Research Radio".
This text feeds a text-to-speech engine, so whenever the hosts say the show's name aloud, spell the
"FG's" prefix phonetically as "Eff-Gee's" — i.e. write the spoken name as "Eff-Gee's Research Radio"
so the letters F and G are pronounced correctly (never write the literal "FG's", which the TTS
mispronounces as "eff-eff-gee"). It is a podcast featuring deep dive discussions on recent academic
papers in computational social science, platform studies, misinformation research, and the evolving
landscape of social media and AI.

The conversation should be between two hosts:
- Host (named {host_name}): The main host who guides the discussion and provides context
- Cohost (named {cohost_name}): A co-host who offers analysis, asks probing questions, and adds perspective

Important: This is a discussion ABOUT the paper by two podcast hosts. They are NOT the authors
and should not pretend to be. They should refer to the authors in third person (e.g., "The
researchers found..." or "According to the authors...").

Guidelines:
- Start by welcoming listeners to the show, writing its name as "Eff-Gee's Research Radio" so the TTS pronounces the FG's prefix correctly, have the hosts briefly introduce themselves by name, then introduce the paper's topic and authors
- Mention the authors by name naturally in the conversation (e.g., "As Boyd argues..." or "The team led by Ferrara found...")
- Explain the key findings and methodology in accessible terms
- Have both hosts share insights and build on each other's points
- Discuss implications and significance for the field
- End with takeaways for the audience
- At the very end, the host should remind listeners that if they want to read the full paper, they can find the complete reference in the episode description, and encourage them to subscribe on Spotify and Apple Podcasts
- Use natural, conversational language{ai_humor_guideline}
- Target length: 8-12 minutes of dialogue (roughly 1200-1800 words)
- Format each line exactly as "Host: [dialogue]" or "Cohost: [dialogue]"
{scaffold}
Paper Title: {paper_title}

Paper Content:
{paper_text[:60000]}

Generate the podcast script now:"""

        try:
            return self._complete_with_retry(
                model=self.model,
                max_tokens=8192,
                messages=[{"role": "user", "content": prompt}],
            ).strip()
        except Exception as e:
            print(f"Error generating script: {e}")
            return None

    def generate_episode_title(self, script: str, paper_title: str) -> Optional[str]:
        """
        Generate a podcast-style episode title based on the transcript.

        Args:
            script: The generated podcast script/transcript
            paper_title: Original paper title (for context)

        Returns:
            A catchy, podcast-appropriate episode title
        """
        prompt = f"""You are a podcast producer for "FG's Research Radio", a podcast about computational social science research.

Based on the following podcast transcript, generate a compelling episode title that:
- Is catchy and engaging for podcast listeners
- Captures the main theme or most interesting finding discussed
- Is concise (ideally 5-10 words, maximum 15 words)
- Sounds like a podcast episode title, not an academic paper title
- Does NOT start with "FG's Research Radio:" (that prefix will be added separately)

Original paper title (for context): {paper_title}

Podcast transcript:
{script[:15000]}

Generate ONLY the episode title, nothing else. No quotes, no explanation, just the title itself."""

        try:
            title = self._complete_with_retry(
                model=self.title_model,
                max_tokens=100,
                messages=[{"role": "user", "content": prompt}],
            ).strip()
            # Strip quotes and a redundant "FG's Research Radio:" prefix the
            # model sometimes adds despite the instruction not to.
            title = title.strip('"\'')
            if title.lower().startswith("fg's research radio:"):
                title = title.split(":", 1)[1].strip()
            # Reject obviously broken titles (empty, truncated, or a dangling
            # subtitle colon) so the caller falls back to the paper title — a
            # correct full title beats a mangled catchy one.
            if not title or title.endswith(":") or len(title.split()) < 3:
                print(f"  Discarding malformed episode title: {title!r}")
                return None
            return title
        except Exception as e:
            print(f"Error generating episode title: {e}")
            return None
