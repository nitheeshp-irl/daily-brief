from ..base import SourceError


class SessionExpired(SourceError):
    """LinkedIn is showing a login wall; the saved browser session is missing or expired."""


class ParserDrift(SourceError):
    """The page loaded but nothing could be parsed; LinkedIn's markup probably changed."""


class MCPError(SourceError):
    """The MCP server failed to start or returned an error."""
