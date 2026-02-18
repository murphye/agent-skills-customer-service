#!/usr/bin/env python3
# /// script
# requires-python = ">=3.10"
# dependencies = ["pyyaml"]
# ///
"""
Skill Test Harness — run multi-turn agent tests via the `claude` CLI.

Usage:
    # Run all scenarios in a directory
    uv run skills/customer-service/tests/run_test.py \
    skills/customer-service/tests/scenarios/ \
    -d skills/customer-service

    # Run a single scenario
    uv run skills/customer-service/tests/run_test.py \
    skills/customer-service/tests/scenarios/refund_auto_approve.yaml \
    -d skills/customer-service
"""

import argparse
import json
import os
import re
import subprocess
import sys
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

try:
    import yaml
except ImportError:
    print("PyYAML required: pip install pyyaml", file=sys.stderr)
    sys.exit(1)


# ── Data classes ────────────────────────────────────────────────────────────

@dataclass
class ToolCall:
    """A single tool invocation captured from stream-json output."""
    tool_name: str
    tool_input: dict
    raw_command: str  # reconstructed CLI-style string for pattern matching


@dataclass
class TurnResult:
    """Everything captured from one agent turn."""
    text_responses: list[str] = field(default_factory=list)
    tool_calls: list[ToolCall] = field(default_factory=list)
    tool_results: list[dict] = field(default_factory=list)
    raw_messages: list[dict] = field(default_factory=list)


@dataclass
class TestResult:
    """Aggregate result for one test plan."""
    name: str
    passed: bool = True
    tool_call_results: list[dict] = field(default_factory=list)
    outcome_results: list[dict] = field(default_factory=list)
    quality_results: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    total_turns: int = 0
    duration_s: float = 0.0


# ── Claude CLI wrapper ─────────────────────────────────────────────────────

def _clean_env(extra_env: Optional[dict] = None) -> dict:
    """Return a copy of os.environ safe for spawning a child `claude` process.

    Strips only CLAUDECODE (nested-session flag).  Keeps everything else
    so the child inherits locale, PATH, TMPDIR, etc.
    """
    env = {k: v for k, v in os.environ.items() if k != "CLAUDECODE"}
    if extra_env:
        env.update(extra_env)
    return env


def _find_project_root() -> str:
    """Walk up from this file to find the git repo root."""
    d = Path(__file__).resolve().parent
    while d != d.parent:
        if (d / ".git").exists():
            return str(d)
        d = d.parent
    return str(Path(__file__).resolve().parent)


def call_claude(
    prompt: str,
    session_id: Optional[str] = None,
    skill_dir: Optional[str] = None,
    extra_flags: Optional[list[str]] = None,
    verbose: bool = False,
    env_override: Optional[dict] = None,
    model: Optional[str] = None,
) -> tuple[str, list[dict]]:
    """
    Send a prompt to `claude` in print mode with stream-json output.

    Returns (session_id, list_of_json_messages).
    """
    cmd = ["claude", "-p", prompt, "--output-format", "stream-json", "--verbose"]

    if model:
        cmd += ["--model", model]

    # Resume existing session for multi-turn
    if session_id:
        cmd += ["--resume", session_id]

    # Point at the skill's working directory
    if skill_dir:
        cmd += ["--add-dir", skill_dir]

    # Skip interactive permission prompts in CI/test
    cmd += ["--dangerously-skip-permissions"]

    if extra_flags:
        cmd += extra_flags

    if verbose:
        print(f"  ▸ CMD: {' '.join(cmd)}", file=sys.stderr)

    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=300,  # 5-minute safety valve
        cwd=_find_project_root(),
        env=_clean_env(env_override),
    )

    if verbose and proc.stderr:
        for line in proc.stderr.strip().splitlines()[:5]:
            print(f"  ▸ STDERR: {line}", file=sys.stderr)

    # Parse newline-delimited JSON messages
    messages = []
    found_session_id = session_id
    for line in proc.stdout.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line)
            messages.append(msg)
            # Capture session_id from the first message that has one
            if not found_session_id:
                sid = _extract_session_id(msg)
                if sid:
                    found_session_id = sid
        except json.JSONDecodeError:
            if verbose:
                print(f"  ▸ (non-JSON line skipped)", file=sys.stderr)

    return found_session_id, messages


def _extract_session_id(msg: dict) -> Optional[str]:
    """Pull session_id from various message shapes."""
    if "session_id" in msg:
        return msg["session_id"]
    if isinstance(msg.get("message"), dict) and "session_id" in msg["message"]:
        return msg["message"]["session_id"]
    return None


