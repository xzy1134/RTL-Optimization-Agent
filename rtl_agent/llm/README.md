
# Model clients and structured prompts

M4 adds `deepseek_client.py`, implementing the existing ModelClient interface.
Mock remains available and is the default CLI backend. The provider client uses
Python's standard library; no new package is required.

`client.py` defines the request/response contract. `prompts/system.md` contains
the fixed optimization rules and JSON output contract. `prompts/user.md` receives
the compact current request as JSON. `prompt_builder.build_messages(request)` returns
system/user message dictionaries for a future provider client.

Each graph round now saves `messages.json` beside `request.json`. The former is
the rendered prompt; the latter is the structured input. Mock still receives a
ModelRequest and returns predefined candidates. It does not interpret these
messages. With the DeepSeek backend the client renders the same messages and
sends them to the configured provider. The old Demo's root-level `prompts/` was
removed during cleanup; current templates are only under `rtl_agent/llm/prompts/`.

Prompt instructions are not a proof or a security boundary. Existing formal,
synthesis and acceptance gates remain authoritative.

## Compact model context

`prompt_builder.compact_context(request)` builds a new dictionary; it never
changes the ModelRequest received by Mock or the full `request.json` audit file.
The provider and `messages.json` use the same compact rendering:

- Strategies carry observations, proposed transformations, risks, confidence,
  unconfirmed conditions and capability/permission flags. Full library titles,
  goals, citations and versions remain in the local request. Strategy-specific
  prohibitions are retained. Duplicate structural reasons are removed; shared
  observations are stored once and referenced by zero-based array indices.
- Exactly identical original/parent/best source text is sent once. `rtl_ref` and
  `parent_rtl_ref` point to that source; a distinct best design survives baseline
  restarts. Identical measured values use explicit field references, and an
  identical baseline/best summary uses `design_ref`. All references resolve
  within the current request; no provider conversation memory is assumed.
- Successful and unsuccessful trials retain measured area/depth, formal status,
  acceptance/rejection reasons and actual edits. Empty diagnostic text and a
  repeated common depth limit are omitted. Fixed rules ask for a materially
  different structure without inventing a critical-path explanation.

No proof, synthesis, scoring, search budget or token reservation policy changes
are involved. JSON whitespace is also reduced. Byte reductions are not measured
token savings or evidence of better model decisions.

Offline replay of saved requests in
`work/rtl_agent/bench-memory-1a4v5xlu/` produced these message sizes. Both sides
use `len(json.dumps(messages, ensure_ascii=False).encode())`; the old side is
the archived `messages.json`, the new side rebuilds the same `request.json`:

| Case / round | Before (UTF-8 bytes) | After | Reduction |
| --- | ---: | ---: | ---: |
| addsubz / 1 | 17,219 | 10,344 | 39.93% |
| addsubz / 3 | 19,712 | 12,391 | 37.14% |
| barrel / 1 | 16,889 | 11,074 | 34.43% |
| barrel / 3 | 20,587 | 14,549 | 29.33% |

The 92-test regression passed, including the three-benchmark Mock search with
real M1 tools. No paid API call was made for this compression check. A later
bounded live comparison should measure actual tokens, accepted improvements,
duplicate rate and runtime before drawing conclusions about effectiveness.

## Configure and run DeepSeek locally

Like the teacher Demo, the client automatically reads the repository-root
`.env`, regardless of the current working directory. Use the root `.env.example`
as a template when no `.env` exists; edit an existing file rather than overwriting
it. Set `DEEPSEEK_API_KEY`, optionally `DEEPSEEK_MODEL` and `DEEPSEEK_API_URL`.
The file is Git-ignored. Do not put actual keys in the example, CLI arguments,
source, requests, reports or chat. Then run `./run_agent.sh`; no terminal export
is needed each session.

Explicit model/URL arguments override `.env`, which overrides environment
variables, which override defaults. Key lookup is `.env` DEEPSEEK_API_KEY,
then `.env` LLM_API_KEY (teacher compatibility), then environment DEEPSEEK_API_KEY.
Empty or common placeholder keys are skipped. `LLM_MODEL` is also supported in
the file, but `LLM_BACKEND` and `LLM_BASE_URL` are ignored: this client uses the
chat-completions protocol, not the Demo's Claude CLI/Anthropic endpoint.
Parsing supports plain KEY=VALUE and matching quotes; shell commands and variable
expansion are never executed. Configuration is not copied into os.environ.
Mock does not load this file or make requests.

Environment-only setup remains supported; this optional Bash input keeps the
key out of command history and screen output:

```bash
read -rsp 'DeepSeek API Key: ' DEEPSEEK_API_KEY
export DEEPSEEK_API_KEY
printf '\n'
```

From the repository root, run the bounded experiment in that same terminal:

```bash
.venv-m3/bin/python -B -m rtl_agent.agent \
  rtl_agent/examples/mock_baseline.v --top t \
  --backend deepseek --model deepseek-flash \
  --depth-limit 6 --max-candidates 5 --max-rounds 5 --patience 5 \
  --max-api-requests 5 --max-tokens 50000 --max-output-tokens 2048 \
  --runtime 300 --api-timeout 45 --max-retries 1
```

