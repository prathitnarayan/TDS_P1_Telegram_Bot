"""Tools the agent can call. The core one is run_python: a code-interpreter
that lets the LLM fetch public datasets (MOSPI etc.) and compute answers with
pandas/numpy. Everything the agent does here is captured in the run log."""
import subprocess
import sys
import textwrap

# Max seconds any single code block may run, and how much output we keep.
CODE_TIMEOUT = 90
MAX_OUTPUT_CHARS = 8000

TOOLS_SPEC = [
    {
        "type": "function",
        "function": {
            "name": "run_python",
            "description": (
                "Execute Python 3 code and return combined stdout+stderr. "
                "Available: pandas, numpy, requests, bs4 (BeautifulSoup), "
                "openpyxl, lxml. Network access IS allowed - fetch public "
                "datasets by URL (MOSPI, data.gov.in, CSV/XLSX links, etc.). "
                "Always print() the values you want to see; return values are "
                "not captured. Keep each call focused."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "code": {"type": "string", "description": "Python source to run."}
                },
                "required": ["code"],
            },
        },
    }
]


def run_python(code: str) -> str:
    """Run code in a fresh subprocess, return truncated stdout+stderr."""
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True,
            text=True,
            timeout=CODE_TIMEOUT,
        )
        out = proc.stdout + (("\n[stderr]\n" + proc.stderr) if proc.stderr else "")
        out = out.strip() or "(no output - remember to print() your results)"
    except subprocess.TimeoutExpired:
        out = f"(timed out after {CODE_TIMEOUT}s - make the code faster or fetch less)"
    except Exception as e:  # noqa: BLE001
        out = f"(runner error: {type(e).__name__}: {e})"
    if len(out) > MAX_OUTPUT_CHARS:
        out = out[:MAX_OUTPUT_CHARS] + f"\n...[truncated {len(out) - MAX_OUTPUT_CHARS} chars]"
    return out


def dispatch_tool(name: str, args: dict, log) -> str:
    """Route a tool call, logging input and output as JSONL run-log lines."""
    if name == "run_python":
        code = args.get("code", "")
        log("tool_call", tool="run_python", code=code)
        result = run_python(code)
        log("tool_result", tool="run_python", output=result)
        return result
    msg = f"(unknown tool: {name})"
    log("tool_error", tool=name, detail=msg)
    return msg
