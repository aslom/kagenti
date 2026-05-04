# Copyright 2025 IBM Corp.
# Licensed under the Apache License, Version 2.0

"""
A2A Chat API endpoints.

Provides endpoints for chatting with A2A agents using the Agent-to-Agent protocol.
"""

import logging
from typing import Optional, List
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

from app.core.auth import require_roles, get_required_user, ROLE_VIEWER, ROLE_OPERATOR, TokenData
from app.core.config import settings
from app.services.kubernetes import KubernetesService, get_kubernetes_service
from app.utils.routes import resolve_agent_url

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/chat", tags=["chat"])

# A2A protocol constants
A2A_AGENT_CARD_PATH = "/.well-known/agent-card.json"


class ChatMessage(BaseModel):
    """A chat message."""

    role: str  # "user" or "assistant"
    content: str


class AgentCardResponse(BaseModel):
    """Simplified agent card response."""

    name: str
    description: Optional[str] = None
    version: str
    url: str
    streaming: bool = False
    skills: List[dict] = []


class ChatRequest(BaseModel):
    """Request to chat with an A2A agent."""

    message: str
    session_id: Optional[str] = None


class ChatResponse(BaseModel):
    """Response from A2A agent chat."""

    content: str
    session_id: str
    is_complete: bool = True
    username: Optional[str] = None


@router.get(
    "/{namespace}/{name}/agent-card",
    response_model=AgentCardResponse,
    dependencies=[Depends(require_roles(ROLE_VIEWER))],
)
async def get_agent_card(
    namespace: str,
    name: str,
    kube: KubernetesService = Depends(get_kubernetes_service),
) -> AgentCardResponse:
    """
    Fetch the A2A agent card for an agent.

    The agent card describes the agent's capabilities, skills, and metadata.
    All agents are reached via their cluster-internal URL through AuthBridge.
    """
    agent_url = resolve_agent_url(name, namespace, kube)
    card_url = f"{agent_url}{A2A_AGENT_CARD_PATH}"

    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            response = await client.get(card_url)
            response.raise_for_status()
            card_data = response.json()

            # Parse capabilities
            capabilities = card_data.get("capabilities", {})
            streaming = capabilities.get("streaming", False)

            # Parse skills
            skills = []
            for skill in card_data.get("skills", []):
                skills.append(
                    {
                        "id": skill.get("id", ""),
                        "name": skill.get("name", ""),
                        "description": skill.get("description", ""),
                        "examples": skill.get("examples", []),
                    }
                )

            return AgentCardResponse(
                name=card_data.get("name", name),
                description=card_data.get("description"),
                version=card_data.get("version", "unknown"),
                url=card_data.get("url", agent_url),
                streaming=streaming,
                skills=skills,
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error fetching agent card: {e}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Failed to fetch agent card: {e.response.text}",
        )
    except httpx.RequestError as e:
        logger.error(f"Request error fetching agent card: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to agent at {agent_url}",
        )
    except Exception as e:
        logger.error(f"Unexpected error fetching agent card: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error fetching agent card: {str(e)}",
        )


@router.post(
    "/{namespace}/{name}/send",
    response_model=ChatResponse,
    dependencies=[Depends(require_roles(ROLE_OPERATOR))],
)
async def send_message(
    namespace: str,
    name: str,
    request: ChatRequest,
    http_request: Request,
    user: TokenData = Depends(get_required_user),
    kube: KubernetesService = Depends(get_kubernetes_service),
) -> ChatResponse:
    """
    Send a message to an A2A agent and get the response.

    This endpoint sends a message using the A2A protocol and returns
    the agent's response. For streaming agents, use the /stream endpoint.

    Forwards the Authorization header from the client to the agent for
    authenticated requests.
    """
    agent_url = resolve_agent_url(name, namespace, kube)
    session_id = request.session_id or uuid4().hex

    # Build A2A message payload
    message_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/send",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": request.message}],
                "messageId": uuid4().hex,
            },
        },
    }

    # Prepare headers with optional Authorization
    headers = {"Content-Type": "application/json"}
    authorization = http_request.headers.get("Authorization")
    if authorization:
        headers["Authorization"] = authorization
        logger.info("Forwarding Authorization header to agent")

    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            response = await client.post(
                agent_url,
                json=message_payload,
                headers=headers,
            )
            response.raise_for_status()
            result = response.json()

            # Extract response content from A2A response
            content = ""
            if "result" in result:
                result_data = result["result"]
                # Handle Task response
                if "status" in result_data and "message" in result_data.get("status", {}):
                    parts = result_data["status"]["message"].get("parts", [])
                    for part in parts:
                        if isinstance(part, dict) and "text" in part:
                            content += part["text"]
                        elif hasattr(part, "text"):
                            content += part.text
                # Handle direct message response
                elif "parts" in result_data:
                    for part in result_data["parts"]:
                        if isinstance(part, dict) and "text" in part:
                            content += part["text"]

            if "error" in result:
                error = result["error"]
                content = f"Error: {error.get('message', 'Unknown error')}"

            return ChatResponse(
                content=content or "No response from agent",
                session_id=session_id,
                is_complete=True,
                username=user.username,
            )

    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error sending message: {e}")
        raise HTTPException(
            status_code=e.response.status_code,
            detail=f"Agent returned error: {e.response.text}",
        )
    except httpx.RequestError as e:
        logger.error(f"Request error sending message: {e}")
        raise HTTPException(
            status_code=503,
            detail=f"Failed to connect to agent at {agent_url}",
        )
    except Exception as e:
        logger.error(f"Unexpected error sending message: {e}")
        raise HTTPException(
            status_code=500,
            detail=f"Error sending message: {str(e)}",
        )