Default endpoint: `https://api.deepseek.com/chat/completions`. `--api-url` can
set another HTTPS endpoint. Redirects are rejected so credentials are not
forwarded. Model name is configurable via `--model`, `.env` or `DEEPSEEK_MODEL`.
The request uses non-streaming JSON output, explicit non-thinking mode, and a
completion token cap. Format guidance follows the
[official JSON guide](https://api-docs.deepseek.com/guides/json_mode/).

## Parsing and error policy

Only a JSON object with exactly action, strategy_id, description and rtl is
accepted. CANDIDATE needs a nonempty full source and a short reporting label.
Labels may name a known strategy, combine names with `+`, or name a new direction;
they are not a transformation allowlist. NO_CHANGE requires null RTL and an empty
strategy ID. Duplicate fields, Markdown wrappers, missing content and truncated responses
are rejected. Tool prechecks and formal proof still determine whether source
is valid and equivalent; JSON validation never establishes correctness.

HTTP 429/500/502/503/504 can retry with bounded backoff and Retry-After. Default
is one retry; CLI permits at most two retries. Every HTTP attempt, including
retries and failed requests, counts against the search request budget. Requests
are paced at least one second apart. Authentication/balance errors stop search.
Timeouts and ambiguous network failures are not automatically resubmitted, to
avoid paying twice for work that may still run on the server. Format errors do
not trigger client retries; a later bounded graph round may use their feedback.
One NO_CHANGE also permits another graph round; it consumes patience and round
budgets. It is not a proof of optimality or an unconditional stop request.
Missing or invalid successful-response usage stops further search.

No HTTP error body or request headers are archived. Valid response text is
redacted for the active key before persistence. Diagnostics contain fixed safe
codes rather than raw transport exception text.

## Accounting and artifacts

`api_request.json` records the actual payload without headers. `api_attempts.json`
records HTTP status, elapsed time, sanitized response, and usage for each attempt.
These are saved on both success and client failure. `messages.json`, candidate
RTL, actual diff and all M1 logs continue to be saved. Errors also produce
`model_error.json`. Runtime artifact directories remain ignored by Git.

Usage sums actual provider prompt_tokens, completion_tokens and total_tokens,
including usage on invalid model output. API time includes request retries and
waiting; individual attempt durations are stored separately. Graph model time,
EDA evaluation time and overall runtime are also reported. `calls` counts model
rounds; `real_api_calls` counts HTTP attempts. Offline tests stub HTTP and do not
represent live calls, even though they exercise these counter names.

If a request has no usage, its cost is unknown, not measured zero. Known token
sums exclude it, `unknown_usage_calls` increases, and `token_usage_complete` is
false. Budget accounting uses `charged_tokens`: known totals plus conservative
reservations for unknown attempts. A preflight reservation is request JSON UTF-8
bytes + 512 framing allowance + requested maximum output tokens. This is a
conservative admission heuristic, not exact tokenization or a provider billing
guarantee. It can stop early. The server response usage is the actual measurement.

Remaining request/token allowances are sent through ModelRequest constraints;
the client checks before each attempt. Remaining graph runtime bounds call and
retry deadlines. Standard-library socket timeouts are per blocking operation;
DNS and unusually slow body delivery can exceed a wall-clock deadline, which is
checked again before accepting output. No candidate is accepted after the graph
deadline. Additional host watchdog isolation is not implemented here.

## Prompt review and follow-up

The initial prompt was reviewed for M4: it already includes original/parent RTL,
strategy evidence, metrics and bounded feedback. JSON examples support provider
JSON mode. A real small-circuit run passed (see M4_ACCEPTANCE.md). Requests now
also carry successful and unsuccessful experiment notes and best-design context;
their effect on real benchmark search still needs measurement. Fixed auxiliary-source context is still a
follow-up for multi-file model reasoning, although EDA retains fixed extra files.

Remind the user to review the saved real messages and outcomes before changing
prompt wording or selection heuristics:

- Inspect actual messages with the user and assess real model responses.
- Review strict JSON parsing failures separately from RTL proof failures.
- Tune strategy context, feedback summaries and repeated RTL for token cost.
- Include fixed auxiliary source context when supporting multi-file model edits.
- Compare real candidate equivalence, area and depth before claiming effectiveness.

Do not skip this review just because the Mock workflow passes.

Cross-round notes are documented in [memory acceptance](../MEMORY_ACCEPTANCE.md).
The later vector-store experiment reminder is saved in [follow-up](../FOLLOW_UP.md).


## Default historical feedback

The default feedback_mode is historical. It restores the message builder used
by deepseek-search-ouw3ksjt: ranked strategy hints and structural evidence,
bounded successful/failed trial summaries with actual edits, and the best
design context. The fixed system rules still allow combined or novel strategies.
Identical sources and measurements use the original compact references.

M5 diagnosis and location switching remain opt-in through --diagnostics or
run_search(..., diagnostics=True). The basic A renderer remains available through
ModelRequest(feedback_mode='basic') for offline comparisons. The historical
five-round archive is checked message-for-message in regression tests.
