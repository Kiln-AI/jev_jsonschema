import inspect
import json
import logging
from typing import Any

import httpx
import pytest
import respx

from jev_jsonschema.client import (
    API_KEY_ENV_VAR,
    JEV_BASE_URL,
    AsyncJevClient,
    JevApiError,
    JevClient,
    JevResponseError,
)
from jev_jsonschema.models import (
    ChoiceQuestion,
    NoulQuestion,
    SystemOneRequest,
)

SYSTEM_ONE_URL = f"{JEV_BASE_URL}/v1/systemone"


# Every test runs against both clients. They are meant to behave identically, so the
# helpers below await the async one and pass the sync one straight through.
@pytest.fixture(params=[JevClient, AsyncJevClient], ids=["sync", "async"])
def client_class(request: pytest.FixtureRequest):
    return request.param


@pytest.fixture
def client(client_class: Any):
    return client_class(api_key="sk-test-key")


async def _resolve(value: Any) -> Any:
    return await value if inspect.isawaitable(value) else value


async def call_system_one(client: Any, request: SystemOneRequest) -> Any:
    return await _resolve(client.system_one(request))


async def call_evaluate(client: Any, schema: Any, state: Any, **kwargs: Any) -> Any:
    return await _resolve(client.evaluate(schema, state, **kwargs))


async def call_ask(client: Any, question_set: Any, state: Any, **kwargs: Any) -> Any:
    return await _resolve(client.ask(question_set, state, **kwargs))


def request_for(state: Any = "some content") -> SystemOneRequest:
    return SystemOneRequest(
        state=state,
        model="jev-latest",
        questions={
            "is_spam": NoulQuestion(instructions="Is this spam?"),
            "verdict": ChoiceQuestion(criteria={"pass": None, "fail": None}),
        },
    )


def success_payload() -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "is_spam": {"type": "noul", "noul": 0.05},
            "verdict": {
                "type": "choice",
                "choice": "pass",
                "confidence": 0.9,
                "probabilities": {"pass": 0.9, "fail": 0.1},
            },
            "stars": {
                "type": "score",
                "score": 4.1,
                "confidence": 0.6,
                "legend": {"0": "1", "1": "2"},
                "probabilities": {"0": 0.1, "1": 0.9},
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


@pytest.mark.parametrize(
    "state", ["some content", {"task_instructions": "judge", "input": "hi"}]
)
async def test_request_shape(client: Any, state: Any):
    with respx.mock:
        route = respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        await call_system_one(client, request_for(state))

        sent = route.calls.last.request
        assert sent.url.path == "/v1/systemone"
        assert sent.headers["authorization"] == "Bearer sk-test-key"
        assert sent.headers["content-type"] == "application/json"
        assert sent.headers["accept"] == "application/json"
        assert sent.headers["user-agent"].startswith("jev_jsonschema/")
        assert json.loads(sent.content) == {
            "state": state,
            "model": "jev-latest",
            "questions": {
                # Serialized with exclude_none: no unset instructions or criteria, but
                # the choice's None option descriptions are still there.
                "is_spam": {"type": "noul", "instructions": "Is this spam?"},
                "verdict": {
                    "type": "choice",
                    "criteria": {"pass": None, "fail": None},
                },
            },
        }


async def test_success_parses_response(client: Any):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        response = await call_system_one(client, request_for())

    assert response.model == "jev-1.13.0"
    assert response.answers["is_spam"].noul == 0.05
    assert response.answers["verdict"].choice == "pass"
    assert response.answers["stars"].score == 4.1
    assert response.usage.input_tokens == 120
    assert response.usage.output_tokens == 12


@pytest.mark.parametrize(
    "status,body,expected_message,retryable",
    [
        (
            401,
            {"detail": "nope"},
            "Authentication with TypeSafe AI failed. Check your API key.",
            False,
        ),
        (
            403,
            {"detail": "nope"},
            "Authentication with TypeSafe AI failed. Check your API key.",
            False,
        ),
        (
            429,
            {"detail": "slow down"},
            "TypeSafe AI rate limit exceeded. Wait a moment and try again.",
            True,
        ),
        (
            422,
            {
                "detail": [
                    {"loc": ["body", "questions"], "msg": "too many questions"},
                    {"loc": ["body", "state"], "msg": "state too large"},
                ]
            },
            "TypeSafe AI rejected the request: too many questions; state too large",
            False,
        ),
        (
            422,
            {"detail": []},
            'TypeSafe AI rejected the request: {"detail":[]}',
            False,
        ),
        (
            422,
            {"message": "unprocessable"},
            'TypeSafe AI rejected the request: {"message":"unprocessable"}',
            False,
        ),
        (
            400,
            {"error": "bad model"},
            'TypeSafe AI rejected the request (HTTP 400): {"error":"bad model"}',
            False,
        ),
        (
            404,
            {"error": "no such route"},
            'TypeSafe AI rejected the request (HTTP 404): {"error":"no such route"}',
            False,
        ),
        (
            500,
            {"error": "boom"},
            "TypeSafe AI is currently unavailable. Try again in a moment.",
            True,
        ),
        (
            503,
            {"error": "maintenance"},
            "TypeSafe AI is currently unavailable. Try again in a moment.",
            True,
        ),
        (
            302,
            {"error": "moved"},
            'TypeSafe AI rejected the request (HTTP 302): {"error":"moved"}',
            False,
        ),
    ],
)
async def test_error_mapping(
    client: Any,
    status: int,
    body: dict[str, Any],
    expected_message: str,
    retryable: bool,
):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(return_value=httpx.Response(status, json=body))

        with pytest.raises(JevApiError) as exc_info:
            await call_system_one(client, request_for())

    assert str(exc_info.value) == expected_message
    assert exc_info.value.status_code == status
    assert exc_info.value.retryable is retryable


@pytest.mark.parametrize(
    "error,expected_message",
    [
        (
            httpx.ConnectTimeout("too slow"),
            "Could not connect to TypeSafe AI. Check your network connection. (timed out)",
        ),
        (
            httpx.ReadTimeout("too slow"),
            "Could not connect to TypeSafe AI. Check your network connection. (timed out)",
        ),
        (
            httpx.ConnectError("no route"),
            "Could not connect to TypeSafe AI. Check your network connection.",
        ),
    ],
)
async def test_transport_errors_are_retryable(
    client: Any, error: Exception, expected_message: str
):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(side_effect=error)

        with pytest.raises(JevApiError) as exc_info:
            await call_system_one(client, request_for())

    assert str(exc_info.value) == expected_message
    assert exc_info.value.retryable is True
    assert exc_info.value.status_code is None


async def test_request_id_captured_on_error(client: Any):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(
                500, json={}, headers={"x-typesafe-request-id": "req_abc123"}
            )
        )

        with pytest.raises(JevApiError) as exc_info:
            await call_system_one(client, request_for())

    assert exc_info.value.request_id == "req_abc123"
    # The request id is for logs, not for the user-facing message.
    assert "req_abc123" not in str(exc_info.value)


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="not json at all"),
        httpx.Response(
            200,
            json={
                "model": "jev-latest",
                "answers": {"a": {"type": "vibes", "vibes": 1}},
            },
        ),
        httpx.Response(200, json={"answers": {}}),
    ],
)
async def test_malformed_success_body_raises_response_error(
    client: Any, response: httpx.Response
):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(return_value=response)

        with pytest.raises(JevResponseError, match="unexpected response") as exc_info:
            await call_system_one(client, request_for())

    # Not a JevApiError: a malformed body is never retried.
    assert not isinstance(exc_info.value, JevApiError)


