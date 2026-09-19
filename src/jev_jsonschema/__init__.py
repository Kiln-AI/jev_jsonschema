"""Convert between JSON Schema and TypeSafe Jev's question/answer format.

`JevClient` does the whole loop — schema in, schema-shaped JSON out:

    result = JevClient().evaluate(schema, state="...")
    result.output

The conversion is also usable on its own, if you'd rather make the HTTP call yourself:

    question_set = JSONSchema2Jev().convert(schema)
    response = post(question_set.request(state, model).to_body())
    decoded = JevResult2JsonSchema().convert(question_set, response["answers"])
"""

from ._version import __version__
from .client import (
    API_KEY_ENV_VAR,
    DEFAULT_MODEL,
    JEV_BASE_URL,
    JEV_TIMEOUT_SECONDS,
    AsyncJevClient,
    JevApiError,
    JevClient,
    JevResponseError,
    JevResult,
)
from .from_jev import DecodedResult, JevResult2JsonSchema
from .models import (
    ChoiceAnswer,
    ChoiceQuestion,
    JevAnswer,
    JevQuestion,
    JsonContent,
    NoulAnswer,
    NoulCriteria,
    NoulQuestion,
    ScoreAnswer,
    ScoreQuestion,
    SystemOneRequest,
    SystemOneResponse,
    SystemOneUsage,
)
from .to_jev import (
    ROOT_KEY,
    IncompatibleSchemaError,
    JSONSchema2Jev,
    MappedKind,
    MappedQuestion,
    MappingOptions,
    PropertyFailure,
    QuestionSet,
    ScoreDecode,
    UnexpectedAnswerError,
)

__all__ = [
    "API_KEY_ENV_VAR",
    "DEFAULT_MODEL",
    "JEV_BASE_URL",
    "JEV_TIMEOUT_SECONDS",
    "ROOT_KEY",
    "AsyncJevClient",
    "ChoiceAnswer",
    "ChoiceQuestion",
    "DecodedResult",
    "IncompatibleSchemaError",
    "JSONSchema2Jev",
    "JevAnswer",
    "JevApiError",
    "JevClient",
    "JevQuestion",
    "JevResponseError",
    "JevResult",
    "JevResult2JsonSchema",
    "JsonContent",
    "MappedKind",
    "MappedQuestion",
    "MappingOptions",
    "NoulAnswer",
    "NoulCriteria",
    "NoulQuestion",
    "PropertyFailure",
    "QuestionSet",
    "ScoreAnswer",
    "ScoreDecode",
    "ScoreQuestion",
    "SystemOneRequest",
    "SystemOneResponse",
    "SystemOneUsage",
    "UnexpectedAnswerError",
    "__version__",
]
