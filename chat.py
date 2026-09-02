"""
Portfolio chatbot endpoint.

Drop this into the existing order-parser-api service and mount it:

    from chat import router as chat_router
    app.include_router(chat_router)

Requires in requirements.txt:
    anthropic
    slowapi

Requires on Railway:
    ANTHROPIC_API_KEY

Place system_prompt.md next to this file.
"""

import os
from pathlib import Path
from typing import Literal

from anthropic import Anthropic
from fastapi import APIRouter, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, field_validator
from slowapi import Limiter
from slowapi.util import get_remote_address

MODEL = "claude-haiku-4-5"
MAX_TOKENS = 500
MAX_MESSAGES = 40          # 20 turns
MAX_CHARS_PER_MESSAGE = 2000

SYSTEM_PROMPT = (Path(__file__).parent / "system_prompt.md").read_text(encoding="utf-8")

client = Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
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


@router.post("/chat")
@limiter.limit("15/minute;100/hour")
async def chat(request: Request, body: ChatRequest) -> StreamingResponse:
    """Stream a reply as Server-Sent Events."""

    def generate():
        try:
            with client.messages.stream(
                model=MODEL,
                max_tokens=MAX_TOKENS,
                # cache_control makes repeat reads of the system prompt ~10% of
                # input price, which is most of the token spend here.
                system=[
                    {
                        "type": "text",
                        "text": SYSTEM_PROMPT,
                        "cache_control": {"type": "ephemeral"},
                    }
                ],
                messages=[m.model_dump() for m in body.messages],
            ) as stream:
                for chunk in stream.text_stream:
                    yield f"data: {chunk}\n\n"
            yield "data: [DONE]\n\n"
        except Exception:
            # Never leak internals to the browser.
            yield "data: [ERROR]\n\n"

    return StreamingResponse(
        generate(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
