"""HTTP client for TypeSafe AI's System One API.

`JevClient` is the sync client and `AsyncJevClient` the async one; they have the same
methods and the same behaviour. Each offers three levels:

    evaluate(schema, state)      JSON Schema in, schema-shaped JSON out
    ask(question_set, state)     the same, reusing questions you converted once
    system_one(request)          the raw request and response models

One POST per call, no retries, no streaming: callers decide whether to retry from
`JevApiError.retryable`.
"""

import logging
import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx
from pydantic import ValidationError

from ._version import __version__
from .from_jev import DecodedResult, JevResult2JsonSchema
from .models import JsonContent, SystemOneRequest, SystemOneResponse, SystemOneUsage
from .to_jev import JSONSchema2Jev, MappingOptions, QuestionSet

logger = logging.getLogger(__name__)

JEV_BASE_URL = "https://api.typesafe.ai"
JEV_TIMEOUT_SECONDS = 60.0
DEFAULT_MODEL = "jev-latest"
API_KEY_ENV_VAR = "TYPESAFE_API_KEY"

SYSTEM_ONE_PATH = "/v1/systemone"
REQUEST_ID_HEADER = "x-typesafe-request-id"

MAX_BODY_CHARS_IN_MESSAGE = 500


class JevApiError(RuntimeError):
    """A call to TypeSafe AI failed. The message is user-facing."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None,
        retryable: bool,
        request_id: str | None = None,
    ):
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable
        self.request_id = request_id


class JevResponseError(RuntimeError):
    """A 2xx response that is not a System One response. Never worth retrying."""


@dataclass(frozen=True)
class JevResult:
    """One Jev call: the decoded output, and what the API reported alongside it."""

    decoded: DecodedResult
    response: SystemOneResponse
    question_set: QuestionSet
    """The questions that were asked. Pass it to `ask()` to reuse them."""

    @property
    def output(self) -> dict[str, Any]:
        """The decoded object. Validates against the schema the questions came from."""
        return self.decoded.output

    @property
    def probabilities(self) -> dict[str, dict[str, float]]:
        """Property -> {output value as a string: probability}."""
        return self.decoded.probabilities

    @property
    def confidence(self) -> dict[str, float | None]:
        """Property -> Jev's confidence. None for booleans and numbers."""
        return self.decoded.confidence

    @property
    def usage(self) -> SystemOneUsage:
        return self.response.usage


class _JevClientBase:
    def __init__(
        self,
        api_key: str | None = None,
        *,
        base_url: str = JEV_BASE_URL,
        timeout: float = JEV_TIMEOUT_SECONDS,
        options: MappingOptions = MappingOptions(),
    ) -> None:
        """
        Args:
            api_key: your TypeSafe AI key. Read from the `TYPESAFE_API_KEY`
                environment variable when omitted.
            options: how schemas are mapped onto questions, and answers back again.
        """
        self._api_key = _resolve_api_key(api_key)
        self._base_url = base_url
        self._timeout = timeout
        self._options = options
        self._to_jev = JSONSchema2Jev(options)
        self._from_jev = JevResult2JsonSchema(options)

    def convert(self, schema: Mapping[str, Any]) -> QuestionSet:
        """Map a schema onto Jev questions, without calling the API.

        Raises:
            IncompatibleSchemaError: listing every property that cannot be mapped.
        """
        return self._to_jev.convert(schema)

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": f"jev_jsonschema/{__version__}",
        }

    def _result(
        self, question_set: QuestionSet, response: SystemOneResponse
    ) -> JevResult:
        decoded = self._from_jev.convert(question_set, response.answers)
        return JevResult(decoded=decoded, response=response, question_set=question_set)