# ── Message parsing ─────────────────────────────────────────────────────────

def parse_turn(messages: list[dict]) -> TurnResult:
    """Extract tool calls, text, and tool results from stream-json messages."""
    result = TurnResult(raw_messages=messages)

    for msg in messages:
        msg_type = msg.get("type", "")

        # Assistant messages contain content blocks
        if msg_type == "assistant":
            content = msg.get("message", {}).get("content", [])
            if isinstance(content, str):
                result.text_responses.append(content)
                continue
            if isinstance(content, list):
                for block in content:
                    if block.get("type") == "text":
                        result.text_responses.append(block.get("text", ""))
                    elif block.get("type") == "tool_use":
                        tc = _parse_tool_call(block)
                        if tc:
                            result.tool_calls.append(tc)
                    elif block.get("type") == "tool_result":
                        result.tool_results.append(block)

        # tool_result messages arrive as separate top-level events
        elif msg_type == "tool_result":
            result.tool_results.append(msg)

        # Result messages (final summary) may duplicate the last assistant
        # text block — only add if it's genuinely new content.
        elif msg_type == "result":
            r = msg.get("result")
            if r and r not in result.text_responses:
                result.text_responses.append(r)

    return result


def _parse_tool_call(block: dict) -> Optional[ToolCall]:
    """
    Convert a tool_use content block into a ToolCall.

    Reconstructs a command string for regex pattern matching.
    For Bash tool calls, the raw_command is the actual shell command.
    For MCP / other tools, it's "tool_name key=value ...".
    """
    name = block.get("name", "")
    inp = block.get("input", {})

    # Bash tool — the command IS the thing we want to match
    if name.lower() in ("bash", "bash_tool"):
        raw = inp.get("command", "")
    else:
        # For MCP and other tools, build "tool_name key=value ..." string
        parts = [name]
        for k, v in inp.items():
            if v is None:
                continue  # skip null params
            if isinstance(v, bool) and v:
                parts.append(f"--{k}")
            else:
                parts.append(f"{k}={v}")
        raw = " ".join(str(p) for p in parts)

    return ToolCall(tool_name=name, tool_input=inp, raw_command=raw)


# ── Assertion engine ────────────────────────────────────────────────────────

def check_tool_call_patterns(
    all_tool_calls: list[ToolCall], expected: dict
) -> list[dict]:
    """Check must_include / must_not_include regex patterns against tool calls."""
    results = []
    all_commands = [tc.raw_command for tc in all_tool_calls]
    commands_blob = "\n".join(all_commands)

    for item in expected.get("must_include", []):
        pattern = item if isinstance(item, str) else item.get("pattern", "")
        found = bool(re.search(pattern, commands_blob))
        results.append({
            "check": f"must_include: {pattern}",
            "passed": found,
            "detail": f"matched in {sum(1 for c in all_commands if re.search(pattern, c))} command(s)"
            if found else "no match found",
        })

    for item in expected.get("must_not_include", []):
        pattern = item if isinstance(item, str) else item.get("pattern", "")
        found = bool(re.search(pattern, commands_blob))
        results.append({
            "check": f"must_not_include: {pattern}",
            "passed": not found,
            "detail": "correctly absent"
            if not found else f"unexpectedly found in commands",
        })

    return results


def check_outcomes(
    all_tool_calls: list[ToolCall],
    all_tool_results: list[dict],
    all_text: list[str],
    expected: dict,
) -> list[dict]:
    """
    Check expected_outcomes using domain-specific logic per outcome key.

    Boolean outcomes use command-pattern matching rather than naive string
    search so that e.g. "escalated: false" isn't fooled by the word
    "escalate" appearing in agent prose.
    """
    results = []
    all_commands = [tc.raw_command for tc in all_tool_calls]
    commands_blob = "\n".join(all_commands)

    # Full transcript blob for string-search outcomes
    blob_parts = all_commands[:]
    blob_parts += [json.dumps(tr) for tr in all_tool_results]
    blob_parts += all_text
    blob = "\n".join(blob_parts)

    for key, expected_val in expected.items():
        if isinstance(expected_val, bool):
            found = _check_bool_outcome(key, commands_blob)
            passed = found == expected_val
            results.append({
                "check": f"outcome: {key} == {expected_val}",
                "passed": passed,
                "detail": f"evidence {'found' if found else 'not found'} in tool commands",
            })
        elif isinstance(expected_val, str):
            found = _check_string_outcome(key, expected_val, commands_blob, blob)
            results.append({
                "check": f"outcome: {key} contains '{expected_val}'",
                "passed": found,
                "detail": f"value {'found' if found else 'not found'} in transcript",
            })
        else:
            results.append({
                "check": f"outcome: {key}",
                "passed": False,
                "detail": f"unsupported outcome type: {type(expected_val).__name__}",
            })

    return results


