"""
MCP server at /api/mcp: streamable HTTP, stateless, JSON responses
authenticated with the TA API token ('Token <key>' or 'Bearer <key>')
"""

import json

from common.views_base import AdminOnly
from django.conf import settings
from django.core.serializers.json import DjangoJSONEncoder
from drf_spectacular.utils import extend_schema
from mcp_server.tools import BY_NAME, ToolError, describe
from rest_framework.authentication import TokenAuthentication
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

PROTOCOL_VERSIONS = ["2025-11-25", "2025-06-18", "2025-03-26"]
SERVER_INFO = {
    "name": "tubearchivist",
    "title": "TubeArchivist",
    "version": settings.TA_VERSION,
}
INSTRUCTIONS = (
    "TubeArchivist archives YouTube videos. Typical flow for 'add this "
    "link and download anything over an hour': preview_url with "
    'rule {"min_duration": 3600} to see what matches, add_url with '
    "mode once and the same rule, then activity until the downloads are "
    "done. Found videos wait in list_found; download or ignore them by id."
)


class BearerAuthentication(TokenAuthentication):
    """the same TA token, as 'Authorization: Bearer <key>'"""

    keyword = "Bearer"


def _error(msg_id, code: int, message: str) -> dict:
    return {
        "jsonrpc": "2.0",
        "id": msg_id,
        "error": {"code": code, "message": message},
    }


def _result(msg_id, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def _initialize(params: dict) -> dict:
    asked = params.get("protocolVersion")
    version = asked if asked in PROTOCOL_VERSIONS else PROTOCOL_VERSIONS[0]
    return {
        "protocolVersion": version,
        "capabilities": {"tools": {"listChanged": False}},
        "serverInfo": SERVER_INFO,
        "instructions": INSTRUCTIONS,
    }


def call_tool(params: dict) -> dict:
    """tools/call: errors the agent can fix come back as isError"""
    tool = BY_NAME.get(params.get("name"))
    if not tool:
        raise KeyError(params.get("name"))
    try:
        data = tool["handler"](params.get("arguments") or {})
    except ToolError as err:
        return {
            "content": [{"type": "text", "text": str(err)}],
            "isError": True,
        }
    structured = json.loads(json.dumps(data, cls=DjangoJSONEncoder))
    return {
        "content": [{"type": "text", "text": json.dumps(structured)}],
        "structuredContent": structured,
        "isError": False,
    }


def handle(message) -> dict | None:
    """one JSON-RPC message; None for notifications"""
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return _error(None, -32600, "invalid request")
    msg_id, method = message.get("id"), message.get("method")
    if msg_id is None:
        return None  # notification, e.g. notifications/initialized
    params = message.get("params") or {}
    if method == "initialize":
        return _result(msg_id, _initialize(params))
    if method == "ping":
        return _result(msg_id, {})
    if method == "tools/list":
        return _result(msg_id, {"tools": describe()})
    if method == "tools/call":
        try:
            return _result(msg_id, call_tool(params))
        except KeyError:
            return _error(
                msg_id, -32602, f"unknown tool: {params.get('name')}"
            )
    return _error(msg_id, -32601, f"method not found: {method}")


@extend_schema(exclude=True)
class McpView(APIView):
    """resolves to /api/mcp
    POST: JSON-RPC messages; GET: no server-initiated stream (405)
    """

    authentication_classes = [TokenAuthentication, BearerAuthentication]
    permission_classes = [AdminOnly]
    parser_classes = [JSONParser]

    def post(self, request):
        """answer one message or a batch"""
        origin = request.headers.get("Origin")
        if origin and origin not in settings.CSRF_TRUSTED_ORIGINS:
            return Response({"error": "origin not allowed"}, status=403)
        body = request.data
        if isinstance(body, list):
            replies = [r for r in map(handle, body) if r is not None]
            return Response(replies) if replies else Response(status=202)
        reply = handle(body)
        if reply is None:
            return Response(status=202)
        return Response(reply)

    def get(self, request):
        """no SSE stream"""
        del request
        return Response(status=405, headers={"Allow": "POST"})
