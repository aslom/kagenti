"""Claude SDK agent with code review skill for OpenShell PoC.

Uses the Anthropic Python SDK to call Claude.  The ``ANTHROPIC_BASE_URL``
environment variable can point to LiteLLM's Anthropic pass-through endpoint
so the agent routes through the Budget Proxy instead of calling Anthropic
directly.

A2A exposure is implemented manually via Starlette since the Anthropic SDK
does not include a built-in A2A wrapper (unlike Google ADK's ``to_a2a()``).
"""

import json
import os
import uuid

import anthropic
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

# ---------------------------------------------------------------------------
# Anthropic client — honours ANTHROPIC_BASE_URL for Budget Proxy routing
# ---------------------------------------------------------------------------
_base_url = os.environ.get("ANTHROPIC_BASE_URL")
client = anthropic.Anthropic(
    api_key=os.environ.get("ANTHROPIC_API_KEY", "dummy"),
    base_url=_base_url if _base_url else anthropic.NOT_GIVEN,
)

_model = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-20250514")
_port = int(os.environ.get("PORT", "8080"))

# ---------------------------------------------------------------------------
# A2A Agent Card
# ---------------------------------------------------------------------------
AGENT_CARD = {
    "name": "claude-code-reviewer",
    "description": "Reviews code using Claude and provides constructive feedback.",
    "url": "http://claude-sdk-agent.team1.svc:8080",
    "version": "0.1.0",
    "capabilities": {"streaming": False},
    "skills": [
        {
            "id": "code_review",
            "name": "Code Review",
            "description": (
                "Review code for quality, security, and best practices. "
                "Provide constructive, actionable feedback."
            ),
        },
    ],
}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
async def agent_card(request: Request) -> JSONResponse:
    """Serve the A2A agent card."""
    return JSONResponse(AGENT_CARD)


async def handle_jsonrpc(request: Request) -> JSONResponse:
    """Handle A2A JSON-RPC 2.0 requests (``message/send``)."""
    body = await request.json()
    req_id = body.get("id")
    method = body.get("method")

    if method != "message/send":
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": req_id,
                "error": {"code": -32601, "message": "Method not found"},
            }
        )

    # Extract user message text from A2A parts
    params = body.get("params", {})
    message = params.get("message", {})
    parts = message.get("parts", [])
    text = " ".join(
        p.get("text", "") for p in parts if p.get("type") == "text"
    )

    # Call Claude via the Anthropic SDK
    try:
        response = client.messages.create(
            model=_model,
            max_tokens=1024,
            system=(
                "You are a thorough, constructive code reviewer. "
                "Provide specific, actionable feedback on code quality, "
                "security, performance, and best practices. "
                "Be concise and focus on the most impactful observations."
            ),
            messages=[{"role": "user", "content": text}],
        )
        reply_text = response.content[0].text
    except Exception as e:
        reply_text = f"Error calling Claude: {e}"

    task_id = str(uuid.uuid4())
    return JSONResponse(
        {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "id": task_id,
                "status": {"state": "completed"},
                "artifacts": [
                    {"parts": [{"type": "text", "text": reply_text}]},
                ],
            },
        }
    )


# ---------------------------------------------------------------------------
# ASGI application
# ---------------------------------------------------------------------------
app = Starlette(
    routes=[
        Route("/.well-known/agent-card.json", agent_card),
        Route("/", handle_jsonrpc, methods=["POST"]),
    ],
)

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="0.0.0.0", port=_port)
