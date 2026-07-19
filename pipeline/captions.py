"""Platform-tailored copy from the human's clip description, via the
Anthropic API.

One call per clip returns an Instagram caption plus a YouTube title and
description as validated JSON. Brand voice lives in an editable text file
(brand_voice.txt by default) so the POC to dojo transition is a config
change, not a code change.
"""

import json

import anthropic

from pipeline import config

# Platform limits enforced after generation as a safety net.
IG_CAPTION_MAX = 2200
YT_TITLE_MAX = 100
YT_DESCRIPTION_MAX = 5000
# YouTube caps the tags field at 500 characters total (commas and the
# implicit quoting of multi-word tags count too), so stay well under.
YT_TAGS_MAX_COUNT = 15
YT_TAGS_MAX_TOTAL_CHARS = 400

OUTPUT_SCHEMA = {
    "type": "object",
    "properties": {
        "instagram_caption": {
            "type": "string",
            "description": "Instagram Reels caption with a few relevant hashtags",
        },
        "youtube_title": {
            "type": "string",
            "description": "YouTube Shorts title, under 100 characters",
        },
        "youtube_description": {
            "type": "string",
            "description": "YouTube description, must include #Shorts",
        },
        "youtube_tags": {
            "type": "array",
            "items": {"type": "string"},
            "description": "YouTube search tags, plain words or phrases without #",
        },
    },
    "required": [
        "instagram_caption", "youtube_title", "youtube_description", "youtube_tags",
    ],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """You write social media copy for short vertical martial arts videos.

You will get a one or two line description of a clip, written by the person
who filmed it. From it, produce copy for two platforms:

1. instagram_caption: an Instagram Reels caption. Hashtag friendly: end with
   3 to 6 relevant hashtags. Stay under {ig_max} characters.
2. youtube_title: a YouTube Shorts title. Under {yt_title_max} characters,
   no hashtags in the title.
3. youtube_description: a YouTube description of one to three sentences.
   It MUST include the hashtag #Shorts.
4. youtube_tags: {tags_min} to {tags_max} search tags for the YouTube video.
   Plain words or short phrases, no # symbol. Mix broad discovery terms
   (the martial art, the sport) with specific ones from the clip
   (the technique shown, the drill, the skill level).

Follow this brand voice exactly:

{brand_voice}"""


def generate_captions(description: str) -> dict:
    """Return {"instagram_caption", "youtube_title", "youtube_description"}
    for one clip description. Raises on API or parsing failure; the caller
    decides how to handle that (the clip must never publish with bad copy).
    """
    client = anthropic.Anthropic(api_key=config.ANTHROPIC_API_KEY)

    system = SYSTEM_PROMPT.format(
        ig_max=IG_CAPTION_MAX,
        yt_title_max=YT_TITLE_MAX,
        tags_min=8,
        tags_max=YT_TAGS_MAX_COUNT,
        brand_voice=_load_brand_voice(),
    )

    response = client.messages.create(
        model=config.CAPTION_MODEL,
        max_tokens=2048,
        thinking={"type": "adaptive"},
        system=system,
        output_config={"format": {"type": "json_schema", "schema": OUTPUT_SCHEMA}},
        messages=[{"role": "user", "content": f"Clip description: {description}"}],
    )

    if response.stop_reason == "refusal":
        raise RuntimeError("Caption model refused the request")

    text = next(b.text for b in response.content if b.type == "text")
    copy = json.loads(text)
    return _enforce_limits(copy)


def _enforce_limits(copy: dict) -> dict:
    if "#shorts" not in copy["youtube_description"].lower():
        copy["youtube_description"] = copy["youtube_description"].rstrip() + " #Shorts"
    copy["instagram_caption"] = copy["instagram_caption"][:IG_CAPTION_MAX]
    copy["youtube_title"] = copy["youtube_title"][:YT_TITLE_MAX]
    copy["youtube_description"] = copy["youtube_description"][:YT_DESCRIPTION_MAX]
    copy["youtube_tags"] = _clean_tags(copy.get("youtube_tags", []))
    return copy


def _clean_tags(tags: list) -> list[str]:
    """Dedupe, strip stray # prefixes, and stay under YouTube's caps.
    Tags are optional metadata, so a malformed list degrades to fewer
    tags rather than failing the clip.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    total = 0
    for tag in tags:
        if not isinstance(tag, str):
            continue
        tag = tag.strip().lstrip("#").strip()
        key = tag.lower()
        if not tag or key in seen:
            continue
        # Multi-word tags count their wrapping quotes toward the limit.
        cost = len(tag) + (2 if " " in tag else 0)
        if total + cost > YT_TAGS_MAX_TOTAL_CHARS:
            break
        seen.add(key)
        cleaned.append(tag)
        total += cost
        if len(cleaned) >= YT_TAGS_MAX_COUNT:
            break
    return cleaned


def _load_brand_voice() -> str:
    with open(config.BRAND_VOICE_FILE, encoding="utf-8") as fh:
        return fh.read().strip()