async def test_body_truncated_in_message(client: Any):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(400, text="x" * 5000)
        )

        with pytest.raises(JevApiError) as exc_info:
            await call_system_one(client, request_for())

    assert len(str(exc_info.value)) < 600
    assert str(exc_info.value).endswith("x" * 500)


async def test_custom_base_url_and_timeout(client_class: Any):
    client = client_class(api_key="k", base_url="https://example.test", timeout=1.5)

    with respx.mock:
        route = respx.post("https://example.test/v1/systemone").mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        await call_system_one(client, request_for())

    assert route.called


async def test_failure_logging_omits_key_and_body(client: Any, caplog):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(
                500,
                text="echoed state and questions",
                headers={"x-typesafe-request-id": "req_1"},
            )
        )

        with (
            caplog.at_level(logging.DEBUG, logger="jev_jsonschema.client"),
            pytest.raises(JevApiError),
        ):
            await call_system_one(client, request_for())

    logged = "\n".join(record.getMessage() for record in caplog.records)
    assert "req_1" in logged
    assert "500" in logged
    assert "sk-test-key" not in logged
    assert "echoed state and questions" not in logged


# ---------------------------------------------------------------------------
# API key resolution
# ---------------------------------------------------------------------------


def test_api_key_read_from_environment(client_class: Any, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-from-env")
    client = client_class()

    assert client._headers()["Authorization"] == "Bearer sk-from-env"


def test_explicit_api_key_wins_over_environment(client_class: Any, monkeypatch):
    monkeypatch.setenv(API_KEY_ENV_VAR, "sk-from-env")
    client = client_class(api_key="sk-explicit")

    assert client._headers()["Authorization"] == "Bearer sk-explicit"


@pytest.mark.parametrize("env_value", [None, ""])
def test_missing_api_key_is_a_clear_error(
    client_class: Any, monkeypatch, env_value: str | None
):
    monkeypatch.delenv(API_KEY_ENV_VAR, raising=False)
    if env_value is not None:
        monkeypatch.setenv(API_KEY_ENV_VAR, env_value)

    with pytest.raises(ValueError, match=API_KEY_ENV_VAR):
        client_class()


# ---------------------------------------------------------------------------
# evaluate() / ask(): the whole schema -> Jev -> schema loop
# ---------------------------------------------------------------------------

SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "sentiment": {"type": "string", "enum": ["positive", "neutral", "negative"]},
        "is_spam": {"type": "boolean", "description": "The message is spam."},
        "quality": {
            "type": "integer",
            "minimum": 1,
            "maximum": 5,
            "description": "Overall writing quality.",
        },
    },
}


