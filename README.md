<div align="center">

### jev_jsonschema

**Use a JSON Schema with [Jev](https://docs.typesafe.ai/introduction). Get JSON back.**

<a href="#quick-start"><strong>Quick Start</strong></a> •
<a href="#what-maps-to-what"><strong>What Maps to What</strong></a> •
<a href="#what-you-get-back"><strong>What You Get Back</strong></a> •
<a href="#what-isnt-supported"><strong>Limits</strong></a> •
<a href="#development"><strong>Development</strong></a>

</div>

---

Jev is TypeSafe's System One model. It can't hallucinate a value, because it never writes one: you send it typed questions, and it answers them with probabilities. That's great, but it means Jev doesn't speak JSON Schema — and almost every LLM app already has its structured output defined as a JSON Schema.

This library sits in between. Give it your schema, and it hands you the Jev questions to send. Give it Jev's answers, and it hands you back JSON that validates against the schema you started with.

```
JSON Schema  ──▶  Jev questions  ──▶  [ Jev ]  ──▶  answers  ──▶  JSON Schema output
```

## Quick Start

```bash
pip install jev_jsonschema   # or: uv add jev_jsonschema
```

```python
import httpx

from jev_jsonschema import JSONSchema2Jev, JevResult2JsonSchema, SystemOneResponse

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

# 1. Schema in, Jev questions out.
question_set = JSONSchema2Jev().convert(schema)

# 2. Call Jev however you like. The request body is built for you.
body = question_set.request(
    state="Loved it. Shipped in a day.", model="jev-latest"
).to_body()
raw = httpx.post(
    "https://api.typesafe.ai/v1/systemone",
    json=body,
    headers={"Authorization": f"Bearer {TYPESAFE_API_KEY}"},
).json()

# 3. Answers in, schema-shaped JSON out.
response = SystemOneResponse.model_validate(raw)
result = JevResult2JsonSchema().convert(question_set, response.answers)

result.output
# {"sentiment": "positive", "is_spam": False, "quality": 5}
```

`result.output` validates against `schema`. Hand it to the same code that used to parse your model's JSON.

## What Maps to What

One Jev question per schema property, in the order the schema declares them.

| JSON Schema property | Jev question | Comes back as |
|---|---|---|
| `{"type": "string", "enum": [...]}` | choice | the enum string Jev picked |
| `{"type": "integer", "enum": [...]}` | choice | the enum integer Jev picked |
| `{"type": "boolean"}` | noul | `true`/`false`, thresholded at 0.5 |
| `{"type": "number", "minimum": 0, "maximum": 1}` | noul | the raw probability, `0.0`–`1.0` |
| `{"type": "integer", "minimum": 1, "maximum": 5}` | score | an integer in that range |

Jev needs to know what it's judging, so each question gets instructions from the property's `description`, falling back to its `title`, then to the property name. `boolean` and `number` require a real description — a property named `is_spam` with no description is too thin to ask about, so the library tells you instead of guessing.

## What You Get Back

Jev answers with distributions, not just values, and none of that is thrown away:

```python
result.output  # {"sentiment": "positive", "is_spam": False, "quality": 5}
result.confidence  # {"sentiment": 0.97, "is_spam": None, "quality": 0.81}
result.probabilities  # {"sentiment": {"positive": 0.97, "neutral": 0.02, "negative": 0.01},
#  "is_spam": {"true": 0.03, "false": 0.97},
#  "quality": {"1": 0.0, "2": 0.01, ... "5": 0.88}}
```

`probabilities` is keyed by your schema's values, not Jev's internal labels, so a score of `1`–`5` reads as `"1"`–`"5"` and not `"0"`–`"4"`. Noul questions carry no confidence of their own, so `confidence` is `None` for booleans and numbers.

## What Isn't Supported

Jev answers questions from a fixed set of options. Plenty of JSON Schema doesn't fit, and this library refuses it loudly rather than inventing a mapping:

- Free-form `string` (anything without an `enum`), `array`, `object`, `null`
- `anyOf`, `oneOf`, `allOf`, `$ref`, `const`, `not`, and multi-type `"type": [...]`
- `number` with any bounds other than `minimum: 0` / `maximum: 1`
- `integer` ranges wider than 10 values, and enums with more than 255 values
- `integer` without both `minimum` and `maximum`

`convert()` reports **every** property it can't map, not just the first one:

```python
try:
    JSONSchema2Jev().convert(schema)
except IncompatibleSchemaError as e:
    for failure in e.failures:
        print(failure.key, failure.reason)
# summary uses 'anyOf', which is not supported
# tags type 'array' is not supported
```

Error messages are written to be shown to your users as-is.

## Options

```python
from jev_jsonschema import (
    JSONSchema2Jev,
    JevResult2JsonSchema,
    MappingOptions,
    ScoreDecode,
)

options = MappingOptions(
    noul_threshold=0.5,  # where a noul probability becomes True
    max_score_levels=10,  # lower the cap on integer ranges
    score_decode=ScoreDecode.argmax,  # or ScoreDecode.expected, for the rounded mean
    instructions_fallback_to_key=True,  # use the property name when there's no description
)

question_set = JSONSchema2Jev(options).convert(schema)
result = JevResult2JsonSchema(options).convert(question_set, response.answers)
```

Pass the same options to both halves — the decoder needs to know how the questions were built.

## Design Notes

- **No HTTP client.** The library builds request bodies and parses response bodies. Use `httpx`, `requests`, TypeSafe's own SDK, or whatever your app already has.
- **Pydantic is the only dependency.** `SystemOneRequest`, `SystemOneResponse`, and the question and answer models are all here, so you don't need a second package just to type the wire format.
- **The `QuestionSet` is the state.** It remembers how each property was mapped, which is why decoding needs it. Keep it between the request and the response; it's a plain frozen dataclass.
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

CI runs all of the above on Python 3.10 through 3.14.

## License

MIT — see [LICENSE](LICENSE).
