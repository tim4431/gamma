"""Account-only web-search settings and a fixed-query service check."""

from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, field_validator

from .. import web_search_settings
from ..auth import require_personal_user, require_user

router = APIRouter(prefix="/api/ai/web-search", tags=["ai"])


class WebSearchSettingsRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: Literal["auto", "openai", "brave", "searxng", "off"] | None = None
    openai_api_key: str | None = None
    brave_api_key: str | None = None
    clear_openai_api_key: bool = False
    clear_brave_api_key: bool = False
    openai_model: str | None = Field(default=None, max_length=100)
    openai_connection: str | None = Field(default=None, max_length=100)
    searxng_url: str | None = Field(default=None, max_length=4096)

    @field_validator("openai_api_key", "brave_api_key", mode="before")
    @classmethod
    def validate_key_input(cls, value):
        # Pydantic's default validation response includes the rejected input.
        # Keep credential errors generic, including wrong types/oversize keys.
        if value is not None and (not isinstance(value, str) or len(value) > 512):
            raise HTTPException(400, "invalid web search API key")
        return value


def _editor(request: Request) -> str:
    return require_personal_user(request, "guest accounts cannot configure or test web search services")


@router.get("")
def settings_get(request: Request):
    return web_search_settings.masked(require_user(request), can_edit=not request.state.is_guest)


@router.put("")
def settings_save(payload: WebSearchSettingsRequest, request: Request):
    user = _editor(request)
    web_search_settings.save(user, payload.model_dump(exclude_none=True))
    return web_search_settings.masked(user, can_edit=True)


@router.post("/test")
def settings_test(request: Request):
    """Test saved settings only; no document or chat text is sent."""
    from ..web_search import WebSearchError, search_web

    user = _editor(request)
    provider = ""
    try:
        provider = web_search_settings.credentials(user)["provider"]
        results = search_web("Raman sideband cooling paper", limit=1, user=user)
    except (WebSearchError, web_search_settings.SearchConfigurationError) as error:
        return {"ok": False, "provider": provider, "error": str(error), "code": error.code}
    return {"ok": True, "provider": provider, "count": len(results)}
