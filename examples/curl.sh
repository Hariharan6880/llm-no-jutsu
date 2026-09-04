#!/usr/bin/env bash
# Call the devllm server with curl. Start it first: python server.py
set -euo pipefail

echo "--- is the server healthy? ---"
curl -s localhost:8765/health

echo
echo "--- plain text ---"
curl -s --max-time 300 -X POST localhost:8765/generate \
  -H 'content-type: application/json' \
  -d '{"prompt": "Recommend one phone under 50000 INR. One sentence."}'

echo
echo "--- structured output ---"
curl -s --max-time 300 -X POST localhost:8765/generate \
  -H 'content-type: application/json' \
  -d '{
        "prompt": "Pick 2 phones under 50000 INR.",
        "schema": {
          "type": "object",
          "properties": {
            "picks": {
              "type": "array",
              "items": {
                "type": "object",
                "properties": {
                  "name": {"type": "string"},
                  "price_inr": {"type": "integer"}
                },
                "required": ["name", "price_inr"],
                "additionalProperties": false
              }
            }
          },
          "required": ["picks"],
          "additionalProperties": false
        }
      }'
