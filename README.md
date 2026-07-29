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