def _check_bool_outcome(key: str, commands_blob: str) -> bool:
    """Domain-specific boolean outcome checks based on MCP tool call commands."""
    if key == "escalated":
        return bool(re.search(r"mcp__tickets__escalate_ticket", commands_blob))
    if key == "refund_processed":
        return bool(re.search(r"mcp__orders__refund", commands_blob))
    if key == "final_ticket_status":
        # This shouldn't be bool, but handle defensively
        return bool(
            re.search(r"mcp__tickets__(resolve_ticket|escalate_ticket)", commands_blob)
        )
    if key == "ticket_created":
        return bool(re.search(r"mcp__tickets__create_ticket", commands_blob))
    # Fallback: search commands for the key
    return key.replace("_", " ") in commands_blob.lower()


def _check_string_outcome(
    key: str, expected_val: str, commands_blob: str, blob: str
) -> bool:
    """Domain-specific string outcome checks."""
    if key == "final_ticket_status":
        if expected_val == "resolved":
            return bool(re.search(r"mcp__tickets__resolve_ticket", commands_blob))
        if expected_val == "escalated":
            return bool(re.search(r"mcp__tickets__escalate_ticket", commands_blob))
    if key == "ticket_reused":
        # The ticket ID must appear in commands AND no create was called
        ticket_in_cmds = expected_val in commands_blob
        no_create = not bool(re.search(r"mcp__tickets__create_ticket", commands_blob))
        return ticket_in_cmds and no_create
    # Default: simple string search across the full transcript
    return expected_val in blob


def run_quality_checks(
    checks: list[str],
    full_transcript: str,
    verbose: bool = False,
) -> list[dict]:
    """
    Use a separate `claude -p` call as an LLM judge to evaluate
    qualitative assertions against the conversation transcript.
    """
    if not checks:
        return []

    checks_text = "\n".join(f"  {i+1}. {c}" for i, c in enumerate(checks))

    judge_prompt = textwrap.dedent(f"""\
    You are a QA judge evaluating an AI customer-service agent's conversation.

    Below is the full transcript of the conversation (agent tool calls and
    text responses interleaved with customer messages).  After the transcript
    are quality checks to evaluate.

    For EACH check, respond with exactly one JSON object per line:
    {{"check_index": <1-based>, "passed": true/false, "reason": "<brief explanation>"}}

    Output ONLY those JSON lines, nothing else.

    ═══ TRANSCRIPT ═══
    {full_transcript}

    ═══ QUALITY CHECKS ═══
    {checks_text}
    """)

    proc = subprocess.run(
        [
            "claude", "-p", judge_prompt,
            "--output-format", "json",
            "--verbose",
            "--dangerously-skip-permissions",
            "--max-turns", "1",
        ],
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=120,
        env=_clean_env(),
    )

    # Parse the judge response — output may be a JSON object with a
    # "result" key, a JSON array, or plain text depending on format/version.
    judge_text = ""
    try:
        resp = json.loads(proc.stdout.strip())
        if isinstance(resp, dict):
            judge_text = resp.get("result", proc.stdout)
        elif isinstance(resp, list):
            # stream-json or array output — look for the result message
            for item in resp:
                if isinstance(item, dict) and item.get("type") == "result":
                    judge_text = item.get("result", "")
                    break
            if not judge_text:
                # Fallback: join all text content
                judge_text = proc.stdout
        else:
            judge_text = str(resp)
    except (json.JSONDecodeError, TypeError, AttributeError):
        judge_text = proc.stdout

    results = []
    for line in judge_text.strip().splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            obj = json.loads(line)
            if not isinstance(obj, dict):
                continue
            idx = obj.get("check_index", 0) - 1
            check_label = checks[idx] if 0 <= idx < len(checks) else f"check #{idx+1}"
            results.append({
                "check": f"quality: {check_label}",
                "passed": obj.get("passed", False),
                "detail": obj.get("reason", ""),
            })
        except (json.JSONDecodeError, IndexError):
            if verbose:
                print(f"  ▸ (judge line unparseable: {line[:80]})", file=sys.stderr)

    # Fill in any checks the judge missed
    evaluated_indices = {r.get("check", "").split(": ", 1)[-1] for r in results}
    for i, check in enumerate(checks):
        if check not in evaluated_indices and not any(
            str(i + 1) in r.get("check", "") for r in results
        ):
            results.append({
                "check": f"quality: {check}",
                "passed": False,
                "detail": "judge did not evaluate this check",
            })

    return results