def _extract_text_from_parts(parts: list) -> str:
    """Extract text content from A2A message parts."""
    import json as json_module

    content = ""
    for part in parts:
        if isinstance(part, dict):
            # Handle simple text field
            if "text" in part:
                content += part["text"]
            # Handle kind=text format
            elif part.get("kind") == "text":
                content += part.get("text", "")
            # Handle data field (for JSON, images, etc.)
            elif "data" in part:
                data = part["data"]
                if isinstance(data, dict):
                    if "content_type" in data and "content" in data:
                        content_type = data.get("content_type", "")
                        content_value = data.get("content", "")
                        if content_type == "application/json" and content_value:
                            try:
                                json_data = json_module.loads(content_value)
                                formatted = json_module.dumps(json_data, indent=2)
                                content += f"\n```json\n{formatted}\n```\n"
                            except json_module.JSONDecodeError:
                                content += f"\n{content_value}\n"
                        elif not content_type.startswith("image/"):
                            content += f"\n{content_value}\n"
                    else:
                        formatted = json_module.dumps(data, indent=2)
                        content += f"\n```json\n{formatted}\n```\n"
                elif isinstance(data, str):
                    try:
                        json_data = json_module.loads(data)
                        formatted = json_module.dumps(json_data, indent=2)
                        content += f"\n```json\n{formatted}\n```\n"
                    except (json_module.JSONDecodeError, TypeError):
                        content += f"\n{data}\n"
                elif isinstance(data, (list, int, float, bool)):
                    formatted = json_module.dumps(data, indent=2)
                    content += f"\n```json\n{formatted}\n```\n"
    return content


