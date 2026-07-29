"""
Integration Test: Cortex Agent API - Copilot Studio Workarounds
================================================================

Validates all approaches against a live Snowflake account.
Includes timing, token usage tracking, request tracing, and detailed diagnostics.

Run: python3 test_integration.py

Prerequisites:
- Environment variables SNOWFLAKE_ACCOUNT_URL and SNOWFLAKE_PAT set
- OR: Uses Snowflake CLI connection if available
"""

import os
import sys
import json
import time
import platform
import subprocess
from datetime import datetime, timezone
from dataclasses import dataclass, field
from typing import Optional

# Try to detect connection info
SNOWFLAKE_ACCOUNT_URL = os.environ.get("SNOWFLAKE_ACCOUNT_URL", "")
SNOWFLAKE_PAT = os.environ.get("SNOWFLAKE_PAT", "")

# Test configuration
AGENT_FQN = "DEMO.DEMO.AVIATION_WEATHER_AGENT"  # Known working agent
TEST_QUESTION = "What is 2+2?"
ORCHESTRATION_MODEL = "claude-sonnet-4-5"

# Available models (discovered from error diagnostics):
# claude-haiku-4-5, claude-opus-4-5, claude-opus-4-6, claude-opus-4-7,
# claude-opus-4-8, claude-opus-5, claude-sonnet-4-5, claude-sonnet-4-6,
# claude-sonnet-5, gemini-3.1-pro, gemini-3.5-flash, openai-gpt-4.1,
# openai-gpt-5, openai-gpt-5-mini, openai-gpt-5.1, openai-gpt-5.2, openai-gpt-5.4


# =============================================================================
# Tracing / Metrics Data Classes
# =============================================================================

@dataclass
class TokenUsage:
    model_name: str = ""
    input_tokens_total: int = 0
    input_tokens_cached: int = 0
    output_tokens: int = 0
    context_window: int = 0

    @classmethod
    def from_response(cls, response: dict) -> "TokenUsage":
        tokens = (response.get("metadata", {})
                  .get("usage", {})
                  .get("tokens_consumed", []))
        if tokens:
            t = tokens[0]
            return cls(
                model_name=t.get("model_name", ""),
                input_tokens_total=t.get("input_tokens", {}).get("total", 0),
                input_tokens_cached=t.get("input_tokens", {}).get("cache_read", 0),
                output_tokens=t.get("output_tokens", {}).get("total", 0),
                context_window=t.get("context_window", 0),
            )
        return cls()


@dataclass
class TestTrace:
    test_name: str
    started_at: str = ""
    ended_at: str = ""
    elapsed_seconds: float = 0.0
    status: str = "pending"  # pass, fail, error, skip
    answer: str = ""
    error_message: str = ""
    # Agent metadata
    run_id: str = ""
    thread_id: Optional[int] = None
    assistant_message_id: Optional[int] = None
    request_id: str = ""
    schema_version: str = ""
    sequence_number: Optional[int] = None
    # Token usage
    token_usage: TokenUsage = field(default_factory=TokenUsage)
    # Additional context
    sql_query: str = ""
    raw_response_size_bytes: int = 0
    content_types_found: list = field(default_factory=list)

    def summary_line(self) -> str:
        icon = "✓" if self.status == "pass" else "✗" if self.status == "fail" else "?"
        timing = f"{self.elapsed_seconds:.2f}s"
        tokens = f"{self.token_usage.input_tokens_total + self.token_usage.output_tokens} tok"
        return f"  {icon} {self.test_name:35s} {self.status:6s}  {timing:>8s}  {tokens:>10s}"


# =============================================================================
# Core Infrastructure
# =============================================================================

def _has_snowflake_cli():
    """Check if snow CLI is available for running SQL."""
    return os.system("which snow > /dev/null 2>&1") == 0


