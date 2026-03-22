from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request

from proxy_agent.core.config import Settings
from proxy_agent.schemas.openai import openai_error_payload


def get_app_settings(request: Request) -> Settings:
    return request.app.state.settings


async def verify_bearer(
    settings: Annotated[Settings, Depends(get_app_settings)],
    authorization: Annotated[str | None, Header()] = None,
) -> None:
    if not settings.api_key:
        return
    if authorization is None or not authorization.startswith("Bearer "):
        raise HTTPException(
            status_code=401,
            detail=openai_error_payload(
                "Missing or invalid Authorization header",
                type_="authentication_error",
            ),
        )
    token = authorization.removeprefix("Bearer ").strip()
    if token != settings.api_key:
        raise HTTPException(
            status_code=401,
            detail=openai_error_payload("Invalid API key", type_="authentication_error"),
        )
