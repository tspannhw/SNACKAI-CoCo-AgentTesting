# Cortex Agent API - Copilot Studio Integration

Workaround patterns for integrating Snowflake Cortex Agents with Microsoft Copilot Studio,
which has a 2-minute maximum execution time and does not support streaming (SSE).

## Problem Statement

- Copilot Studio agent flows have a **2-minute timeout**
- Copilot Studio **does not support streaming** (SSE)
- Cortex Agent API defaults to streaming; non-streaming (`stream: false`) is synchronous with a 15-min timeout
- Background/async mode (`background: true`) returns error `399500` when not enabled on the account

## Approaches

| # | Approach | Works Today? | Requires | Best For |
|---|----------|:---:|----------|----------|
| 1 | Background Mode + Thread Polling | No* | Account enablement | Long-running queries, cleanest architecture |
| 2 | Orchestration Budget Constraint | **Yes** | Nothing | Queries that can complete in < 90s |
| 3 | Async Middleware (Azure Function) | **Yes** | Azure Function deployment | Full control, any query length |
| 4 | SQL API + AGENT_RUN Function | **Yes** | Nothing | Native async without middleware |

*Requires Snowflake to enable background mode on the account.

## Approach 1: Background Mode (`approach1_background_mode.py`)

**Status**: Blocked until Snowflake enables async mode on the account.

Once enabled:
1. Create a thread
2. POST `agent:run` with `"background": true` → get `run_id` immediately
3. Poll the thread (Describe Thread API) until assistant message appears
4. Return JSON result to Copilot Studio

**Action needed**: Contact your Snowflake account team to request enablement.

## Approach 2: Budget Constraint (`approach2_budget_constraint.py`)

**Status**: Works today, no changes needed.

Adds `orchestration.budget.seconds` to cap agent execution time:
```json
{
  "stream": false,
  "orchestration": {"budget": {"seconds": 90}},
  "messages": [...]
}
```

Trade-off: Complex queries may return partial/incomplete answers.

## Approach 3: Async Middleware (`approach3_async_middleware.py`)

**Status**: Works today with infrastructure deployment.

Two sub-patterns:
- **Pattern A**: Direct proxy with budget (simple, for queries < 2 min)
- **Pattern B**: Submit + Poll (job queue for any query length)

In Copilot Studio, use Power Automate HTTP actions to call the middleware.

## Approach 4: SQL API Async (`approach4_sql_api_async.py`)

**Status**: Works today, no middleware needed.

Uses the Snowflake SQL API's native async execution:
1. POST `/api/v2/statements` with `"async": true` + `SNOWFLAKE.CORTEX.AGENT_RUN(...)` SQL
2. Get `statementHandle` immediately (< 1 second)
3. GET `/api/v2/statements/{handle}` to poll (returns 202 while running, 200 when done)
4. Parse the JSON result

This is the **recommended immediate workaround** because:
- No account enablement needed
- No middleware infrastructure
- Pure REST/JSON (no streaming)
- Native async with polling via statementHandle
- Directly usable from Power Automate HTTP actions

## Configuration

Set these environment variables:

```bash
export SNOWFLAKE_ACCOUNT_URL="https://<orgname>-<account>.snowflakecomputing.com"
export SNOWFLAKE_PAT="<your-programmatic-access-token>"
```

Update the constants in each file:
- `AGENT_DATABASE`, `AGENT_SCHEMA`, `AGENT_NAME`
- `WAREHOUSE`, `ROLE`

## Copilot Studio Power Automate Flow (Approach 4)

```
[Trigger: User message]
    |
    v
[HTTP POST: Submit async SQL statement]
    |
    v
[Parse: Extract statementHandle]
    |
    v
[Do Until: status != running, max 50 iterations]
    |-> [Delay: 3 seconds]
    |-> [HTTP GET: Check statement status]
    |-> [Condition: HTTP status == 200 -> exit loop]
    |
    v
[Parse: Extract data[0][0] from result]
    |
    v
[Parse JSON: Get agent answer text]
    |
    v
[Return: Answer to user]
```

## Multi-Turn Conversations

For follow-up questions, use threads:
1. On first call, capture `thread_id` and `assistant_message_id` from the response
2. Pass them back on subsequent calls as `parent_message_id`
3. Store thread state in Copilot Studio conversation variables

## Dependencies

