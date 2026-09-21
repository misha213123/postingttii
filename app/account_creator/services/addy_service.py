from __future__ import annotations

from typing import Any

import httpx

from app.config import settings


class AddyError(RuntimeError):
    pass


class AddyService:
    def __init__(self) -> None:
        self.base_url = settings.addy_base_url.rstrip("/")
        self.timeout = settings.addy_timeout_seconds

    @property
    def configured(self) -> bool:
        return bool(settings.addy_api_token.strip())

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            raise AddyError("ADDY_API_TOKEN не настроен")
        return {
            "Authorization": f"Bearer {settings.addy_api_token.strip()}",
            "Accept": "application/json",
            "Content-Type": "application/json",
            "X-Requested-With": "XMLHttpRequest",
        }

    async def _request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        try:
            async with httpx.AsyncClient(timeout=self.timeout) as client:
                response = await client.request(
                    method,
                    url,
                    headers=self._headers(),
                    params=params,
                    json=json,
                )
        except httpx.HTTPError as exc:
            raise AddyError(f"addy.io недоступен: {exc}") from exc

        if response.status_code >= 400:
            detail = ""
            try:
                payload = response.json()
                detail = str(
                    payload.get("message")
                    or payload.get("error")
                    or payload.get("errors")
                    or ""
                )
            except Exception:
                detail = response.text[:300]
            suffix = f": {detail}" if detail else ""
            raise AddyError(f"addy.io HTTP {response.status_code}{suffix}")

        if response.status_code == 204 or not response.content:
            return {}

        try:
            payload = response.json()
        except ValueError as exc:
            raise AddyError("addy.io вернул некорректный JSON") from exc

        if not isinstance(payload, dict):
            raise AddyError("addy.io вернул неожиданный формат ответа")
        return payload

    async def token_details(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/api-token-details")

    async def domain_options(self) -> dict[str, Any]:
        return await self._request("GET", "/api/v1/domain-options")

    async def list_aliases(self, *, page_size: int = 100) -> list[dict[str, Any]]:
        payload = await self._request(
            "GET",
            "/api/v1/aliases",
            params={
                "page[number]": 1,
                "page[size]": max(1, min(100, int(page_size))),
                "sort": "-created_at",
            },
        )
        data = payload.get("data", [])
        return data if isinstance(data, list) else []

    async def create_alias(
        self,
        *,
        domain: str = "",
        alias_format: str = "random_characters",
        local_part: str = "",
        description: str = "PostingTTII",
    ) -> dict[str, Any]:
        allowed_formats = {
            "random_characters",
            "uuid",
            "random_words",
            "random_male_name",
            "random_female_name",
            "random_noun",
            "custom",
        }
        alias_format = alias_format.strip() or "random_characters"
        if alias_format not in allowed_formats:
            raise AddyError("Неподдерживаемый формат alias")

        if not domain.strip():
            options = await self.domain_options()
            domain = str(options.get("defaultAliasDomain") or "").strip()
            if not domain:
                raise AddyError("addy.io не вернул default alias domain")

        body: dict[str, Any] = {
            "domain": domain.strip(),
            "format": alias_format,
            "description": (description or "PostingTTII")[:255],
        }
        if alias_format == "custom":
            if not local_part.strip():
                raise AddyError("Для custom alias нужен local_part")
            body["local_part"] = local_part.strip()

        payload = await self._request("POST", "/api/v1/aliases", json=body)
        data = payload.get("data")
        if not isinstance(data, dict):
            raise AddyError("addy.io не вернул созданный alias")
        return data


addy_service = AddyService()
