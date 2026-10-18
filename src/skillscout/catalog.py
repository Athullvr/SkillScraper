"""Bounded HTTP adapter for the documented skills.sh v1 API."""

from __future__ import annotations

import json
import random
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx
from pydantic import HttpUrl, ValidationError

from skillscout.config import Config
from skillscout.models import AuditFinding, CatalogSkill, SkillDetail, utc_now


class CatalogError(RuntimeError):
    """Base error for a catalog request or invalid response."""


class CatalogAuthenticationError(CatalogError):
    """The catalog requires credentials that are not configured or accepted."""


class CatalogUnavailableError(CatalogError):
    """The catalog could not be reached after bounded retries."""


class CatalogDataError(CatalogError):
    """The catalog returned a malformed or oversized response."""


class _ResponseStatusError(CatalogError):
    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"HTTP {status_code}")


class CatalogClient:
    """Skills.sh client with explicit timeouts, size caps, and bounded retries."""

    TRANSIENT_STATUS_CODES = {429, 500, 502, 503, 504}

    def __init__(
        self,
        config: Config,
        *,
        client: httpx.Client | None = None,
        sleep=time.sleep,
        random_value=random.random,
    ) -> None:
        self.config = config
        self._sleep = sleep
        self._random_value = random_value
        timeout = httpx.Timeout(
            connect=config.connect_timeout_seconds,
            read=config.read_timeout_seconds,
            write=5.0,
            pool=5.0,
        )
        self._client = client or httpx.Client(timeout=timeout, follow_redirects=False)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> CatalogClient:
        return self

    def __exit__(self, *_: object) -> None:
        self.close()

    def search(self, query: str, *, limit: int = 10) -> list[CatalogSkill]:
        """Search by task/query text and validate every returned stable ID."""
        if len(query.strip()) < 2:
            raise CatalogDataError("catalog search query must contain at least two characters")
        if not 1 <= limit <= 200:
            raise CatalogDataError("catalog search limit must be between 1 and 200")
        payload = self._get_json("/api/v1/skills/search", params={"q": query, "limit": limit})
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise CatalogDataError("catalog search response is missing its data array")
        results: list[CatalogSkill] = []
        try:
            for raw in payload["data"]:
                item = CatalogSkill.model_validate(raw)
                if item.id != f"{item.source}/{item.slug}":
                    raise CatalogDataError(f"catalog returned an inconsistent stable ID: {item.id}")
                if item.source_url is None:
                    encoded = "/".join(
                        quote(part, safe="") for part in [*item.source.split("/"), item.slug]
                    )
                    item.source_url = HttpUrl(f"{self.config.catalog_base_url}/{encoded}")
                item.retrieved_at = utc_now()
                results.append(item)
        except ValidationError as exc:
            raise CatalogDataError("catalog search response did not match the expected schema") from exc
        return results

    def get_detail(self, skill: CatalogSkill) -> SkillDetail:
        """Fetch a skill's file tree using source and slug path components."""
        path = self._skill_path(skill.source, skill.slug)
        payload = self._get_json(f"/api/v1/skills/{path}")
        try:
            detail = SkillDetail.model_validate(payload)
        except ValidationError as exc:
            raise CatalogDataError(f"catalog detail for {skill.id} did not match the expected schema") from exc
        if detail.id != skill.id or detail.source != skill.source or detail.slug != skill.slug:
            raise CatalogDataError(f"catalog detail identity did not match requested skill {skill.id}")
        return detail

    def get_audits(
        self, skill: CatalogSkill
    ) -> tuple[list[AuditFinding], str, list[str]]:
        """Return audit evidence without making audit availability a search blocker."""
        path = self._skill_path(skill.source, skill.slug)
        try:
            payload = self._get_json(f"/api/v1/skills/audit/{path}")
        except CatalogAuthenticationError:
            return [], "unavailable", ["External audit endpoint requires credentials or rejected the configured token."]
        except _ResponseStatusError as exc:
            if exc.status_code == 404:
                return [], "none", ["No external audit results are available in the catalog."]
            return [], "unavailable", [f"External audit lookup was unavailable (HTTP {exc.status_code})."]
        except CatalogError as exc:
            return [], "unavailable", [f"External audit lookup was unavailable: {exc}."]

        if not isinstance(payload, dict) or payload.get("id") != skill.id:
            return [], "unavailable", ["External audit response did not identify the requested skill."]
        raw_audits = payload.get("audits")
        if not isinstance(raw_audits, list):
            return [], "unavailable", ["External audit response did not contain a valid audits array."]
        try:
            top_level_revision = payload.get("hash") or payload.get("revision") or payload.get("contentHash")
            findings = []
            for item in raw_audits:
                finding = AuditFinding.model_validate(item)
                if finding.revision is None and isinstance(top_level_revision, str):
                    finding.revision = top_level_revision
                findings.append(finding)
        except ValidationError:
            return [], "unavailable", ["External audit response contained invalid evidence fields."]
        if not findings:
            return [], "none", ["No external audit results are available in the catalog."]
        return findings, "available", []

    @staticmethod
    def _skill_path(source: str, slug: str) -> str:
        source_parts = source.split("/")
        if not source_parts or any(not part or part in {".", ".."} for part in source_parts):
            raise CatalogDataError("invalid skill source path")
        if not slug or "/" in slug or slug in {".", ".."}:
            raise CatalogDataError("invalid skill slug")
        return "/".join(quote(part, safe="") for part in [*source_parts, slug])

    def _get_json(self, endpoint: str, *, params: dict[str, str | int] | None = None) -> object:
        url = f"{self.config.catalog_base_url}{endpoint}"
        headers = {"Accept": "application/json", "User-Agent": "SkillScout/0.1.0"}
        if self.config.catalog_token:
            headers["Authorization"] = f"Bearer {self.config.catalog_token}"

        for attempt in range(self.config.retry_count + 1):
            try:
                with self._client.stream("GET", url, params=params, headers=headers) as response:
                    if response.status_code in self.TRANSIENT_STATUS_CODES:
                        raise _ResponseStatusError(
                            response.status_code,
                            self._retry_after(response.headers.get("Retry-After")),
                        )
                    if response.status_code in {401, 403}:
                        raise CatalogAuthenticationError(
                            "skills.sh rejected the request; configure SKILLSCOUT_CATALOG_TOKEN "
                            "or VERCEL_OIDC_TOKEN if this endpoint requires authentication"
                        )
                    if response.status_code >= 400:
                        raise _ResponseStatusError(response.status_code)
                    body = self._read_bounded(response)
                try:
                    return json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CatalogDataError("catalog returned invalid JSON") from exc
            except _ResponseStatusError as exc:
                if exc.status_code not in self.TRANSIENT_STATUS_CODES or attempt >= self.config.retry_count:
                    if exc.status_code == 429:
                        raise CatalogUnavailableError("catalog rate limit remained after retries") from exc
                    raise CatalogUnavailableError(
                        f"catalog request failed with HTTP {exc.status_code} after bounded retries"
                    ) from exc
                self._wait_before_retry(attempt, exc.retry_after)
            except (httpx.TimeoutException, httpx.NetworkError) as exc:
                if attempt >= self.config.retry_count:
                    raise CatalogUnavailableError("catalog request failed after bounded retries") from exc
                self._wait_before_retry(attempt, None)
        raise CatalogUnavailableError("catalog request could not be completed")

    def _read_bounded(self, response: httpx.Response) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > self.config.max_response_bytes:
                    raise CatalogDataError("catalog response exceeded the configured size limit")
            except ValueError:
                pass
        body = bytearray()
        for chunk in response.iter_bytes():
            body.extend(chunk)
            if len(body) > self.config.max_response_bytes:
                raise CatalogDataError("catalog response exceeded the configured size limit")
        return bytes(body)

    def _wait_before_retry(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            if retry_after > 300:
                raise CatalogUnavailableError(
                    "catalog Retry-After exceeds the 300-second bounded wait; request was not retried early"
                )
            delay = max(retry_after, 0.0)
        else:
            delay = min(0.25 * (2**attempt) + self._random_value() * 0.1, 2.0)
        self._sleep(delay)

    @staticmethod
    def _retry_after(value: str | None) -> float | None:
        if value is None:
            return None
        try:
            return max(float(value), 0.0)
        except ValueError:
            try:
                retry_at = parsedate_to_datetime(value)
                if retry_at.tzinfo is None:
                    retry_at = retry_at.replace(tzinfo=timezone.utc)
                return max((retry_at - datetime.now(timezone.utc)).total_seconds(), 0.0)
            except (TypeError, ValueError, OverflowError):
                return None