```
pip3 install requests
```

No Snowflake connector or SDK required - all interactions use REST APIs.


## Example Run


```
======================================================================
CORTEX AGENT API - COPILOT STUDIO INTEGRATION TESTS
======================================================================
Run Time:     2026-07-29T01:07:24Z
Platform:     Darwin 25.5.0 (arm64)
Python:       3.11.9
Snow CLI:     Snowflake CLI version: 3.23.0
Agent:        DEMO.DEMO.AVIATION_WEATHER_AGENT
Model:        claude-sonnet-4-5
Account URL:  (using snow CLI default)

======================================================================
TEST: Approach 2 - Budget Constraint (DATA_AGENT_RUN)
======================================================================
  Status: completed
  Answer: 2 + 2 = 4
  ────────────────────────────────────────────────────────────
  Timing:     9.934s (started 01:07:26.923Z)
  Schema:     v2 (seq #9)
  Model:      claude-opus-4-8 (context window: 1,000,000)
  Tokens:     in=5248 (1502/5248 cached), out=40
  Total:      5288 tokens
  Content:    ['thinking', 'text']
  Response:   410 bytes
  ────────────────────────────────────────────────────────────
  RESULT: PASS

======================================================================
TEST: Approach 2 - Budget Constraint (AGENT_RUN objectless)
======================================================================
  Status: completed
  Answer: 

2 + 2 = 4
  ────────────────────────────────────────────────────────────
  Timing:     12.420s (started 01:07:36.858Z)
  Schema:     v2 (seq #23)
  Model:      claude-sonnet-4-5 (context window: 200,000)
  Tokens:     in=3801 (1080/3801 cached), out=60
  Total:      3861 tokens
  Content:    ['thinking', 'text']
  Response:   540 bytes
  ────────────────────────────────────────────────────────────
  RESULT: PASS

======================================================================
TEST: Approach 4 - SQL API pattern (AGENT_RUN via SQL)
======================================================================
  Status: completed
  Answer: 

2 + 2 = 4
  ────────────────────────────────────────────────────────────
  Timing:     20.473s (started 01:07:49.278Z)
  Schema:     v2 (seq #19)
  Model:      claude-sonnet-4-5 (context window: 200,000)
  Tokens:     in=1083 (1080/1083 cached), out=58
  Total:      1141 tokens
  Response:   351 bytes
  ────────────────────────────────────────────────────────────
  RESULT: PASS

======================================================================
TEST: Approach 1 - Background Mode
======================================================================
  Thread created: 45096044029 (26.36s)
  Assistant message: 2955414241470018
  Background mode WORKS!
  Run ID: 45096044029-2955414241467498
  Timing: thread creation=26.36s, bg submit=5.44s
  RESULT: PASS (background mode enabled on this account)

======================================================================
TEST: Multi-turn Conversation via Threads
======================================================================
  Turn 1: thread=45096044033, msg=2955414241470022 (12.01s)
  Turn 2: (6.61s)
  Answer: 

You asked me to remember the number 42.
  Contains '42': True
  ────────────────────────────────────────────────────────────
  Timing:     18.625s (started 01:08:41.558Z)
  Run ID:     45096044033-2955414241469526
  Thread:     45096044033 → msg 2955414241470026
  Schema:     v2 (seq #20)
  Model:      claude-sonnet-4-5 (context window: 200,000)
  Tokens:     in=3903 (1130/3903 cached), out=62
  Total:      3965 tokens
  Content:    ['thinking', 'text']
  Response:   709 bytes
  ────────────────────────────────────────────────────────────
  Timing breakdown: Turn1=12.01s + Turn2=6.61s = 18.62s
  RESULT: PASS

======================================================================
SUMMARY
======================================================================
  Test                                Status      Time      Tokens
  ─────────────────────────────────── ──────  ────────  ──────────
  ✓ approach2_budget (DATA_AGENT_RUN)   pass       9.93s    5288 tok
  ✓ approach2_objectless (AGENT_RUN)    pass      12.42s    3861 tok
  ✓ approach4_sql_api (CTE+FLATTEN)     pass      20.47s    1141 tok
  ✓ approach1_background (async)        pass      31.81s       0 tok
  ✓ multi_turn (thread context)         pass      18.62s    3965 tok

  5/5 tests passed
  Total agent time: 93.26s
  Total suite time: 95.28s (includes snow CLI overhead)
  Total tokens:     14,255

----------------------------------------------------------------------
TIMING ANALYSIS (Copilot Studio 2-min budget):
----------------------------------------------------------------------
  approach2_budget (DATA_AGENT_RUN)      9.93s  ✓ WITHIN 2-min limit
  approach2_objectless (AGENT_RUN)      12.42s  ✓ WITHIN 2-min limit
  approach4_sql_api (CTE+FLATTEN)       20.47s  ✓ WITHIN 2-min limit
  approach1_background (async)          31.81s  ✓ WITHIN 2-min limit
  multi_turn (thread context)           18.62s  ✓ WITHIN 2-min limit

  Avg single-call latency: 14.28s
  Estimated budget usage:  12% of 2-min limit

----------------------------------------------------------------------
RECOMMENDATIONS FOR VOYA COPILOT STUDIO:
----------------------------------------------------------------------
  ● Background mode is ENABLED on this account.
    → Use Approach 1 (background + thread polling) for best results.
  ● SQL API async (Approach 4) works.
    → RECOMMENDED: Use SQL API async for Copilot Studio integration.
  ● Budget constraint (Approach 2) works.
    → Use as simple fallback (avg latency: 9.9s).

----------------------------------------------------------------------
TRACE LOG (JSON):
----------------------------------------------------------------------
{
  "run_timestamp": "2026-07-29T01:07:24Z",
  "suite_elapsed_seconds": 95.284,
  "total_tokens": 14255,
  "passed": 5,
  "total": 5,
  "tests": [
    {
      "name": "approach2_budget (DATA_AGENT_RUN)",
      "status": "pass",
      "elapsed_seconds": 9.934,
      "started_at": "01:07:26.923Z",
      "ended_at": "01:07:36.857Z",
      "run_id": "",
      "thread_id": null,
      "request_id": "",
      "model": "claude-opus-4-8",
      "input_tokens": 5248,
      "output_tokens": 40,
      "cache_hit_tokens": 1502,
      "response_bytes": 410,
      "answer_preview": "2 + 2 = 4",
      "error": null
    },
    {
      "name": "approach2_objectless (AGENT_RUN)",
      "status": "pass",
      "elapsed_seconds": 12.42,
      "started_at": "01:07:36.858Z",
      "ended_at": "01:07:49.278Z",
      "run_id": "",
      "thread_id": null,
      "request_id": "",
      "model": "claude-sonnet-4-5",
      "input_tokens": 3801,
      "output_tokens": 60,
      "cache_hit_tokens": 1080,
      "response_bytes": 540,
      "answer_preview": "2 + 2 = 4",
      "error": null
    },
    {
      "name": "approach4_sql_api (CTE+FLATTEN)",
      "status": "pass",
      "elapsed_seconds": 20.473,
      "started_at": "01:07:49.278Z",
      "ended_at": "01:08:09.751Z",
      "run_id": "",
      "thread_id": null,
      "request_id": "",
      "model": "claude-sonnet-4-5",
      "input_tokens": 1083,
      "output_tokens": 58,
      "cache_hit_tokens": 1080,
      "response_bytes": 351,
      "answer_preview": "2 + 2 = 4",
      "error": null
    },
    {
      "name": "approach1_background (async)",
      "status": "pass",
      "elapsed_seconds": 31.807,
      "started_at": "01:08:09.751Z",
      "ended_at": "01:08:41.558Z",
      "run_id": "45096044029-2955414241467498",
      "thread_id": 45096044029,
      "request_id": "",
      "model": "",
      "input_tokens": 0,
      "output_tokens": 0,
      "cache_hit_tokens": 0,
      "response_bytes": 112,
      "answer_preview": "",
      "error": null
    },
    {
      "name": "multi_turn (thread context)",
      "status": "pass",
      "elapsed_seconds": 18.625,
      "started_at": "01:08:41.558Z",
      "ended_at": "01:09:00.183Z",
      "run_id": "45096044033-2955414241469526",
      "thread_id": 45096044033,
      "request_id": "",
      "model": "claude-sonnet-4-5",
      "input_tokens": 3903,
      "output_tokens": 62,
      "cache_hit_tokens": 1130,
      "response_bytes": 709,
      "answer_preview": "You asked me to remember the number 42.",
      "error": null
    }
  ]
}

```