async def _stream_from_response(
    client: httpx.AsyncClient,
    response: httpx.Response,
    session_id: str,
    username: Optional[str] = None,
):
    """Stream SSE events from an already-connected agent response.

    Owns closing both the response and client when done.
    """
    import json

    try:
        if response.status_code >= 400:
            try:
                await response.aread()
                detail = response.text[:500]
            except Exception:
                detail = str(response.status_code)
            logger.error(f"Agent error: {response.status_code}: {detail}")
            yield f"data: {json.dumps({'error': f'Agent error: {response.status_code}', 'session_id': session_id})}\n\n"
            return

        logger.debug("Connected to agent, status=%d", response.status_code)

        _sidecar_mgr = None
        if getattr(settings, "kagenti_feature_flag_sidecars", False):
            try:
                from app.services.sidecar_manager import get_sidecar_manager

                _sidecar_mgr = get_sidecar_manager()
            except ImportError:
                pass

        async for line in response.aiter_lines():
            if not line:
                continue

            if line.startswith("data: "):
                data = line[6:]
                if data == "[DONE]":
                    done_payload = {"done": True, "session_id": session_id}
                    if username:
                        done_payload["username"] = username
                    yield f"data: {json.dumps(done_payload)}\n\n"
                    break

                try:
                    chunk = json.loads(data)

                    if _sidecar_mgr is not None:
                        try:
                            _sidecar_mgr.fan_out_event(session_id, chunk)
                        except Exception:
                            logger.debug("Sidecar fan-out failed", exc_info=True)

                    if "result" not in chunk:
                        continue

                    result = chunk["result"]
                    payload = {"session_id": session_id}
                    if username:
                        payload["username"] = username

                    if "artifact" in result:
                        artifact = result.get("artifact", {})
                        parts = artifact.get("parts", [])
                        content = _extract_text_from_parts(parts)
                        payload["event"] = {
                            "type": "artifact",
                            "taskId": result.get("taskId", ""),
                            "name": artifact.get("name"),
                            "index": artifact.get("index"),
                        }
                        if content:
                            payload["content"] = content
                        yield f"data: {json.dumps(payload)}\n\n"

                    elif "status" in result and "taskId" in result:
                        status = result["status"]
                        is_final = result.get("final", False)
                        state = status.get("state", "UNKNOWN")

                        status_message = ""
                        if "message" in status and status["message"]:
                            parts = status["message"].get("parts", [])
                            status_message = _extract_text_from_parts(parts)

                        event_type = "status"
                        if state == "INPUT_REQUIRED":
                            event_type = "hitl_request"

                        payload["event"] = {
                            "type": event_type,
                            "taskId": result.get("taskId", ""),
                            "state": state,
                            "final": is_final,
                            "message": status_message if status_message else None,
                        }
                        if is_final or state in ["COMPLETED", "FAILED"]:
                            if status_message:
                                payload["content"] = status_message
                        yield f"data: {json.dumps(payload)}\n\n"

                    elif "id" in result and "status" in result:
                        task_status = result["status"]
                        state = task_status.get("state", "UNKNOWN")
                        payload["event"] = {
                            "type": "status",
                            "taskId": result.get("id", ""),
                            "state": state,
                            "final": state in ["COMPLETED", "FAILED"],
                        }
                        if state in ["COMPLETED", "FAILED"]:
                            if "message" in task_status and task_status["message"]:
                                parts = task_status["message"].get("parts", [])
                                content = _extract_text_from_parts(parts)
                                if content:
                                    payload["content"] = content
                        yield f"data: {json.dumps(payload)}\n\n"

                    elif "parts" in result:
                        content = _extract_text_from_parts(result["parts"])
                        message_id = result.get("messageId", "")
                        payload["event"] = {
                            "type": "status",
                            "taskId": message_id,
                            "state": "WORKING",
                            "final": False,
                            "message": content if content else None,
                        }
                        if content:
                            payload["content"] = content
                        yield f"data: {json.dumps(payload)}\n\n"

                    else:
                        logger.warning(f"Unknown result structure: keys={list(result.keys())}")

                except json.JSONDecodeError as e:
                    logger.warning(f"Failed to parse SSE data: {data[:200]}, error: {e}")
                    continue

    except httpx.RequestError as e:
        error_msg = f"Connection error: {str(e)}"
        logger.error(error_msg)
        yield f"data: {json.dumps({'error': error_msg, 'session_id': session_id})}\n\n"
    except Exception as e:
        error_msg = f"Unexpected error: {str(e)}"
        logger.error(error_msg, exc_info=True)
        yield f"data: {json.dumps({'error': error_msg, 'session_id': session_id})}\n\n"
    finally:
        await response.aclose()
        await client.aclose()


@router.post("/{namespace}/{name}/stream", dependencies=[Depends(require_roles(ROLE_OPERATOR))])
async def stream_message(
    namespace: str,
    name: str,
    request: ChatRequest,
    http_request: Request,
    user: TokenData = Depends(get_required_user),
    kube: KubernetesService = Depends(get_kubernetes_service),
):
    """
    Send a message to an A2A agent and stream the response.

    This endpoint uses Server-Sent Events (SSE) to stream the agent's
    response in real-time. Requires an agent that supports streaming.

    Forwards the Authorization header from the client to the agent for
    authenticated requests.

    Returns HTTP 401 directly when the agent rejects the token, enabling
    the frontend to trigger token refresh and retry transparently.
    """
    agent_url = resolve_agent_url(name, namespace, kube)
    session_id = request.session_id or uuid4().hex

    # Extract Authorization header if present
    authorization = http_request.headers.get("Authorization")

    # Pre-flight: open the streaming connection and check for auth errors
    # before committing to the StreamingResponse (which locks HTTP 200).
    # This allows the frontend to see a real HTTP 401 and trigger token
    # refresh (e.g., when a new agent's audience scope was added after login).
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
    }
    if authorization:
        headers["Authorization"] = authorization

    message_payload = {
        "jsonrpc": "2.0",
        "id": str(uuid4()),
        "method": "message/stream",
        "params": {
            "message": {
                "role": "user",
                "parts": [{"kind": "text", "text": request.message}],
                "messageId": uuid4().hex,
            },
        },
    }

    logger.info(f"Starting A2A stream to {agent_url} with session_id={session_id}")

    client = httpx.AsyncClient(timeout=120.0)
    try:
        response = await client.send(
            client.build_request("POST", agent_url, json=message_payload, headers=headers),
            stream=True,
        )
    except httpx.RequestError as e:
        await client.aclose()
        raise HTTPException(status_code=503, detail=f"Cannot connect to agent: {e}")

    if response.status_code == 401:
        await response.aclose()
        await client.aclose()
        raise HTTPException(status_code=401, detail="Agent rejected token (audience mismatch)")

    return StreamingResponse(
        _stream_from_response(client, response, session_id, user.username),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