# ── Main test executor ──────────────────────────────────────────────────────

def _reset_mcp_state(skill_dir: Optional[str] = None, verbose: bool = False, model: Optional[str] = None) -> None:
    """Reset both MCP servers to default state for test isolation.

    Sends a one-shot claude call to invoke the reset_state tools on both
    the orders and tickets MCP servers.
    """
    reset_prompt = (
        "Call both reset_state tools right now: "
        "mcp__orders__reset_state and mcp__tickets__reset_state. "
        "Do not say anything else."
    )
    cmd = [
        "claude", "-p", reset_prompt,
        "--output-format", "json",
        "--verbose",
        "--dangerously-skip-permissions",
        "--max-turns", "2",
    ]
    if model:
        cmd += ["--model", model]
    if skill_dir:
        cmd += ["--add-dir", skill_dir]

    if verbose:
        print("  ▸ Resetting MCP state…", file=sys.stderr)

    subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        stdin=subprocess.DEVNULL,
        timeout=60,
        cwd=_find_project_root(),
        env=_clean_env(),
    )


def run_test_plan(plan_path: str, skill_dir: Optional[str] = None,
                  verbose: bool = False, extra_flags: Optional[list] = None,
                  model: Optional[str] = None) -> TestResult:
    """Execute a single YAML test plan and return results."""
    with open(plan_path) as f:
        plan = yaml.safe_load(f)

    test_name = plan.get("name", Path(plan_path).stem)
    result = TestResult(name=test_name)
    start = time.time()

    # Reset MCP server state so each test starts clean
    _reset_mcp_state(skill_dir=skill_dir, verbose=verbose, model=model)

    print(f"\n{'━' * 60}")
    print(f"  TEST: {test_name}")
    print(f"  {plan.get('description', '').strip()[:100]}")
    print(f"{'━' * 60}")

    session_id = None
    all_tool_calls: list[ToolCall] = []
    all_tool_results: list[dict] = []
    all_text: list[str] = []
    transcript_parts: list[str] = []
    turn_count = 0

    for turn in plan.get("turns", []):
        role = turn.get("role", "")

        if role == "customer":
            message = turn.get("message", "").strip()
            turn_count += 1
            print(f"\n  ╭─ Customer (turn {turn_count})")
            print(f"  │ {message[:120]}{'…' if len(message) > 120 else ''}")

            transcript_parts.append(f"[CUSTOMER]: {message}")

            turn_start = time.time()
            sid, messages = call_claude(
                prompt=message,
                session_id=session_id,
                skill_dir=skill_dir,
                extra_flags=extra_flags,
                verbose=verbose,
                model=model,
            )
            turn_elapsed = time.time() - turn_start
            if sid:
                session_id = sid

            parsed = parse_turn(messages)
            all_tool_calls.extend(parsed.tool_calls)
            all_tool_results.extend(parsed.tool_results)
            all_text.extend(parsed.text_responses)

            # Log what the agent did
            for tc in parsed.tool_calls:
                transcript_parts.append(f"[TOOL CALL]: {tc.raw_command}")
                print(f"  │ ⚙ {tc.raw_command[:100]}")

            for tr in parsed.tool_results:
                transcript_parts.append(f"[TOOL RESULT]: {json.dumps(tr)[:200]}")

            for txt in parsed.text_responses:
                transcript_parts.append(f"[AGENT]: {txt}")
                preview = txt.replace("\n", " ")[:120]
                print(f"  │ 💬 {preview}{'…' if len(txt) > 120 else ''}")

            elapsed_total = time.time() - start
            print(f"  ╰─ turn: {turn_elapsed:.1f}s | total: {elapsed_total:.1f}s")

        elif role == "await_agent":
            # This is just a marker; the agent already responded in the
            # previous customer turn via `claude -p`.
            pass

        elif role == "system":
            # Inject system context (could extend to use --append-system-prompt)
            message = turn.get("message", "")
            transcript_parts.append(f"[SYSTEM]: {message}")

    result.total_turns = turn_count
    full_transcript = "\n".join(transcript_parts)

    # ── Run assertions ──────────────────────────────────────────────────

    print(f"\n  ┌─ Assertions")

    # 1. Tool call patterns
    if "expected_tool_calls" in plan:
        tc_results = check_tool_call_patterns(all_tool_calls, plan["expected_tool_calls"])
        result.tool_call_results = tc_results
        for r in tc_results:
            icon = "✅" if r["passed"] else "❌"
            print(f"  │ {icon} {r['check']}")
            if not r["passed"]:
                print(f"  │   → {r['detail']}")
                result.passed = False

    # 2. Expected outcomes
    if "expected_outcomes" in plan:
        oc_results = check_outcomes(
            all_tool_calls, all_tool_results, all_text, plan["expected_outcomes"]
        )
        result.outcome_results = oc_results
        for r in oc_results:
            icon = "✅" if r["passed"] else "❌"
            print(f"  │ {icon} {r['check']}")
            if not r["passed"]:
                print(f"  │   → {r['detail']}")
                result.passed = False

    # 3. LLM-as-judge quality checks
    if "quality_checks" in plan:
        print(f"  │")
        print(f"  │ 🤖 Running LLM judge…")
        judge_start = time.time()
        qc_results = run_quality_checks(
            plan["quality_checks"], full_transcript, verbose
        )
        judge_elapsed = time.time() - judge_start
        print(f"  │ (judge took {judge_elapsed:.1f}s)")
        result.quality_results = qc_results
        for r in qc_results:
            icon = "✅" if r["passed"] else "❌"
            print(f"  │ {icon} {r['check']}")
            if not r["passed"]:
                print(f"  │   → {r['detail']}")
                result.passed = False

    result.duration_s = time.time() - start
    status = "PASSED ✅" if result.passed else "FAILED ❌"
    print(f"  └─ {status}  ({result.duration_s:.1f}s)")

    return result