def _get_snow_cli_version() -> str:
    """Get the snow CLI version for diagnostics."""
    try:
        result = subprocess.run(
            ["snow", "--version"], capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip()
    except Exception:
        return "unknown"


def _get_snowflake_connection_info() -> dict:
    """Get current connection details from snow CLI."""
    try:
        result = subprocess.run(
            ["snow", "connection", "status", "--format", "json"],
            capture_output=True, text=True, timeout=15
        )
        if result.returncode == 0:
            return json.loads(result.stdout)
    except Exception:
        pass
    return {}


def _run_sql(sql: str) -> str:
    """Execute SQL via snow CLI and return result."""
    cmd = ["snow", "sql", "-q", sql, "--format", "json"]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if result.returncode != 0:
        raise RuntimeError(f"SQL failed: {result.stderr}")
    return result.stdout


def _run_sql_timed(sql: str) -> tuple:
    """Execute SQL and return (output, elapsed_seconds)."""
    start = time.perf_counter()
    output = _run_sql(sql)
    elapsed = time.perf_counter() - start
    return output, elapsed


def _extract_trace_from_response(response: dict, trace: TestTrace):
    """Populate trace object with metadata from agent response."""
    metadata = response.get("metadata", {})
    trace.run_id = metadata.get("run_id", "")
    trace.thread_id = metadata.get("thread_id")
    trace.assistant_message_id = metadata.get("assistant_message_id")
    trace.request_id = response.get("request_id", "")
    trace.schema_version = response.get("schema_version", "")
    trace.sequence_number = response.get("sequence_number")
    trace.token_usage = TokenUsage.from_response(response)
    # Track content types present
    trace.content_types_found = [
        c.get("type", "unknown") for c in response.get("content", [])
    ]


def _print_trace(trace: TestTrace):
    """Print detailed trace information for a test."""
    print(f"  {'─' * 60}")
    print(f"  Timing:     {trace.elapsed_seconds:.3f}s (started {trace.started_at})")
    if trace.run_id:
        print(f"  Run ID:     {trace.run_id}")
    if trace.thread_id:
        print(f"  Thread:     {trace.thread_id} → msg {trace.assistant_message_id}")
    if trace.request_id:
        print(f"  Request ID: {trace.request_id}")
    if trace.schema_version:
        print(f"  Schema:     {trace.schema_version} (seq #{trace.sequence_number})")
    if trace.token_usage.model_name:
        tu = trace.token_usage
        cache_pct = (
            f" ({tu.input_tokens_cached}/{tu.input_tokens_total} cached)"
            if tu.input_tokens_cached else ""
        )
        print(f"  Model:      {tu.model_name} (context window: {tu.context_window:,})")
        print(f"  Tokens:     in={tu.input_tokens_total}{cache_pct}, out={tu.output_tokens}")
        print(f"  Total:      {tu.input_tokens_total + tu.output_tokens} tokens")
    if trace.content_types_found:
        print(f"  Content:    {trace.content_types_found}")
    print(f"  Response:   {trace.raw_response_size_bytes:,} bytes")
    print(f"  {'─' * 60}")


# =============================================================================
# Test Functions
# =============================================================================

def test_approach2_budget_constraint() -> TestTrace:
    """
    Test Approach 2: Direct synchronous call with budget constraint.
    Uses DATA_AGENT_RUN with an agent object.
    """
    trace = TestTrace(test_name="approach2_budget (DATA_AGENT_RUN)")
    trace.started_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

    print("\n" + "=" * 70)
    print("TEST: Approach 2 - Budget Constraint (DATA_AGENT_RUN)")
    print("=" * 70)

    sql = f"""
    SELECT SNOWFLAKE.CORTEX.DATA_AGENT_RUN(
        '{AGENT_FQN}',
        $${{
            "messages": [{{"role": "user", "content": [{{"type": "text", "text": "{TEST_QUESTION}"}}]}}],
            "orchestration": {{"budget": {{"seconds": 60}}}}
        }}$$
    ) AS response
    """
    trace.sql_query = sql.strip()

    try:
        output, elapsed = _run_sql_timed(sql)
        trace.elapsed_seconds = elapsed
        trace.ended_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

        data = json.loads(output)
        response_str = data[0]["RESPONSE"] if data else ""
        trace.raw_response_size_bytes = len(response_str.encode())
        response = json.loads(response_str) if response_str else {}

        _extract_trace_from_response(response, trace)

        status = response.get("status", "unknown")
        content = response.get("content", [])
        text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")

        trace.answer = text.strip()
        trace.status = "pass" if status == "completed" else "fail"

        print(f"  Status: {status}")
        print(f"  Answer: {text[:200]}")
        _print_trace(trace)
        print(f"  RESULT: {'PASS' if trace.status == 'pass' else 'FAIL'}")

    except Exception as e:
        trace.elapsed_seconds = time.perf_counter()  # approximate
        trace.status = "error"
        trace.error_message = str(e)
        print(f"  ERROR: {e}")
        print(f"  RESULT: FAIL")

    return trace


def test_approach2_objectless() -> TestTrace:
    """
    Test Approach 2 variant: AGENT_RUN (objectless) with budget.
    This doesn't require a pre-created agent object.
    """
    trace = TestTrace(test_name="approach2_objectless (AGENT_RUN)")
    trace.started_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

    print("\n" + "=" * 70)
    print("TEST: Approach 2 - Budget Constraint (AGENT_RUN objectless)")
    print("=" * 70)

    sql = f"""
    SELECT SNOWFLAKE.CORTEX.AGENT_RUN(
        $${{
            "messages": [{{"role": "user", "content": [{{"type": "text", "text": "{TEST_QUESTION}"}}]}}],
            "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}},
            "orchestration": {{"budget": {{"seconds": 30}}}}
        }}$$
    ) AS response
    """
    trace.sql_query = sql.strip()

    try:
        output, elapsed = _run_sql_timed(sql)
        trace.elapsed_seconds = elapsed
        trace.ended_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

        data = json.loads(output)
        response_str = data[0]["RESPONSE"] if data else ""
        trace.raw_response_size_bytes = len(response_str.encode())
        response = json.loads(response_str) if response_str else {}

        _extract_trace_from_response(response, trace)

        status = response.get("status", "unknown")
        content = response.get("content", [])
        text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")

        trace.answer = text.strip()
        trace.status = "pass" if status == "completed" else "fail"

        print(f"  Status: {status}")
        print(f"  Answer: {text[:200]}")
        _print_trace(trace)
        print(f"  RESULT: {'PASS' if trace.status == 'pass' else 'FAIL'}")

    except Exception as e:
        trace.status = "error"
        trace.error_message = str(e)
        print(f"  ERROR: {e}")
        print(f"  RESULT: FAIL")

    return trace


def test_approach4_sql_api_async() -> TestTrace:
    """
    Test Approach 4: SQL API async execution pattern.
    Uses CTE + FLATTEN to correctly extract text (skipping thinking blocks).
    """
    trace = TestTrace(test_name="approach4_sql_api (CTE+FLATTEN)")
    trace.started_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

    print("\n" + "=" * 70)
    print("TEST: Approach 4 - SQL API pattern (AGENT_RUN via SQL)")
    print("=" * 70)

    sql = f"""
    WITH agent_resp AS (
        SELECT TRY_PARSE_JSON(
            SNOWFLAKE.CORTEX.AGENT_RUN(
                $${{
                    "messages": [{{"role": "user", "content": [{{"type": "text", "text": "{TEST_QUESTION}"}}]}}],
                    "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}},
                    "orchestration": {{"budget": {{"seconds": 30}}}}
                }}$$
            )
        ) AS resp
    )
    SELECT
        resp:status::STRING AS status,
        resp:metadata:run_id::STRING AS run_id,
        resp:metadata:thread_id::NUMBER AS thread_id,
        resp:schema_version::STRING AS schema_version,
        resp:sequence_number::NUMBER AS seq_num,
        resp:metadata:usage:tokens_consumed[0]:model_name::STRING AS model_name,
        resp:metadata:usage:tokens_consumed[0]:input_tokens:total::NUMBER AS input_tokens,
        resp:metadata:usage:tokens_consumed[0]:input_tokens:cache_read::NUMBER AS cache_read,
        resp:metadata:usage:tokens_consumed[0]:output_tokens:total::NUMBER AS output_tokens,
        resp:metadata:usage:tokens_consumed[0]:context_window::NUMBER AS context_window,
        c.value:text::STRING AS answer
    FROM agent_resp,
        LATERAL FLATTEN(input => resp:content) c
    WHERE c.value:type::STRING = 'text'
    """
    trace.sql_query = sql.strip()

    try:
        output, elapsed = _run_sql_timed(sql)
        trace.elapsed_seconds = elapsed
        trace.ended_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

        data = json.loads(output)
        trace.raw_response_size_bytes = len(output.encode())

        if data:
            row = data[0]
            status = row.get("STATUS", "unknown")
            answer = row.get("ANSWER") or ""
            trace.answer = answer.strip()
            trace.run_id = row.get("RUN_ID") or ""
            trace.thread_id = row.get("THREAD_ID")
            trace.schema_version = row.get("SCHEMA_VERSION") or ""
            trace.sequence_number = row.get("SEQ_NUM")
            trace.token_usage = TokenUsage(
                model_name=row.get("MODEL_NAME") or "",
                input_tokens_total=row.get("INPUT_TOKENS") or 0,
                input_tokens_cached=row.get("CACHE_READ") or 0,
                output_tokens=row.get("OUTPUT_TOKENS") or 0,
                context_window=row.get("CONTEXT_WINDOW") or 0,
            )
            trace.status = "pass" if status == "completed" else "fail"
        else:
            trace.status = "fail"
            status = "no_data"
            answer = ""

        print(f"  Status: {status}")
        print(f"  Answer: {answer[:200]}")
        _print_trace(trace)
        print(f"  RESULT: {'PASS' if trace.status == 'pass' else 'FAIL'}")

    except Exception as e:
        trace.status = "error"
        trace.error_message = str(e)
        print(f"  ERROR: {e}")
        print(f"  RESULT: FAIL")

    return trace


def test_approach1_background_mode() -> TestTrace:
    """
    Test Approach 1: Background mode with thread.
    First creates a thread (auto-create=TRUE), then tests background=true.
    """
    trace = TestTrace(test_name="approach1_background (async)")
    trace.started_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

    print("\n" + "=" * 70)
    print("TEST: Approach 1 - Background Mode")
    print("=" * 70)

    # Step 1: Create a thread via auto-create
    sql_create = f"""
    SELECT SNOWFLAKE.CORTEX.AGENT_RUN(
        $${{
            "messages": [{{"role": "user", "content": [{{"type": "text", "text": "hello"}}]}}],
            "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}}
        }}$$,
        TRUE
    ) AS response
    """

    try:
        output, elapsed_create = _run_sql_timed(sql_create)
        data = json.loads(output)
        response_str = data[0]["RESPONSE"] if data else ""
        response = json.loads(response_str) if response_str else {}

        thread_id = response.get("metadata", {}).get("thread_id")
        assistant_msg_id = response.get("metadata", {}).get("assistant_message_id")

        if not thread_id:
            trace.status = "fail"
            trace.error_message = "Could not create thread"
            trace.elapsed_seconds = elapsed_create
            print("  Could not create thread")
            print("  RESULT: FAIL")
            return trace

        print(f"  Thread created: {thread_id} ({elapsed_create:.2f}s)")
        print(f"  Assistant message: {assistant_msg_id}")

        # Step 2: Test background mode
        sql_bg = f"""
        SELECT SNOWFLAKE.CORTEX.AGENT_RUN(
            $${{
                "messages": [{{"role": "user", "content": [{{"type": "text", "text": "{TEST_QUESTION}"}}]}}],
                "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}},
                "background": true,
                "thread_id": {thread_id},
                "parent_message_id": {assistant_msg_id}
            }}$$
        ) AS response
        """

        output, elapsed_bg = _run_sql_timed(sql_bg)
        trace.elapsed_seconds = elapsed_create + elapsed_bg
        trace.ended_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

        data = json.loads(output)
        response_str = data[0]["RESPONSE"] if data else ""
        trace.raw_response_size_bytes = len(response_str.encode())
        response = json.loads(response_str) if response_str else {}

        trace.thread_id = thread_id

        if response.get("code") == "399500":
            trace.status = "fail"
            trace.error_message = f"Background mode NOT enabled: {response.get('message')}"
            trace.request_id = response.get("request_id", "")
            print(f"  Background mode NOT enabled: {response.get('message')}")
            print(f"  Request ID: {trace.request_id}")
            print(f"  Timing: thread creation={elapsed_create:.2f}s, bg call={elapsed_bg:.2f}s")
            print("  RESULT: EXPECTED FAIL (account not enabled)")
        elif response.get("status") == "in_progress":
            run_id = response.get("metadata", {}).get("run_id")
            trace.run_id = run_id or ""
            trace.status = "pass"
            print(f"  Background mode WORKS!")
            print(f"  Run ID: {run_id}")
            print(f"  Timing: thread creation={elapsed_create:.2f}s, bg submit={elapsed_bg:.2f}s")
            print(f"  RESULT: PASS (background mode enabled on this account)")
        else:
            trace.status = "fail"
            trace.error_message = f"Unexpected: {json.dumps(response)[:200]}"
            print(f"  Unexpected response: {json.dumps(response)[:200]}")
            print("  RESULT: UNKNOWN")

    except Exception as e:
        trace.status = "error"
        trace.error_message = str(e)
        print(f"  ERROR: {e}")
        print(f"  RESULT: FAIL")

    return trace


def test_multi_turn_with_thread() -> TestTrace:
    """
    Test multi-turn conversation using threads.
    Critical for Copilot Studio follow-up questions.
    """
    trace = TestTrace(test_name="multi_turn (thread context)")
    trace.started_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

    print("\n" + "=" * 70)
    print("TEST: Multi-turn Conversation via Threads")
    print("=" * 70)

    # Turn 1: Initial question with auto-create thread
    sql_turn1 = f"""
    SELECT SNOWFLAKE.CORTEX.AGENT_RUN(
        $${{
            "messages": [{{"role": "user", "content": [{{"type": "text", "text": "Remember the number 42."}}]}}],
            "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}}
        }}$$,
        TRUE
    ) AS response
    """

    try:
        output, elapsed_t1 = _run_sql_timed(sql_turn1)
        data = json.loads(output)
        response = json.loads(data[0]["RESPONSE"]) if data else {}

        thread_id = response.get("metadata", {}).get("thread_id")
        assistant_msg_id = response.get("metadata", {}).get("assistant_message_id")

        if not thread_id:
            trace.status = "fail"
            trace.error_message = "Turn 1 failed - no thread_id"
            trace.elapsed_seconds = elapsed_t1
            print("  Turn 1 failed - no thread_id")
            print("  RESULT: FAIL")
            return trace

        print(f"  Turn 1: thread={thread_id}, msg={assistant_msg_id} ({elapsed_t1:.2f}s)")

        # Turn 2: Follow-up using thread context
        sql_turn2 = f"""
        SELECT SNOWFLAKE.CORTEX.AGENT_RUN(
            $${{
                "messages": [{{"role": "user", "content": [{{"type": "text", "text": "What number did I ask you to remember?"}}]}}],
                "models": {{"orchestration": "{ORCHESTRATION_MODEL}"}},
                "thread_id": {thread_id},
                "parent_message_id": {assistant_msg_id}
            }}$$
        ) AS response
        """

        output, elapsed_t2 = _run_sql_timed(sql_turn2)
        trace.elapsed_seconds = elapsed_t1 + elapsed_t2
        trace.ended_at = datetime.now(timezone.utc).strftime("%H:%M:%S.%f")[:-3] + "Z"

        data = json.loads(output)
        response_str = data[0]["RESPONSE"] if data else ""
        trace.raw_response_size_bytes = len(response_str.encode())
        response = json.loads(response_str) if response_str else {}

        _extract_trace_from_response(response, trace)
        trace.thread_id = thread_id

        content = response.get("content", [])
        text = next((c.get("text", "") for c in content if c.get("type") == "text"), "")

        has_42 = "42" in text
        trace.answer = text.strip()
        trace.status = "pass" if has_42 else "fail"

        print(f"  Turn 2: ({elapsed_t2:.2f}s)")
        print(f"  Answer: {text[:200]}")
        print(f"  Contains '42': {has_42}")
        _print_trace(trace)
        print(f"  Timing breakdown: Turn1={elapsed_t1:.2f}s + Turn2={elapsed_t2:.2f}s = {trace.elapsed_seconds:.2f}s")
        print(f"  RESULT: {'PASS' if has_42 else 'FAIL'}")

    except Exception as e:
        trace.status = "error"
        trace.error_message = str(e)
        print(f"  ERROR: {e}")
        print(f"  RESULT: FAIL")

    return trace


# =============================================================================
# Main
# =============================================================================

def main():
    suite_start = time.perf_counter()
    run_timestamp = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

    print("=" * 70)
    print("CORTEX AGENT API - COPILOT STUDIO INTEGRATION TESTS")
    print("=" * 70)
    print(f"Run Time:     {run_timestamp}")
    print(f"Platform:     {platform.system()} {platform.release()} ({platform.machine()})")
    print(f"Python:       {platform.python_version()}")
    print(f"Snow CLI:     {_get_snow_cli_version()}")
    print(f"Agent:        {AGENT_FQN}")
    print(f"Model:        {ORCHESTRATION_MODEL}")

    if not _has_snowflake_cli():
        print("\nERROR: 'snow' CLI not found. Install with: pip install snowflake-cli")
        print("Or set SNOWFLAKE_ACCOUNT_URL and SNOWFLAKE_PAT for REST API tests.")
        sys.exit(1)

    # Get connection info
    conn_info = _get_snowflake_connection_info()
    if conn_info:
        print(f"Account:      {conn_info.get('Account', 'N/A')}")
        print(f"User:         {conn_info.get('User', 'N/A')}")
        print(f"Role:         {conn_info.get('Role', 'N/A')}")
        print(f"Warehouse:    {conn_info.get('Warehouse', 'N/A')}")
    else:
        print(f"Account URL:  {SNOWFLAKE_ACCOUNT_URL or '(using snow CLI default)'}")

    # Run all tests
    traces: list[TestTrace] = []

    traces.append(test_approach2_budget_constraint())
    traces.append(test_approach2_objectless())
    traces.append(test_approach4_sql_api_async())
    traces.append(test_approach1_background_mode())
    traces.append(test_multi_turn_with_thread())

    suite_elapsed = time.perf_counter() - suite_start

    # ==========================================================================
    # Summary
    # ==========================================================================
    print("\n" + "=" * 70)
    print("SUMMARY")
    print("=" * 70)
    print(f"  {'Test':35s} {'Status':6s}  {'Time':>8s}  {'Tokens':>10s}")
    print(f"  {'─' * 35} {'─' * 6}  {'─' * 8}  {'─' * 10}")
    for t in traces:
        print(t.summary_line())

    total = len(traces)
    passed = sum(1 for t in traces if t.status == "pass")
    total_elapsed = sum(t.elapsed_seconds for t in traces)
    total_tokens = sum(
        t.token_usage.input_tokens_total + t.token_usage.output_tokens
        for t in traces
    )

    print(f"\n  {passed}/{total} tests passed")
    print(f"  Total agent time: {total_elapsed:.2f}s")
    print(f"  Total suite time: {suite_elapsed:.2f}s (includes snow CLI overhead)")
    print(f"  Total tokens:     {total_tokens:,}")

    # Timing analysis for Copilot Studio feasibility
    print("\n" + "-" * 70)
    print("TIMING ANALYSIS (Copilot Studio 2-min budget):")
    print("-" * 70)
    for t in traces:
        within = "✓ WITHIN" if t.elapsed_seconds < 120 else "✗ EXCEEDS"
        print(f"  {t.test_name:35s} {t.elapsed_seconds:>7.2f}s  {within} 2-min limit")

    avg_single_call = sum(
        t.elapsed_seconds for t in traces
        if "multi_turn" not in t.test_name and "background" not in t.test_name
    ) / max(1, sum(
        1 for t in traces
        if "multi_turn" not in t.test_name and "background" not in t.test_name
    ))
    print(f"\n  Avg single-call latency: {avg_single_call:.2f}s")
    print(f"  Estimated budget usage:  {avg_single_call/120*100:.0f}% of 2-min limit")

    # Recommendations based on results
    print("\n" + "-" * 70)
    print("RECOMMENDATIONS FOR VOYA COPILOT STUDIO:")
    print("-" * 70)
    bg_trace = next((t for t in traces if "background" in t.test_name), None)
    if bg_trace and bg_trace.status == "pass":
        print("  ● Background mode is ENABLED on this account.")
        print("    → Use Approach 1 (background + thread polling) for best results.")
    else:
        print("  ● Background mode is NOT enabled (error 399500).")
        print("    → Request enablement from Snowflake account team.")

    a4_trace = next((t for t in traces if "approach4" in t.test_name), None)
    if a4_trace and a4_trace.status == "pass":
        print("  ● SQL API async (Approach 4) works.")
        print("    → RECOMMENDED: Use SQL API async for Copilot Studio integration.")

    a2_trace = next((t for t in traces if "approach2_budget" in t.test_name), None)
    if a2_trace and a2_trace.status == "pass":
        print("  ● Budget constraint (Approach 2) works.")
        print(f"    → Use as simple fallback (avg latency: {a2_trace.elapsed_seconds:.1f}s).")

    # JSON trace output for programmatic consumption
    print("\n" + "-" * 70)
    print("TRACE LOG (JSON):")
    print("-" * 70)
    trace_log = {
        "run_timestamp": run_timestamp,
        "suite_elapsed_seconds": round(suite_elapsed, 3),
        "total_tokens": total_tokens,
        "passed": passed,
        "total": total,
        "tests": [
            {
                "name": t.test_name,
                "status": t.status,
                "elapsed_seconds": round(t.elapsed_seconds, 3),
                "started_at": t.started_at,
                "ended_at": t.ended_at,
                "run_id": t.run_id,
                "thread_id": t.thread_id,
                "request_id": t.request_id,
                "model": t.token_usage.model_name,
                "input_tokens": t.token_usage.input_tokens_total,
                "output_tokens": t.token_usage.output_tokens,
                "cache_hit_tokens": t.token_usage.input_tokens_cached,
                "response_bytes": t.raw_response_size_bytes,
                "answer_preview": t.answer[:100] if t.answer else "",
                "error": t.error_message or None,
            }
            for t in traces
        ],
    }
    print(json.dumps(trace_log, indent=2))


if __name__ == "__main__":
    main()
