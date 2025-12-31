"""
Script Generator - Uses Gemini API to generate conversational podcast scripts.
"""

import json
import re
from typing import Optional
import google.generativeai as genai

from config import GEMINI_API_KEY


# Initialize Gemini
genai.configure(api_key=GEMINI_API_KEY)


PODCAST_PROMPT = """You are creating a podcast script for a discussion about an academic paper.
The podcast features two hosts:
- Speaker R (Host): Introduces topics, asks clarifying questions, and guides the conversation
- Speaker S (Expert): Provides explanations, insights, and deeper analysis

Create an engaging, educational dialogue that:
1. Opens with a brief, catchy introduction of the paper's topic
2. Explains the key concepts in accessible language
3. Discusses the methodology and findings
4. Covers the implications and significance
5. Ends with key takeaways

Guidelines:
- Keep it conversational and natural, not like reading a paper
- Use analogies and examples to explain complex concepts
- Include natural reactions ("That's fascinating!", "Right, exactly")
- Avoid jargon when possible, explain technical terms when needed
- Total length should be 10-15 minutes when spoken (roughly 1500-2500 words total)
- Each speaker turn should be 1-4 sentences for natural flow

Output ONLY a valid JSON object in this exact format (no markdown, no explanation):
{
  "multiSpeakerMarkup": {
    "turns": [
      {"text": "Welcome to Research Radio...", "speaker": "R"},
      {"text": "Thanks for having me...", "speaker": "S"},
      ...
    ]
  }
}

Paper title: {title}
Authors: {authors}

Paper content:
{content}
"""


def generate_script(
    title: str,
    authors: list[str],
    paper_text: str,
    max_retries: int = 3
) -> Optional[dict]:
    """
    Generate a podcast script from paper content using Gemini.

    Returns the script in multiSpeakerMarkup format for Google TTS.
    """
    # Prepare the prompt
    authors_str = ", ".join(authors) if authors else "Unknown"
    prompt = PODCAST_PROMPT.format(
        title=title,
        authors=authors_str,
        content=paper_text
    )

    model = genai.GenerativeModel('gemini-3-flash-preview')

    for attempt in range(max_retries):
        try:
            response = model.generate_content(
                prompt,
                generation_config=genai.types.GenerationConfig(
                    temperature=0.7,
                    max_output_tokens=8192,
                )
            )

            # Extract the JSON from the response
            script = parse_script_response(response.text)
            if script:
                return script

            print(f"Attempt {attempt + 1}: Failed to parse script, retrying...")

        except Exception as e:
            print(f"Attempt {attempt + 1}: Error generating script: {e}")
            if attempt == max_retries - 1:
                raise

    return None


def parse_script_response(response_text: str) -> Optional[dict]:
    """Parse the JSON script from Gemini's response."""
    # Try to parse directly first
    try:
        return json.loads(response_text)
    except json.JSONDecodeError:
        pass

    # Try to extract JSON from markdown code block
    json_match = re.search(r'```(?:json)?\s*(\{.*?\})\s*```', response_text, re.DOTALL)
    if json_match:
        try:
            return json.loads(json_match.group(1))
        except json.JSONDecodeError:
            pass

    # Try to find JSON object in the text
    json_match = re.search(r'\{[\s\S]*"multiSpeakerMarkup"[\s\S]*\}', response_text)
    if json_match:
        try:
            # Find the matching closing brace
            text = json_match.group(0)
            depth = 0
            end_pos = 0
            for i, char in enumerate(text):
                if char == '{':
                    depth += 1
                elif char == '}':
                    depth -= 1
                    if depth == 0:
                        end_pos = i + 1
                        break
            if end_pos > 0:
                return json.loads(text[:end_pos])
        except json.JSONDecodeError:
            pass

    print(f"Could not parse JSON from response: {response_text[:500]}...")
    return None


def validate_script(script: dict) -> bool:
    """Validate that the script has the expected structure."""
    if not isinstance(script, dict):
        return False

    markup = script.get('multiSpeakerMarkup')
    if not isinstance(markup, dict):
        return False

    turns = markup.get('turns')
    if not isinstance(turns, list) or len(turns) < 4:
        return False

    for turn in turns:
        if not isinstance(turn, dict):
            return False
        if 'text' not in turn or 'speaker' not in turn:
            return False
        if turn['speaker'] not in ('R', 'S'):
            return False

    return True


def estimate_duration(script: dict) -> int:
    """Estimate podcast duration in seconds based on word count."""
    total_words = 0

    for turn in script.get('multiSpeakerMarkup', {}).get('turns', []):
        text = turn.get('text', '')
        total_words += len(text.split())

    # Assume ~150 words per minute speaking rate
    duration_minutes = total_words / 150
    return int(duration_minutes * 60)
