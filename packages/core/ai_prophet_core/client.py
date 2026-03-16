"""Server API Client for Prophet Arena Core API.

Typed, retry-safe HTTP client.
"""

from __future__ import annotations

import logging
import random
import time
from datetime import datetime
from typing import TypeVar

import httpx
from pydantic import BaseModel, ValidationError

from .client_models import (
    CandidatesResponse,
    ClaimTickRequest,
    ClaimTickResponse,
    CompleteTickResponse,
    CreateExperimentRequest,
    CreateExperimentResponse,
    FinalizeRequest,
    FinalizeResponse,
    HealthResponse,
    PlanRequest,
    PutPlanResponse,
    PortfolioResponse,
    ProgressResponse,
    ReasoningResponse,
    TradeIntentBatchRequest,
    TradeIntentRequest,
    TradeSubmissionResult,
    UpsertParticipantRequest,
    UpsertParticipantResponse,
)

logger = logging.getLogger(__name__)
ResponseModelT = TypeVar("ResponseModelT", bound=BaseModel)


class APIError(Exception):
    def __init__(self, message: str, status_code: int | None = None):
        super().__init__(message)
        self.status_code = status_code


class APIConnectionError(APIError):
    pass


class APITimeoutError(APIError):
    pass


class APIValidationError(APIError):
    pass


class APIServerError(APIError):
    pass


class APIClientError(APIError):
    pass


