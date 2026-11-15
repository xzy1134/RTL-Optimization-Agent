# Model prompts: initial scaffold

`client.py` defines the request/response contract. `prompts/system.md` contains
the fixed optimization rules and JSON output contract. `prompts/user.md` receives
the current request as JSON. `prompt_builder.build_messages(request)` returns
system/user message dictionaries for a future provider client.

Each graph round now saves `messages.json` beside `request.json`. The former is
the rendered prompt; the latter is the structured input. Mock still receives a
ModelRequest and returns predefined candidates. It does not interpret these
messages, and no provider is called. The old Demo's root-level `prompts/` was
removed during cleanup; current templates are only under `rtl_agent/llm/prompts/`.

This scaffold does not implement API transport, response parsing or real usage
accounting. Prompt instructions are not a proof or a security boundary. Existing
formal, synthesis and acceptance gates remain authoritative.

## Reminder before M4 / DeepSeek integration

Remind the user to review and refine these initial prompts when connecting the
real model. This is pending work, not a validated optimization prompt:

- Inspect actual messages with the user and assess real model responses.
- Implement strict JSON parsing and ModelResponse validation outside the prompt.
- Tune strategy context, feedback summaries and repeated RTL for token cost.
- Include fixed auxiliary source context when supporting multi-file model edits.
- Compare real candidate equivalence, area and depth before claiming effectiveness.

Do not skip this review just because the Mock workflow passes.
