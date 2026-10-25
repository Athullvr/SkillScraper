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

MAX_AUDIT_FINDINGS = 100


class CatalogError(RuntimeError):
    """Base error for a catalog request or invalid response."""


class CatalogAuthenticationError(CatalogError):
    """The catalog requires credentials that are not configured or accepted."""


class CatalogCredentialsMissingError(CatalogAuthenticationError):
    """The catalog requires a bearer credential that is not configured."""


class CatalogCredentialsRejectedError(CatalogAuthenticationError):
    """The configured credential was rejected or lacks access to the endpoint."""


class CatalogUnavailableError(CatalogError):
    """The catalog could not be reached after bounded retries."""


class CatalogTimeoutError(CatalogUnavailableError):
    """The catalog did not complete within its bounded request window."""


class CatalogRateLimitError(CatalogUnavailableError):
    """The catalog continued rate limiting the request after bounded retries."""


class CatalogServiceError(CatalogUnavailableError):
    """The catalog service returned a transient server failure after bounded retries."""


class CatalogHTTPError(CatalogError):
    """The catalog rejected a request with a permanent HTTP response."""

    def __init__(self, status_code: int, message: str | None = None) -> None:
        self.status_code = status_code
        super().__init__(message or f"catalog rejected the request (HTTP {status_code})")


class CatalogNotFoundError(CatalogHTTPError):
    """The requested catalog resource does not exist."""


class CatalogDataError(CatalogError):
    """The catalog returned a malformed or oversized response."""


class _ResponseStatusError(CatalogError):
    def __init__(self, status_code: int, retry_after: float | None = None) -> None:
        self.status_code = status_code
        self.retry_after = retry_after
        super().__init__(f"HTTP {status_code}")


class _RequestDeadlineExceeded(httpx.TimeoutException):
    """Internal signal raised when a response exceeds the total request window."""


class CatalogSearchResults(list[CatalogSkill]):
    """Validated results plus whether the catalog indicated a complete search page.

    This remains a ``list`` so existing callers that consume catalog results keep
    the same interface.
    """

    def __init__(self, values=(), *, complete: bool = True) -> None:
        super().__init__(values)
        self.complete = complete


