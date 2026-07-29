"""
Capability probe — three independent checks against the xAI API.

Check 1: Basic completion
Check 2: Structured output via response_format
Check 3: Tool calling round-trip
"""
import json
import os
import sys

from dotenv import load_dotenv
from openai import OpenAI
from pydantic import BaseModel

load_dotenv()

API_KEY = os.environ["XAI_API_KEY"]
MODEL = os.environ["GROK_MODEL"]

client = OpenAI(api_key=API_KEY, base_url="https://api.x.ai/v1")

PASS = "\033[32mPASS\033[0m"
FAIL = "\033[31mFAIL\033[0m"


# ---------------------------------------------------------------------------
# Check 1: Basic completion
# ---------------------------------------------------------------------------
def check_basic_completion() -> bool:
    print("\n=== Check 1: Basic completion ===")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": "Reply with the single word: hello"}],
            max_completion_tokens=20,
        )
        text = resp.choices[0].message.content or ""
        print(f"Response: {text!r}")
        if text.strip():
            print(PASS)
            return True
        print(f"{FAIL}: empty response")
        return False
    except Exception as exc:
        print(f"{FAIL}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Check 2: Structured output
# Note: no minLength/maxLength/minItems/maxItems/pattern per CLAUDE.md §3a
# ---------------------------------------------------------------------------
class ProbeLineItem(BaseModel):
    item: str
    quantity: int
    unit_price: float


class ProbeInvoice(BaseModel):
    invoice_number: str
    vendor: str
    total: float
    line_items: list[ProbeLineItem]


SAMPLE_TEXT = """
Invoice Number: INV-TEST-001
Vendor: Acme Supplies
Date: 2026-01-15
Items:
  WidgetA   qty: 3   unit price: $250.00
  GadgetX   qty: 1   unit price: $750.00
Total: $1,500.00
"""


def check_structured_output() -> bool:
    print("\n=== Check 2: Structured output ===")
    schema = ProbeInvoice.model_json_schema()
    # Remove any unsupported constraint keywords from the schema before sending
    schema_str = json.dumps(schema)
    print(f"Schema sent: {schema_str}")
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=[
                {
                    "role": "user",
                    "content": f"Extract invoice fields from this document as JSON.\n\n{SAMPLE_TEXT}",
                }
            ],
            response_format={"type": "json_schema", "json_schema": {"name": "ProbeInvoice", "schema": schema}},
            max_completion_tokens=500,
        )
        raw = resp.choices[0].message.content or ""
        print(f"Raw response: {raw}")
        parsed = ProbeInvoice.model_validate_json(raw)
        print(f"Parsed: invoice_number={parsed.invoice_number!r}, vendor={parsed.vendor!r}, total={parsed.total}, items={len(parsed.line_items)}")
        print(PASS)
        return True
    except Exception as exc:
        print(f"{FAIL}: {exc}")
        return False


# ---------------------------------------------------------------------------
# Check 3: Tool calling round-trip
# ---------------------------------------------------------------------------
ITEM_REF_TOOL = {
    "type": "function",
    "function": {
        "name": "get_item_reference",
        "description": "Look up reference data for an item by name.",
        "parameters": {
            "type": "object",
            "properties": {
                "item": {"type": "string", "description": "Item name to look up"},
            },
            "required": ["item"],
        },
    },
}

ITEM_REFERENCE_DB: dict[str, dict] = {
    "WidgetA": {"unit_price": 250.00, "stock": 15, "category": "widget", "active": True},
    "GadgetX": {"unit_price": 750.00, "stock": 5, "category": "gadget", "active": True},
}


def handle_tool_call(name: str, args: dict) -> str:
    if name == "get_item_reference":
        item = args.get("item", "")
        data = ITEM_REFERENCE_DB.get(item)
        if data is None:
            return json.dumps({"error": f"Item {item!r} not found"})
        return json.dumps(data)
    return json.dumps({"error": "unknown tool"})


def check_tool_calling() -> bool:
    print("\n=== Check 3: Tool calling round-trip ===")
    messages = [
        {
            "role": "user",
            "content": (
                "Using the get_item_reference tool, look up the unit price for WidgetA "
                "and tell me what it is."
            ),
        }
    ]
    try:
        resp = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            tools=[ITEM_REF_TOOL],
            tool_choice="auto",
            max_completion_tokens=500,
        )
        msg = resp.choices[0].message
        if not msg.tool_calls:
            print(f"{FAIL}: no tool calls emitted. Response: {msg.content!r}")
            return False

        print(f"Tool calls emitted: {[tc.function.name for tc in msg.tool_calls]}")
        messages.append(msg)

        # Feed results back
        for tc in msg.tool_calls:
            args = json.loads(tc.function.arguments)
            result = handle_tool_call(tc.function.name, args)
            print(f"  Tool {tc.function.name}({args}) → {result}")
            messages.append({
                "role": "tool",
                "tool_call_id": tc.id,
                "content": result,
            })

        final = client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_completion_tokens=200,
        )
        final_text = final.choices[0].message.content or ""
        print(f"Final answer: {final_text!r}")
        if "250" in final_text:
            print(PASS)
            return True
        print(f"{FAIL}: final answer did not mention the expected price (250). Got: {final_text!r}")
        return False
    except Exception as exc:
        print(f"{FAIL}: {exc}")
        return False


if __name__ == "__main__":
    print(f"Model: {MODEL}")
    r1 = check_basic_completion()
    r2 = check_structured_output()
    r3 = check_tool_calling()

    print("\n=== Summary ===")
    print(f"  Basic completion:  {'PASS' if r1 else 'FAIL'}")
    print(f"  Structured output: {'PASS' if r2 else 'FAIL'}")
    print(f"  Tool calling:      {'PASS' if r3 else 'FAIL'}")
    sys.exit(0 if all([r1, r2, r3]) else 1)
