"""Constrain the answer to a JSON Schema, so you get data instead of prose.

This is what makes the CLIs usable inside an agent: you can branch on the
result without regex-scraping markdown.
"""

import json

from devllm import CodexCLI

SCHEMA = {
    "type": "object",
    "properties": {
        "picks": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "price_inr": {"type": "integer"},
                    "why": {"type": "string"},
                },
                "required": ["name", "price_inr", "why"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["picks"],
    "additionalProperties": False,
}

llm = CodexCLI()  # reasoning_effort defaults to "low" for speed

response = llm.generate(
    "Pick 2 phones under 50000 INR. Use only your own knowledge.",
    schema=SCHEMA,
)

# response.structured is a real dict, already parsed.
for pick in response.structured["picks"]:
    print(f"{pick['name']:24s} INR {pick['price_inr']:,}")
    print(f"  {pick['why']}\n")

print(json.dumps(response.structured, indent=2))