class JevClient(_JevClientBase):
    """A synchronous System One client.

    Connections are pooled and reused. Close it when you're done, or use it as a
    context manager:

        with JevClient() as jev:
            result = jev.evaluate(schema, state="...")
    """

    _client: httpx.Client | None = None

    def evaluate(
        self,
        schema: Mapping[str, Any],
        state: JsonContent,
        *,
        model: str = DEFAULT_MODEL,
    ) -> JevResult:
        """Ask Jev about `state`, with one question per property of `schema`.

        Raises:
            IncompatibleSchemaError: the schema has properties Jev cannot answer.
            JevApiError: transport failure or a non-2xx response.
            JevResponseError: a 2xx response that is not a System One response.
            UnexpectedAnswerError: an answer does not fit its question.
        """
        return self.ask(self.convert(schema), state, model=model)

    def ask(
        self,
        question_set: QuestionSet,
        state: JsonContent,
        *,
        model: str = DEFAULT_MODEL,
    ) -> JevResult:
        """Ask an already-converted `QuestionSet` about `state`.

        Converting a schema is pure work, so hoist it out of your hot path and call
        this instead of `evaluate`.
        """
        response = self.system_one(question_set.request(state, model))
        return self._result(question_set, response)

    def system_one(self, request: SystemOneRequest) -> SystemOneResponse:
        """Ask Jev every question in the request against its state.

        Raises:
            JevApiError: transport failure or a non-2xx response.
            JevResponseError: a 2xx response that is not a System One response.
        """
        try:
            response = self._http().post(
                SYSTEM_ONE_PATH, json=request.to_body(), headers=self._headers()
            )
        except httpx.TimeoutException as err:
            raise _timeout_error() from err
        except httpx.TransportError as err:
            raise _transport_error() from err

        if response.is_success:
            return _parse_response(response)
        raise _error_for_response(response)

    def _http(self) -> httpx.Client:
        if self._client is None:
            self._client = httpx.Client(base_url=self._base_url, timeout=self._timeout)
        return self._client

    def close(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def __enter__(self) -> "JevClient":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self.close()


class AsyncJevClient(_JevClientBase):
    """An asynchronous System One client.

    Connections are pooled and reused. Close it when you're done, or use it as an
    async context manager:

        async with AsyncJevClient() as jev:
            result = await jev.evaluate(schema, state="...")
    """

    _client: httpx.AsyncClient | None = None

    async def evaluate(
        self,
        schema: Mapping[str, Any],
        state: JsonContent,
        *,
        model: str = DEFAULT_MODEL,
    ) -> JevResult:
        """Ask Jev about `state`, with one question per property of `schema`.

        Raises:
            IncompatibleSchemaError: the schema has properties Jev cannot answer.
            JevApiError: transport failure or a non-2xx response.
            JevResponseError: a 2xx response that is not a System One response.
            UnexpectedAnswerError: an answer does not fit its question.
        """
        return await self.ask(self.convert(schema), state, model=model)

    async def ask(
        self,
        question_set: QuestionSet,
        state: JsonContent,
        *,
        model: str = DEFAULT_MODEL,
    ) -> JevResult:
        """Ask an already-converted `QuestionSet` about `state`.

        Converting a schema is pure work, so hoist it out of your hot path and call
        this instead of `evaluate`.
        """
        response = await self.system_one(question_set.request(state, model))
        return self._result(question_set, response)

    async def system_one(self, request: SystemOneRequest) -> SystemOneResponse:
        """Ask Jev every question in the request against its state.

        Raises:
            JevApiError: transport failure or a non-2xx response.
            JevResponseError: a 2xx response that is not a System One response.
        """
        try:
            response = await self._http().post(
                SYSTEM_ONE_PATH, json=request.to_body(), headers=self._headers()
            )
        except httpx.TimeoutException as err:
            raise _timeout_error() from err
        except httpx.TransportError as err:
            raise _transport_error() from err

        if response.is_success:
            return _parse_response(response)
        raise _error_for_response(response)

    def _http(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self._base_url, timeout=self._timeout
            )
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    async def __aenter__(self) -> "AsyncJevClient":
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        await self.aclose()


def _resolve_api_key(api_key: str | None) -> str:
    key = api_key if api_key is not None else os.environ.get(API_KEY_ENV_VAR)
    if not key:
        raise ValueError(
            f"No TypeSafe AI API key. Pass api_key=..., or set {API_KEY_ENV_VAR}."
        )
    return key


def _timeout_error() -> JevApiError:
    return JevApiError(
        "Could not connect to TypeSafe AI. Check your network connection. (timed out)",
        status_code=None,
        retryable=True,
    )


def _transport_error() -> JevApiError:
    return JevApiError(
        "Could not connect to TypeSafe AI. Check your network connection.",
        status_code=None,
        retryable=True,
    )


def _parse_response(response: httpx.Response) -> SystemOneResponse:
    try:
        payload: Any = response.json()
    except ValueError as err:
        raise JevResponseError(
            "TypeSafe AI returned an unexpected response: invalid JSON"
        ) from err

    try:
        return SystemOneResponse.model_validate(payload)
    except ValidationError as err:
        errors = err.errors()
        detail = str(errors[0]["msg"]) if errors else str(err)
        raise JevResponseError(
            f"TypeSafe AI returned an unexpected response: {detail}"
        ) from err


def _error_for_response(response: httpx.Response) -> JevApiError:
    status = response.status_code
    request_id = response.headers.get(REQUEST_ID_HEADER)
    # Never log the body: it echoes the request's state and questions.
    logger.debug(
        "TypeSafe AI request failed. status=%s request_id=%s", status, request_id
    )

    message, retryable = _message_for_status(response, status)
    return JevApiError(
        message, status_code=status, retryable=retryable, request_id=request_id
    )


def _message_for_status(response: httpx.Response, status: int) -> tuple[str, bool]:
    if status in (401, 403):
        return "Authentication with TypeSafe AI failed. Check your API key.", False
    if status == 429:
        return "TypeSafe AI rate limit exceeded. Wait a moment and try again.", True
    if status == 422:
        return (
            f"TypeSafe AI rejected the request: {_validation_detail(response)}",
            False,
        )
    if 500 <= status < 600:
        return "TypeSafe AI is currently unavailable. Try again in a moment.", True
    return (
        f"TypeSafe AI rejected the request (HTTP {status}): {_truncated_body(response)}",
        False,
    )


def _validation_detail(response: httpx.Response) -> str:
    """The messages from a FastAPI-style validation body, or the raw body if it is not one."""
    try:
        detail = response.json()["detail"]
        messages = [item["msg"] for item in detail]
    except Exception:
        return _truncated_body(response)
    if not messages or not all(isinstance(message, str) for message in messages):
        return _truncated_body(response)
    return "; ".join(messages)


def _truncated_body(response: httpx.Response) -> str:
    return response.text[:MAX_BODY_CHARS_IN_MESSAGE]
