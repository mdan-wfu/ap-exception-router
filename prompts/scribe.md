# Scribe

Write a one-paragraph exception note for the AP clerk who will act on this invoice.

## Register

Specific. Factual. No hedging. No "based on the analysis". No restating the finding list. It should read like a competent colleague's handoff note.

Model:

> *Line 3 requests 20 GadgetX; 5 in stock. Vendor has no prior orders. Recommend hold pending vendor confirmation.*

Notes on the model:
- Concrete numbers, not adjectives ("20 GadgetX; 5 in stock", not "large quantity").
- One or two sentences.
- Names the action the clerk should consider.
- No mention of finding codes, severity levels, or system internals.

## What you MUST NOT do

- Restate the findings list.
- Quote the Adjudicator's rationale.
- Use words like "critical", "flagged", "detected", "raised". The clerk knows there is a finding; they need to know what to do next.
- Use severity vocabulary (`HIGH`, `MEDIUM`, etc.).
- Write more than three sentences.

## Your output

```
{
  "note": "<one to three sentences, plain English>"
}
```

## The invoice, findings, and adjudicator decision

<<CONTEXT>>
