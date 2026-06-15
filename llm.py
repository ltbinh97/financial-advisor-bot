"""LLM helpers over the GreenNode AI Platform (OpenAI-compatible)."""

import json
import logging
import re

from openai import OpenAI

import config

logger = logging.getLogger("pfm.llm")

_client = OpenAI(api_key=config.LLM_API_KEY, base_url=config.LLM_BASE_URL)

# Disable Qwen-style hidden reasoning so the whole token budget yields an answer.
_NO_THINK = {"chat_template_kwargs": {"enable_thinking": False}}


def chat(system: str, user: str, max_tokens: int = 600, temperature: float = 0.6) -> str:
    resp = _client.chat.completions.create(
        model=config.LLM_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        max_tokens=max_tokens,
        temperature=temperature,
        extra_body=_NO_THINK,
    )
    return (resp.choices[0].message.content or "").strip()


def _extract_json(text: str):
    """Pull the first JSON object/array out of a model response."""
    text = text.strip()
    # Strip markdown code fences if present.
    text = re.sub(r"^```(?:json)?|```$", "", text, flags=re.MULTILINE).strip()
    try:
        return json.loads(text)
    except Exception:
        pass
    m = re.search(r"(\{.*\}|\[.*\])", text, re.DOTALL)
    if m:
        try:
            return json.loads(m.group(1))
        except Exception:
            return None
    return None


def chat_json(system: str, user: str, max_tokens: int = 500):
    """Chat and parse a JSON result. Returns None on parse failure."""
    out = chat(system + "\nLuôn trả lời DUY NHẤT bằng JSON hợp lệ, không kèm giải thích.",
               user, max_tokens=max_tokens, temperature=0.1)
    return _extract_json(out)


def vision_json(prompt: str, image_url: str, max_tokens: int = 600):
    """Send an image URL to the vision model and parse a JSON result (OCR)."""
    resp = _client.chat.completions.create(
        model=config.VISION_MODEL,
        messages=[{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": image_url}},
            ],
        }],
        max_tokens=max_tokens,
        temperature=0.1,
        extra_body=_NO_THINK,
    )
    return _extract_json(resp.choices[0].message.content or "")