class ServerAPIClient:
    """HTTP client for Prophet Arena Core API."""

    def __init__(
        self,
        base_url: str,
        api_key: str | None = None,
        timeout: int = 30,
        max_retries: int = 3,
        retry_backoff: float = 1.0,
    ):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key.strip() if api_key else None
        self.timeout = timeout
        self.max_retries = max_retries
        self.retry_backoff = retry_backoff
        self.client = self._build_http_client()

    def _build_http_client(self) -> httpx.Client:
        headers: dict[str, str] = {}
        if self.api_key:
            headers["X-API-Key"] = self.api_key
        return httpx.Client(
            base_url=self.base_url,
            timeout=self.timeout,
            follow_redirects=True,
            headers=headers,
        )

    def _reset_client(self) -> None:
        """Reset the underlying HTTP client after transport-level failures."""
        try:
            self.client.close()
        except Exception:
            # Best-effort close; replace regardless.
            pass
        self.client = self._build_http_client()

    def _compute_retry_delay(
        self, attempt: int, response: httpx.Response | None = None
    ) -> float:
        """Compute retry delay with Retry-After + jittered backoff.

        Preference order:
        1) Retry-After header (if present and valid)
        2) exponential backoff based on client config
        """
        if response is not None:
            retry_after = response.headers.get("Retry-After")
            if retry_after:
                try:
                    # Retry-After is usually integer seconds.
                    base = float(retry_after)
                    if base >= 0:
                        return base + random.uniform(0.0, 0.5)
                except ValueError:
                    pass

        base = self.retry_backoff * (2 ** attempt)

        # For explicit overload signals, enforce a modest floor to reduce churn.
        if response is not None and response.status_code == 503:
            body = (response.text or "").lower()
            if "database busy" in body:
                base = max(base, 5.0)

        return base + random.uniform(0.0, 0.5)

    def _request(self, method: str, path: str, **kwargs) -> httpx.Response:
        last_error: Exception | None = None
        for attempt in range(self.max_retries):
            try:
                response = self.client.request(method, path, **kwargs)
                if response.status_code >= 500:
                    last_error = APIServerError(
                        f"Server error {response.status_code}: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                    if attempt < self.max_retries - 1:
                        delay = self._compute_retry_delay(attempt, response=response)
                        logger.warning(
                            "Server %s on %s %s; retrying in %.2fs (%d/%d)",
                            response.status_code,
                            method,
                            path,
                            delay,
                            attempt + 1,
                            self.max_retries - 1,
                        )
                        time.sleep(delay)
                        continue
                    raise last_error
                if response.status_code == 429:
                    last_error = APIClientError(
                        f"Rate limited 429: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                    if attempt < self.max_retries - 1:
                        delay = self._compute_retry_delay(attempt, response=response)
                        logger.warning(
                            "Rate limited on %s %s; retrying in %.2fs (%d/%d)",
                            method,
                            path,
                            delay,
                            attempt + 1,
                            self.max_retries - 1,
                        )
                        time.sleep(delay)
                        continue
                    raise last_error
                if response.status_code >= 400:
                    raise APIClientError(
                        f"Client error {response.status_code}: {response.text[:200]}",
                        status_code=response.status_code,
                    )
                return response
            except httpx.ConnectError as e:
                last_error = APIConnectionError(f"Connection failed: {e}")
            except httpx.TimeoutException as e:
                last_error = APITimeoutError(f"Timeout: {e}")
            except httpx.TransportError as e:
                # Covers intermittent network/protocol errors such as
                # RemoteProtocolError ("server disconnected without response").
                last_error = APIConnectionError(f"Transport error: {e}")
                self._reset_client()
                logger.warning(
                    "Transport error on %s %s (%s); reset connection pool",
                    method,
                    path,
                    type(e).__name__,
                )
            except (APIServerError, APIClientError):
                raise
            if attempt < self.max_retries - 1:
                time.sleep(self._compute_retry_delay(attempt))
        raise last_error or APIError(f"Request failed after {self.max_retries} attempts")

    def _parse_response(
        self,
        response: httpx.Response,
        model_cls: type[ResponseModelT],
    ) -> ResponseModelT:
        try:
            payload = response.json()
        except ValueError as e:
            raise APIValidationError(
                f"Invalid JSON response for {model_cls.__name__}: {e}",
                status_code=response.status_code,
            ) from e

        try:
            return model_cls.model_validate(payload)
        except ValidationError as e:
            raise APIValidationError(
                f"Invalid {model_cls.__name__} response payload: {e}",
                status_code=response.status_code,
            ) from e

    def _get(self, path: str, params: dict | None = None) -> httpx.Response:
        return self._request("GET", path, params=params)

    def _post(self, path: str, json: dict | None = None) -> httpx.Response:
        return self._request("POST", path, json=json)

    def _put(self, path: str, json: dict | None = None) -> httpx.Response:
        return self._request("PUT", path, json=json)

    # --- Health ---------------------------------------------------------------

    def health_check(self) -> HealthResponse:
        response = self._get("/health")
        return self._parse_response(response, HealthResponse)

    # --- Experiments ----------------------------------------------------------

    def create_or_get_experiment(
        self, slug: str, config_hash: str, config_json: dict, n_ticks: int,
    ) -> CreateExperimentResponse:
        req = CreateExperimentRequest(
            experiment_slug=slug,
            config_hash=config_hash,
            config_json=config_json,
            n_ticks=n_ticks,
        )
        response = self._post("/experiments", json=req.model_dump(mode="json"))
        return self._parse_response(response, CreateExperimentResponse)

    def upsert_participant(
        self, experiment_id: str, model: str, rep: int = 0, starting_cash: float = 10000.0,
    ) -> UpsertParticipantResponse:
        req = UpsertParticipantRequest(model=model, rep=rep, starting_cash=starting_cash)
        response = self._post(
            f"/experiments/{experiment_id}/participants:upsert",
            json=req.model_dump(mode="json"),
        )
        return self._parse_response(response, UpsertParticipantResponse)

    def get_progress(self, experiment_id: str) -> ProgressResponse:
        response = self._get(f"/experiments/{experiment_id}/progress")
        return self._parse_response(response, ProgressResponse)

    def get_reasoning(
        self,
        experiment_id: str,
        participant_idx: int | None = None,
        limit: int = 100,
    ) -> ReasoningResponse:
        params: dict[str, int] = {"limit": limit}
        if participant_idx is not None:
            params["participant_idx"] = participant_idx
        response = self._get(f"/experiments/{experiment_id}/reasoning", params=params)
        return self._parse_response(response, ReasoningResponse)

    # --- Tick Leasing ---------------------------------------------------------

    def claim_tick(
        self, experiment_id: str, lease_owner_id: str, lease_sec: int = 600,
    ) -> ClaimTickResponse:
        req = ClaimTickRequest(lease_owner_id=lease_owner_id, lease_sec=lease_sec)
        response = self._post(
            f"/experiments/{experiment_id}/ticks:claim",
            json=req.model_dump(mode="json"),
        )
        return self._parse_response(response, ClaimTickResponse)

    def complete_tick(self, experiment_id: str, tick_id: str) -> CompleteTickResponse:
        response = self._post(f"/experiments/{experiment_id}/ticks/{tick_id}:complete")
        return self._parse_response(response, CompleteTickResponse)

    # --- Plan / Finalize ------------------------------------------------------

    def put_plan(
        self,
        experiment_id: str,
        participant_idx: int,
        tick_id: str,
        snapshot_id: str,
        plan_json: dict,
    ) -> PutPlanResponse:
        req = PlanRequest(snapshot_id=snapshot_id, plan_json=plan_json)
        response = self._put(
            f"/experiments/{experiment_id}/participants/{participant_idx}"
            f"/ticks/{tick_id}/plan",
            json=req.model_dump(mode="json"),
        )
        return self._parse_response(response, PutPlanResponse)

    def finalize_participant(
        self,
        experiment_id: str,
        participant_idx: int,
        tick_id: str,
        status: str,
        error_code: str | None = None,
        error_detail: str | None = None,
    ) -> FinalizeResponse:
        req = FinalizeRequest(status=status, error_code=error_code, error_detail=error_detail)
        response = self._post(
            f"/experiments/{experiment_id}/participants/{participant_idx}"
            f"/ticks/{tick_id}:finalize",
            json=req.model_dump(mode="json", exclude_none=True),
        )
        return self._parse_response(response, FinalizeResponse)

    # --- Portfolio ------------------------------------------------------------

    def get_portfolio(
        self, experiment_id: str, participant_idx: int,
    ) -> PortfolioResponse | None:
        """Fetch current portfolio state for a participant.

        Returns None when the participant has no portfolio yet (404).
        Other failures are propagated to the caller.
        """
        try:
            response = self._get(
                f"/experiments/{experiment_id}/participants/{participant_idx}/portfolio",
            )
            return self._parse_response(response, PortfolioResponse)
        except APIClientError as e:
            if e.status_code == 404:
                return None
            raise

    # --- Candidates -----------------------------------------------------------

    def get_candidates(
        self, tick_ts: datetime, snapshot_id: str | None = None,
    ) -> CandidatesResponse:
        params: dict = {"tick_ts": tick_ts.isoformat()}
        if snapshot_id:
            params["snapshot_id"] = snapshot_id
        response = self._get("/candidates", params=params)
        return self._parse_response(response, CandidatesResponse)

    # --- Trade Submission -----------------------------------------------------

    def submit_trade_intents(
        self,
        experiment_id: str,
        participant_idx: int,
        tick_id: str,
        candidate_set_id: str,
        intents: list[TradeIntentRequest],
    ) -> TradeSubmissionResult:
        req = TradeIntentBatchRequest(
            experiment_id=experiment_id,
            participant_idx=participant_idx,
            tick_id=tick_id,
            candidate_set_id=candidate_set_id,
            intents=intents,
        )
        response = self._post("/trade_intents", json=req.model_dump(mode="json"))
        return self._parse_response(response, TradeSubmissionResult)

    # --- Utilities ------------------------------------------------------------

    def close(self):
        self.client.close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        self.close()