class CatalogClient:
    """Skills.sh client with explicit timeouts, size caps, and bounded retries."""

    TRANSIENT_STATUS_CODES = {408, 425, 429, 500, 502, 503, 504}

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

    def search(self, query: str, *, limit: int = 10) -> CatalogSearchResults:
        """Search by task/query text and validate every returned stable ID."""
        if len(query.strip()) < 2:
            raise CatalogDataError("catalog search query must contain at least two characters")
        if not 1 <= limit <= 200:
            raise CatalogDataError("catalog search limit must be between 1 and 200")
        payload = _validate_success_envelope(
            self._get_json("/api/v1/skills/search", params={"q": query, "limit": limit}),
            operation="search",
        )
        if not isinstance(payload.get("data"), list):
            raise CatalogDataError("catalog search response is missing its data array")
        if len(payload["data"]) > limit:
            raise CatalogDataError("catalog search returned more results than the requested limit")
        results: list[CatalogSkill] = []
        try:
            for raw in payload["data"]:
                item = CatalogSkill.model_validate(raw)
                if item.id != f"{item.source}/{item.slug}":
                    raise CatalogDataError("catalog returned an inconsistent stable ID")
                if item.source_url is None:
                    encoded = "/".join(
                        quote(part, safe="") for part in [*item.source.split("/"), item.slug]
                    )
                    item.source_url = HttpUrl(f"{self.config.catalog_base_url}/{encoded}")
                item.retrieved_at = utc_now()
                results.append(item)
        except ValidationError as exc:
            raise CatalogDataError(
                "catalog search response did not match the expected schema"
            ) from exc
        return CatalogSearchResults(
            results,
            complete=_search_page_is_complete(payload, returned_count=len(results), limit=limit),
        )

    def get_detail(self, skill: CatalogSkill) -> SkillDetail:
        """Fetch a skill's file tree using source and slug path components."""
        path = self._skill_path(skill.source, skill.slug)
        payload = _validate_success_envelope(
            self._get_json(f"/api/v1/skills/{path}"), operation="detail"
        )
        try:
            detail = SkillDetail.model_validate(payload)
        except ValidationError as exc:
            raise CatalogDataError("catalog detail did not match the expected schema") from exc
        if detail.id != skill.id or detail.source != skill.source or detail.slug != skill.slug:
            raise CatalogDataError("catalog detail identity did not match the requested skill")
        return detail

    def get_audits(
        self, skill: CatalogSkill
    ) -> tuple[list[AuditFinding], str, list[str]]:
        """Return audit evidence without making audit availability a search blocker."""
        path = self._skill_path(skill.source, skill.slug)
        try:
            payload = _validate_success_envelope(
                self._get_json(f"/api/v1/skills/audit/{path}"), operation="audit"
            )
        except CatalogCredentialsMissingError:
            return [], "unavailable", [
                "External audit lookup was skipped because catalog credentials are not configured; "
                "set VERCEL_OIDC_TOKEN or SKILLSCOUT_CATALOG_TOKEN."
            ]
        except CatalogCredentialsRejectedError:
            return [], "unavailable", [
                "External audit lookup failed because the configured catalog credential was "
                "rejected or lacks access."
            ]
        except CatalogAuthenticationError:
            return [], "unavailable", [
                "External audit endpoint requires authentication; configure an authorized "
                "catalog credential."
            ]
        except CatalogNotFoundError:
            return [], "none", ["No external audit results are available in the catalog."]
        except CatalogError as exc:
            return [], "unavailable", [
                f"External audit lookup was unavailable: {exc}."
            ]

        if payload.get("id") != skill.id:
            return [], "unavailable", [
                "External audit response did not identify the requested skill."
            ]
        raw_audits = payload.get("audits")
        if not isinstance(raw_audits, list):
            return [], "unavailable", [
                "External audit response did not contain a valid audits array."
            ]
        if len(raw_audits) > MAX_AUDIT_FINDINGS:
            return [], "unavailable", [
                "External audit response exceeded the supported finding count."
            ]
        try:
            top_level_revision = (
                payload.get("hash") or payload.get("revision") or payload.get("contentHash")
            )
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
        if not self.config.catalog_token:
            raise CatalogCredentialsMissingError(
                "catalog credentials are not configured; set VERCEL_OIDC_TOKEN or "
                "SKILLSCOUT_CATALOG_TOKEN before catalog-backed requests"
            )
        url = f"{self.config.catalog_base_url}{endpoint}"
        headers = {"Accept": "application/json", "User-Agent": "SkillScout/0.1.0"}
        headers["Authorization"] = f"Bearer {self.config.catalog_token}"
        request_timeout = self.config.request_timeout_seconds
        timeout = httpx.Timeout(
            request_timeout,
            connect=min(self.config.connect_timeout_seconds, request_timeout),
            read=min(self.config.read_timeout_seconds, request_timeout),
            write=min(5.0, request_timeout),
            pool=min(5.0, request_timeout),
        )

        for attempt in range(self.config.retry_count + 1):
            deadline = time.monotonic() + request_timeout
            try:
                with self._client.stream(
                    "GET", url, params=params, headers=headers, timeout=timeout
                ) as response:
                    if response.status_code in self.TRANSIENT_STATUS_CODES:
                        raise _ResponseStatusError(
                            response.status_code,
                            self._retry_after(response.headers.get("Retry-After")),
                        )
                    if 300 <= response.status_code < 400:
                        raise CatalogHTTPError(
                            response.status_code,
                            "catalog redirected the request "
                            f"(HTTP {response.status_code}); redirects are disabled",
                        )
                    if response.status_code in {401, 403}:
                        if self.config.catalog_token:
                            raise CatalogCredentialsRejectedError(
                                "catalog rejected the configured credential or denied access; "
                                "verify the token and its permissions"
                            )
                        raise CatalogCredentialsMissingError(
                            "catalog requires a bearer credential; configure "
                            "VERCEL_OIDC_TOKEN or SKILLSCOUT_CATALOG_TOKEN"
                        )
                    if response.status_code == 404:
                        raise CatalogNotFoundError(404, "catalog resource was not found (HTTP 404)")
                    if (
                        response.status_code >= 400
                        and response.status_code not in self.TRANSIENT_STATUS_CODES
                    ):
                        raise CatalogHTTPError(response.status_code)
                    if response.status_code >= 400:
                        raise _ResponseStatusError(response.status_code)
                    body = self._read_bounded(response, deadline=deadline)
                if time.monotonic() > deadline:
                    raise _RequestDeadlineExceeded("catalog request exceeded its total timeout")
                try:
                    return json.loads(body)
                except (UnicodeDecodeError, json.JSONDecodeError) as exc:
                    raise CatalogDataError("catalog returned invalid JSON") from exc
            except _ResponseStatusError as exc:
                if (
                    exc.status_code not in self.TRANSIENT_STATUS_CODES
                    or attempt >= self.config.retry_count
                ):
                    if exc.status_code == 429:
                        raise CatalogRateLimitError(
                            "catalog rate limit remained after bounded retries; "
                            "wait before retrying"
                        ) from exc
                    if exc.status_code >= 500:
                        raise CatalogServiceError(
                            f"catalog service returned HTTP {exc.status_code} after bounded retries"
                        ) from exc
                    raise CatalogUnavailableError(
                        f"catalog returned transient HTTP {exc.status_code} after bounded retries"
                    ) from exc
                try:
                    self._wait_before_retry(attempt, exc.retry_after)
                except CatalogUnavailableError as wait_error:
                    if exc.status_code == 429:
                        raise CatalogRateLimitError(
                            "catalog rate limit requested a wait beyond SkillScout's "
                            "bounded retry window"
                        ) from wait_error
                    raise
            except httpx.DecodingError as exc:
                raise CatalogDataError("catalog response encoding could not be decoded") from exc
            except (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError) as exc:
                if attempt >= self.config.retry_count:
                    if isinstance(exc, _RequestDeadlineExceeded):
                        raise CatalogTimeoutError(
                            "catalog request exceeded its configured timeout after bounded retries"
                        ) from exc
                    if isinstance(exc, httpx.TimeoutException):
                        raise CatalogTimeoutError(
                            "catalog request timed out after bounded retries"
                        ) from exc
                    raise CatalogUnavailableError(
                        "catalog request failed after bounded retries"
                    ) from exc
                self._wait_before_retry(attempt, None)
            except httpx.TransportError as exc:
                raise CatalogUnavailableError(
                    "catalog request could not be sent; verify the catalog URL and "
                    "local HTTP configuration"
                ) from exc
        raise CatalogUnavailableError("catalog request could not be completed")

    def _read_bounded(
        self, response: httpx.Response, *, deadline: float | None = None
    ) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                if int(content_length) > self.config.max_response_bytes:
                    raise CatalogDataError("catalog response exceeded the configured size limit")
            except ValueError:
                pass
        body = bytearray()
        for chunk in response.iter_bytes():
            if deadline is not None and time.monotonic() > deadline:
                raise _RequestDeadlineExceeded("catalog response exceeded its total timeout")
            body.extend(chunk)
            if len(body) > self.config.max_response_bytes:
                raise CatalogDataError("catalog response exceeded the configured size limit")
        return bytes(body)

    def _wait_before_retry(self, attempt: int, retry_after: float | None) -> None:
        if retry_after is not None:
            if retry_after > 300:
                raise CatalogUnavailableError(
                    "catalog Retry-After exceeds the 300-second bounded wait; "
                    "request was not retried early"
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


def _search_page_is_complete(payload: dict, *, returned_count: int, limit: int) -> bool:
    """Conservatively detect explicit pagination or a full, potentially truncated page."""
    metadata = [payload]
    for key in ("meta", "pagination"):
        value = payload.get(key)
        if value is not None:
            if not isinstance(value, dict):
                raise CatalogDataError(f"catalog search {key} metadata must be an object")
            metadata.append(value)

    explicit_complete: bool | None = None
    incomplete_marker = False
    for item in metadata:
        if "complete" in item:
            complete = item["complete"]
            if type(complete) is not bool:
                raise CatalogDataError("catalog search complete metadata must be a boolean")
            explicit_complete = (
                complete if explicit_complete is None else explicit_complete and complete
            )
        for key in ("hasMore", "has_more"):
            if key in item:
                has_more = item[key]
                if type(has_more) is not bool:
                    raise CatalogDataError(f"catalog search {key} metadata must be a boolean")
                incomplete_marker = incomplete_marker or has_more
                page_complete = not has_more
                explicit_complete = (
                    page_complete
                    if explicit_complete is None
                    else explicit_complete and page_complete
                )
        for key in ("next", "nextCursor", "next_cursor", "nextPage", "next_page"):
            if key in item:
                has_next_page = item[key] not in (None, "", False)
                incomplete_marker = incomplete_marker or has_next_page
                page_complete = not has_next_page
                explicit_complete = (
                    page_complete
                    if explicit_complete is None
                    else explicit_complete and page_complete
                )
        for key in ("total", "totalCount", "total_count"):
            if key in item:
                total = item[key]
                if type(total) is not int or total < 0:
                    raise CatalogDataError(
                        f"catalog search {key} metadata must be a non-negative integer"
                    )
                if total < returned_count:
                    raise CatalogDataError(
                        f"catalog search {key} metadata is smaller than the returned data array"
                    )
                incomplete_marker = incomplete_marker or total > returned_count
                page_complete = total == returned_count
                explicit_complete = (
                    page_complete
                    if explicit_complete is None
                    else explicit_complete and page_complete
                )
    if explicit_complete is False:
        incomplete_marker = True
    page_may_be_truncated = returned_count >= limit and explicit_complete is not True
    incomplete = incomplete_marker or page_may_be_truncated
    return not incomplete


def _validate_success_envelope(payload: object, *, operation: str) -> dict:
    """Reject common API error envelopes before accepting endpoint-specific data."""
    if not isinstance(payload, dict):
        raise CatalogDataError(f"catalog {operation} response must be a JSON object")
    if "success" in payload and type(payload["success"]) is not bool:
        raise CatalogDataError(f"catalog {operation} response success marker must be a boolean")
    if payload.get("success") is False:
        raise CatalogDataError(f"catalog {operation} response signaled failure")
    if payload.get("error") not in (None, ""):
        raise CatalogDataError(f"catalog {operation} response contained an error envelope")
    if payload.get("errors") not in (None, "", []):
        raise CatalogDataError(f"catalog {operation} response contained an errors envelope")
    return payload

