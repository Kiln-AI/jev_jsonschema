<div align="center">

### jev_jsonschema

**Use a JSON Schema with [Jev](https://docs.typesafe.ai/introduction). Get JSON back.**

[![CI](https://github.com/Kiln-AI/jev_jsonschema/actions/workflows/ci.yml/badge.svg)](https://github.com/Kiln-AI/jev_jsonschema/actions/workflows/ci.yml)
[![Python](https://img.shields.io/badge/python-3.10%2B-blue)](https://pypi.org/project/jev_jsonschema/)
[![License](https://img.shields.io/badge/license-MIT-green)](LICENSE)

<a href="#quick-start"><strong>Quick Start</strong></a> •
<a href="#what-maps-to-what"><strong>What Maps to What</strong></a> •
<a href="#what-you-get-back"><strong>What You Get Back</strong></a> •
<a href="#without-the-client"><strong>Without the Client</strong></a> •
<a href="#what-isnt-supported"><strong>Limits</strong></a>

</div>

---

Jev is TypeSafe's System One model. It can't hallucinate a value, because it never writes one: you send it typed questions, and it answers them with probabilities. That's great, but it means Jev doesn't speak JSON Schema — and almost every LLM app already has its structured output defined as a JSON Schema.

This library sits in between. Give it your schema and your content, and you get back JSON that validates against the schema you started with.

```
JSON Schema  ──▶  Jev questions  ──▶  [ Jev ]  ──▶  answers  ──▶  JSON Schema output
```

## Quick Start

```bash
pip install jev_jsonschema   # or: uv add jev_jsonschema
```

```python
from jev_jsonschema import JevClient

schema = {
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

with JevClient() as jev:  # reads TYPESAFE_API_KEY, or pass api_key="..."
    result = jev.evaluate(schema, state="Loved it. Shipped in a day.")

result.output
# {"sentiment": "positive", "is_spam": False, "quality": 5}
```

`result.output` validates against `schema`. Hand it to the same code that used to parse your model's JSON.

There's an `AsyncJevClient` with identical methods:

```python
from jev_jsonschema import AsyncJevClient

async with AsyncJevClient() as jev:
    result = await jev.evaluate(schema, state="Loved it. Shipped in a day.")
```

Converting a schema is pure work, so if you're calling the same schema in a loop, convert once and reuse it:

```python
question_set = jev.convert(schema)

for review in reviews:
    result = jev.ask(question_set, state=review)
```

## What Maps to What

Jev has three question types. Here's the JSON Schema that reaches each one, and what comes back:

| Jev question | Write this in your schema | Comes back as |
|---|---|---|
| **noul** — a yes/no probability | `{"type": "boolean", "description": "..."}` | `true` / `false`, thresholded at `0.5` |
| **noul** | `{"type": "number", "minimum": 0, "maximum": 1, "description": "..."}` | the raw probability, `0.0`–`1.0` |
| **choice** — one of up to 255 options | `{"type": "string", "enum": [...]}` | the enum string Jev picked |
| **choice** | `{"type": "integer", "enum": [...]}` | the enum integer Jev picked |
| **score** — 2 to 10 ordered levels | `{"type": "integer", "minimum": 1, "maximum": 5}` | an integer in that range |

One question per schema property, in the order the schema declares them.

Jev needs to know what it's judging, so each question gets instructions from the property's `description`, falling back to its `title`, then to the property name. `boolean` and `number` require a real description — a property named `is_spam` with no description is too thin to ask about, so the library tells you instead of guessing.

## What You Get Back

Jev answers with distributions, not just values, and none of that is thrown away:

```python
result.output  # {"sentiment": "positive", "is_spam": False, "quality": 5}
result.confidence  # {"sentiment": 0.97, "is_spam": None, "quality": 0.81}
result.probabilities  # {"sentiment": {"positive": 0.97, "neutral": 0.02, "negative": 0.01},
#  "is_spam": {"true": 0.03, "false": 0.97},
#  "quality": {"1": 0.0, "2": 0.0, "3": 0.02, "4": 0.1, "5": 0.88}}
result.usage  # SystemOneUsage(input_tokens=120, output_tokens=12)
result.response  # the raw SystemOneResponse, if you want it
```

`probabilities` is keyed by your schema's values, not Jev's internal labels, so a score of `1`–`5` reads as `"1"`–`"5"` and not `"0"`–`"4"`. Noul questions carry no confidence of their own, so `confidence` is `None` for booleans and numbers.

## Errors

```python
from jev_jsonschema import IncompatibleSchemaError, JevApiError

try:
    result = jev.evaluate(schema, state=review)
except IncompatibleSchemaError as e:
    ...  # your schema has properties Jev can't answer — see below
except JevApiError as e:
    ...  # e.status_code, e.retryable, e.request_id
```

`JevApiError` messages are written to be shown to your users as-is, and `retryable` tells you whether trying again could help (timeouts, 429s, 5xxs). The client does one POST and never retries on its own, so the backoff policy stays yours.

## What Isn't Supported

Jev answers questions from a fixed set of options. Plenty of JSON Schema doesn't fit, and this library refuses it loudly rather than inventing a mapping:

- Free-form `string` (anything without an `enum`), `array`, `object`, `null`
- `anyOf`, `oneOf`, `allOf`, `$ref`, `const`, `not`, and multi-type `"type": [...]`
- `number` with any bounds other than `minimum: 0` / `maximum: 1`
- `integer` ranges wider than 10 values, and enums with more than 255 values
- `integer` without both `minimum` and `maximum`

You find out before anything is sent, and you find out about **every** bad property, not just the first:

```python
try:
    jev.evaluate(schema, state=review)
except IncompatibleSchemaError as e:
    for failure in e.failures:
        print(failure.key, failure.reason)
# summary uses 'anyOf', which is not supported
# tags type 'array' is not supported
```

Error messages here are also written to be shown to your users as-is.

## Without the Client

The conversion is a separate, pure layer. If you'd rather make the HTTP call yourself — your own retries, your own auth, TypeSafe's official SDK — use the two converters directly and skip `JevClient` entirely:

```python
from jev_jsonschema import JSONSchema2Jev, JevResult2JsonSchema

question_set = JSONSchema2Jev().convert(schema)

body = question_set.request(
    state="Loved it. Shipped in a day.", model="jev-latest"
).to_body()
answers = your_http_post("https://api.typesafe.ai/v1/systemone", json=body)["answers"]

result = JevResult2JsonSchema().convert(question_set, answers)
result.output
```

The `QuestionSet` is the thing to hold onto between the two halves: it remembers how each property was mapped, which is why decoding needs it. It's a plain frozen dataclass.

## Options

```python
from jev_jsonschema import JevClient, MappingOptions, ScoreDecode

options = MappingOptions(
    noul_threshold=0.5,  # where a noul probability becomes True
    max_score_levels=10,  # lower the cap on integer ranges
    score_decode=ScoreDecode.argmax,  # or ScoreDecode.expected, for the rounded mean
    instructions_fallback_to_key=True,  # use the property name when there's no description
)

jev = JevClient(options=options)
```

Using the converters directly? Pass the same options to both halves — the decoder needs to know how the questions were built.

## Design Notes

- **Two layers, and you can take just one.** `JSONSchema2Jev` and `JevResult2JsonSchema` are pure and know nothing about HTTP. `JevClient` is a thin wrapper that adds the POST.
- **Small dependency footprint**: `httpx` and `pydantic`, both of which most apps already have.
- **No hidden retries, no hidden concurrency.** One call is one POST.
- **Never logs your data.** Failures log the status and TypeSafe's request id, never the body — which would echo your state and questions.
- **Fully typed**, ships a `py.typed` marker.

## Development

Uses [uv](https://docs.astral.sh/uv/), [ruff](https://docs.astral.sh/ruff/), and [ty](https://docs.astral.sh/ty/).

```bash
uv sync           # install everything
uv run pytest     # tests
uv run ruff check --fix && uv run ruff format   # lint + format
uv run ty check   # typecheck
uv build          # build the wheel and sdist
```

No test hits the network — the client's tests run against [respx](https://lundberg.github.io/respx/). CI runs all of the above on Python 3.10 through 3.14.

## License

MIT — see [LICENSE](LICENSE).