# ── CLI entry point ─────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="Run agent skill tests via the claude CLI"
    )
    parser.add_argument(
        "paths", nargs="+",
        help="YAML test file(s) or directories containing .yaml files",
    )
    parser.add_argument(
        "--skill-dir", "-d",
        help="Path to the skill's working directory (passed via --add-dir)",
    )
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument(
        "--extra-flags", nargs="*", default=[],
        help="Additional flags to pass to claude CLI",
    )
    parser.add_argument(
        "--model", "-m",
        default="sonnet",
        help="Claude model to use (default: sonnet). E.g., sonnet, haiku, opus, claude-sonnet-4-6",
    )
    parser.add_argument(
        "--json-report", "-j",
        help="Write a JSON report to this path",
    )
    args = parser.parse_args()

    # Collect all test files
    test_files = []
    for p in args.paths:
        path = Path(p)
        if path.is_dir():
            test_files.extend(sorted(path.glob("*.yaml")))
            test_files.extend(sorted(path.glob("*.yml")))
        elif path.is_file():
            test_files.append(path)
        else:
            print(f"Warning: {p} not found, skipping", file=sys.stderr)

    if not test_files:
        print("No test files found.", file=sys.stderr)
        sys.exit(1)

    print(f"\n🧪 Running {len(test_files)} test(s)…\n")

    results = []
    for tf in test_files:
        try:
            r = run_test_plan(
                str(tf),
                skill_dir=args.skill_dir,
                verbose=args.verbose,
                extra_flags=args.extra_flags or None,
                model=args.model,
            )
            results.append(r)
        except Exception as e:
            print(f"\n  ⚠️  Error running {tf.name}: {e}", file=sys.stderr)
            results.append(TestResult(name=tf.stem, passed=False, errors=[str(e)]))

    # ── Summary ─────────────────────────────────────────────────────────
    passed = sum(1 for r in results if r.passed)
    failed = len(results) - passed

    print(f"\n{'═' * 60}")
    print(f"  SUMMARY: {passed} passed, {failed} failed, {len(results)} total")
    print(f"{'═' * 60}")
    for r in results:
        icon = "✅" if r.passed else "❌"
        print(f"  {icon} {r.name} ({r.duration_s:.1f}s)")
    print()

    # Optional JSON report
    if args.json_report:
        report = {
            "total": len(results),
            "passed": passed,
            "failed": failed,
            "tests": [
                {
                    "name": r.name,
                    "passed": r.passed,
                    "duration_s": r.duration_s,
                    "tool_call_checks": r.tool_call_results,
                    "outcome_checks": r.outcome_results,
                    "quality_checks": r.quality_results,
                    "errors": r.errors,
                }
                for r in results
            ],
        }
        with open(args.json_report, "w") as f:
            json.dump(report, f, indent=2)
        print(f"  Report written to {args.json_report}")

    sys.exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()