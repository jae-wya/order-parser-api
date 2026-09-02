"""
Portfolio chatbot endpoint — Gemini free tier.

Requires in requirements.txt:
    google-generativeai
    slowapi

Requires environment variable on Render:
    GEMINI_API_KEY

Get a free key at: aistudio.google.com/app/apikey
No credit card required.

Place system_prompt.md next to this file.
"""

import os
from pathlib import Path
from typing import Literal

import google.generativeai as genai
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

MODEL = "gemini-1.5-flash"   # free tier, fast, 1M tokens/day
MAX_TOKENS = 500
MAX_MESSAGES = 40             # 20 turns
MAX_CHARS_PER_MESSAGE = 2000

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

genai.configure(api_key=os.environ["GEMINI_API_KEY"])
model = genai.GenerativeModel(
    model_name=MODEL,
    system_instruction=SYSTEM_PROMPT,
    generation_config=genai.GenerationConfig(max_output_tokens=MAX_TOKENS),
)

limiter = Limiter(key_func=get_remote_address)
router = APIRouter()


class Message(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=MAX_CHARS_PER_MESSAGE)


class ChatRequest(BaseModel):
    messages: list[Message] = Field(min_length=1, max_length=MAX_MESSAGES)

    @field_validator("messages")
    @classmethod
    def must_alternate_and_end_with_user(cls, messages: list[Message]) -> list[Message]:
        if messages[-1].role != "user":
            raise ValueError("conversation must end with a user message")
        for earlier, later in zip(messages, messages[1:]):
            if earlier.role == later.role:
                raise ValueError("roles must alternate")
        return messages


def _to_gemini_history(messages: list[Message]) -> tuple[list[dict], str]:
    """Split messages into history (all but last) and the current user turn."""
    history = []
    for m in messages[:-1]:
        # Gemini uses "model" instead of "assistant"
        role = "model" if m.role == "assistant" else "user"
        history.append({"role": role, "parts": [m.content]})
    return history, messages[-1].content


@router.post("/chat")
@limiter.limit("15/minute;100/hour")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """Stream a reply as Server-Sent Events."""

    history, user_message = _to_gemini_history(body.messages)

    def generate():
        try:
            session = model.start_chat(history=history)
            response = session.send_message(user_message, stream=True)
            for chunk in response:
                text = chunk.text
                if text:
                    # Escape newlines so SSE frames stay intact
                    safe = text.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            yield "data: [DONE]\n\n"
        except Exception:
            yield "data: [ERROR]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
