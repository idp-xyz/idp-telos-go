"""Phase 0 of docs/adr/ADR-0003 (web search and fetch): an evaluation harness that compares search providers under one
fixed agent, and measures what the design depends on (answer accuracy, citations, cost, latency, injection).

It implements the tools the way idp-web will (ADR-0003 D2–D4, D10): web_search, and web_fetch that reads only an
address a search returned (a signed ref), with an address check, size and time limits, and pages of at most 12 KiB.
The model is any OpenAI-compatible endpoint. See tools/web_eval/README.md.
"""
