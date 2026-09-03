"""
Portfolio chatbot — Kiwi — Gemini free tier.
"""

import os
from pathlib import Path
from typing import Literal

from google import genai
from google.genai import types
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

MODEL = "gemini-1.5-flash"
MAX_TOKENS = 500
MAX_MESSAGES = 40
MAX_CHARS_PER_MESSAGE = 2000

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])

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


def _to_genai_contents(messages: list[Message]) -> list[types.Content]:
    contents = []
    for m in messages:
        role = "model" if m.role == "assistant" else "user"
        contents.append(types.Content(role=role, parts=[types.Part(text=m.content)]))
    return contents


@router.post("/chat")
@limiter.limit("15/minute;100/hour")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:

    contents = _to_genai_contents(body.messages)

    def generate():
        try:
            response = client.models.generate_content_stream(
                model=MODEL,
                contents=contents,
                config=types.GenerateContentConfig(
                    system_instruction=SYSTEM_PROMPT,
                    max_output_tokens=MAX_TOKENS,
                ),
            )
            for chunk in response:
                if chunk.text:
                    # escape newlines so SSE frames stay intact
                    safe = chunk.text.replace("\n", "\\n")
                    yield f"data: {safe}\n\n"
            yield "data: [DONE]\n\n"
        except Exception as e:
            yield f"data: [ERROR]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