def schema_answers() -> dict[str, Any]:
    return {
        "model": "jev-1.13.0",
        "answers": {
            "sentiment": {
                "type": "choice",
                "choice": "positive",
                "confidence": 0.97,
                "probabilities": {"positive": 0.97, "neutral": 0.02, "negative": 0.01},
            },
            "is_spam": {"type": "noul", "noul": 0.03},
            "quality": {
                "type": "score",
                "score": 4.6,
                "confidence": 0.81,
                "legend": {str(i): str(i + 1) for i in range(5)},
                "probabilities": {"0": 0.0, "1": 0.0, "2": 0.02, "3": 0.1, "4": 0.88},
            },
        },
        "usage": {"input_tokens": 120, "output_tokens": 12},
    }


async def test_evaluate_round_trips_a_schema(client: Any):
    with respx.mock:
        route = respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=schema_answers())
        )

        result = await call_evaluate(client, SCHEMA, "Loved it. Shipped in a day.")

    sent = json.loads(route.calls.last.request.content)
    assert sent["state"] == "Loved it. Shipped in a day."
    assert sent["model"] == "jev-latest"
    assert list(sent["questions"]) == ["sentiment", "is_spam", "quality"]

    assert result.output == {
        "sentiment": "positive",
        "is_spam": False,
        "quality": 5,
    }
    # Score probabilities are keyed by the schema's values, not Jev's 0-based levels.
    assert result.probabilities["quality"]["5"] == 0.88
    assert result.confidence["sentiment"] == 0.97
    assert result.confidence["is_spam"] is None
    assert result.usage.input_tokens == 120
    assert result.response.model == "jev-1.13.0"


async def test_evaluate_output_validates_against_the_source_schema(client: Any):
    jsonschema = pytest.importorskip("jsonschema")

    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=schema_answers())
        )

        result = await call_evaluate(client, SCHEMA, "some content")

    jsonschema.validate(result.output, SCHEMA)


async def test_ask_reuses_a_converted_question_set(client: Any):
    question_set = client.convert(SCHEMA)

    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=schema_answers())
        )

        first = await call_ask(client, question_set, "one")
        second = await call_ask(client, question_set, "two", model="jev-1.13.0")

    assert first.output == second.output
    assert first.question_set is question_set


async def test_evaluate_rejects_an_unmappable_schema(client: Any):
    from jev_jsonschema import IncompatibleSchemaError

    schema = {
        "type": "object",
        "properties": {
            "summary": {"type": "string"},
            "tags": {"type": "array", "items": {"type": "string"}},
        },
    }

    with respx.mock:
        route = respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=schema_answers())
        )

        with pytest.raises(IncompatibleSchemaError) as exc_info:
            await call_evaluate(client, schema, "some content")

    # Every bad property is reported, and nothing is sent to the API.
    assert [f.key for f in exc_info.value.failures] == ["summary", "tags"]
    assert not route.called


async def test_model_override_is_sent(client: Any):
    with respx.mock:
        route = respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=schema_answers())
        )

        await call_evaluate(client, SCHEMA, "some content", model="jev-1.13.0")

    assert json.loads(route.calls.last.request.content)["model"] == "jev-1.13.0"


# ---------------------------------------------------------------------------
# Connection lifecycle
# ---------------------------------------------------------------------------


def test_sync_client_is_a_context_manager():
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        with JevClient(api_key="k") as client:
            client.system_one(request_for())
            assert client._client is not None

    assert client._client is None


async def test_async_client_is_a_context_manager():
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        async with AsyncJevClient(api_key="k") as client:
            await client.system_one(request_for())
            assert client._client is not None

    assert client._client is None


async def test_connection_is_reused_across_calls(client: Any):
    with respx.mock:
        respx.post(SYSTEM_ONE_URL).mock(
            return_value=httpx.Response(200, json=success_payload())
        )

        await call_system_one(client, request_for())
        first = client._client
        await call_system_one(client, request_for())

    assert client._client is first
