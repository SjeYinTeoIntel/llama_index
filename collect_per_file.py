#!/usr/bin/env python3
"""
Track pytest collection AND execution status per individual test file.

Usage:
    python3 collect_per_file.py [TARGET_DIR] [OUTPUT_FILE] [--run]

    TARGET_DIR: path to scan for test files (default: llama-index-integrations)
    OUTPUT_FILE: path for TSV output (default: test_per_file_results.tsv)
    --run: actually run the tests (not just collect). Adds pass/fail/skip/xfail counts.

Examples:
    # Collect only (fast, checks if tests are importable)
    python3 collect_per_file.py
    python3 collect_per_file.py /home/dut3806/sy/llama_index/llama-index-core core_per_file_results.tsv

    # Actually run tests (slower, gets real pass/fail/skip/xfail)
    python3 collect_per_file.py --run
    python3 collect_per_file.py /home/dut3806/sy/llama_index/llama-index-core core_per_file_results.tsv --run

"""

import sys
import subprocess
import re
from pathlib import Path

DEFAULT_DIR = Path("/home/dut3806/sy/llama_index/llama-index-integrations")
DEFAULT_OUTPUT = Path("/home/dut3806/sy/llama_index/test_per_file_results.tsv")


def find_package_root(test_file: Path, base_dir: Path) -> Path:
    """Find the package root (directory with pyproject.toml)."""
    d = test_file.parent
    while d != base_dir and d != d.parent:
        if (d / "pyproject.toml").exists():
            return d
        d = d.parent
    if (base_dir / "pyproject.toml").exists():
        return base_dir
    return test_file.parent


def parse_run_result(output: str) -> dict:
    """Parse pytest run output for pass/fail/skip/xfail/error counts."""
    counts = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "xfailed": 0,
        "xpassed": 0,
        "errors": 0,
    }
    # Match the summary line like "5 passed, 2 skipped, 1 xfailed in 1.23s"
    for key in counts:
        m = re.search(rf"(\d+) {key}", output)
        if m:
            counts[key] = int(m.group(1))
    # Also check for "error" (singular)
    m = re.search(r"(\d+) errors?", output)
    if m:
        counts["errors"] = int(m.group(1))
    return counts


def parse_test_details(output: str, rel_file: str) -> list:
    """Parse verbose pytest output to get individual test results with reasons."""
    details = []
    lines = output.splitlines()

    for line in lines:
        # Match verbose pytest lines like:
        # tests/test_foo.py::test_bar PASSED [ 10%]
        # tests/test_foo.py::test_bar[param1] FAILED [ 20%]
        # tests/test_foo.py::test_bar SKIPPED [ 30%]
        # tests/test_foo.py::test_bar SKIPPED (reason) [ 30%]
        m = re.match(
            r"^(.*?::\S+)\s+(PASSED|FAILED|SKIPPED|XFAIL|XPASS|ERROR)\s*(.*?)(?:\[[\s\d]+%\])?$",
            line,
        )
        if m:
            test_id = m.group(1).strip()
            result = m.group(2).strip()
            reason_part = m.group(3).strip()
            reason = ""
            if reason_part:
                reason = re.sub(r"^[\s\-]+", "", reason_part)
                reason = reason.strip("() ")
            details.append(
                {
                    "test_id": test_id,
                    "result": result,
                    "reason": reason,
                }
            )

    # Capture FAILED lines from short test summary: "FAILED tests/... - AssertionError: ..."
    failure_reasons = {}
    for line in lines:
        m = re.match(r"FAILED\s+(\S+)\s*-\s*(.*)", line)
        if m:
            failure_reasons[m.group(1)] = m.group(2).strip()

    # Capture SKIPPED lines from short test summary: "SKIPPED [1] tests/...:20: reason"
    skip_reasons = {}
    for line in lines:
        m = re.match(r"SKIPPED\s+\[\d+\]\s+(\S+?):(\d+):\s*(.*)", line)
        if m:
            skip_file = m.group(1)
            skip_line = m.group(2)
            skip_reason = m.group(3).strip()
            skip_reasons[f"{skip_file}:{skip_line}"] = skip_reason
            # Also store by file only (for fallback matching)
            if skip_file not in skip_reasons:
                skip_reasons[skip_file] = skip_reason

    # Merge failure reasons into details
    for d in details:
        if d["result"] == "FAILED" and not d["reason"]:
            for key, reason in failure_reasons.items():
                if key in d["test_id"] or d["test_id"].endswith(key):
                    d["reason"] = reason
                    break

    # Merge skip reasons into details
    for d in details:
        if d["result"] == "SKIPPED" and not d["reason"]:
            # Try matching by file path (any skip reason from same file)
            for key, reason in skip_reasons.items():
                if key.split(":")[0] in d["test_id"]:
                    d["reason"] = reason
                    break

    return details


def main():
    # Parse arguments
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    run_mode = "--run" in sys.argv

    target_dir = Path(args[0]) if len(args) > 0 else DEFAULT_DIR
    output_file = Path(args[1]) if len(args) > 1 else DEFAULT_OUTPUT

    if not output_file.is_absolute():
        output_file = Path("/home/dut3806/sy/llama_index") / output_file

    mode_label = "RUN" if run_mode else "COLLECT-ONLY"
    print(f"Mode: {mode_label}")
    print(f"Target: {target_dir}")
    print(f"Output: {output_file}")

    test_files = sorted(target_dir.rglob("test_*.py"))
    test_files = [f for f in test_files if ".venv" not in str(f)]

    print(f"Found {len(test_files)} test files")

    results = []
    all_details = []  # Detailed per-test results for failed/skipped/xfailed
    pass_count = 0
    fail_count = 0
    total_passed = 0
    total_failed = 0
    total_skipped = 0
    total_xfailed = 0

    # TSV header
    if run_mode:
        results.append("Status\tFile\tPassed\tFailed\tSkipped\tXfailed\tErrors\tDetail")
    else:
        results.append("Status\tFile\tDetail")

    for i, tf in enumerate(test_files):
        if (i + 1) % 50 == 0:
            if run_mode:
                print(
                    f"  Processing {i + 1}/{len(test_files)}... (collectible={pass_count}, uncollectible={fail_count}, tests_passed={total_passed}, tests_failed={total_failed})"
                )
            else:
                print(
                    f"  Processing {i + 1}/{len(test_files)}... (pass={pass_count}, fail={fail_count})"
                )

        pkg_root = find_package_root(tf, target_dir)
        rel_test = str(tf.relative_to(pkg_root))
        rel_display = str(tf.relative_to(target_dir))

        if run_mode:
            # Actually run the tests with verbose output to capture details
            try:
                proc = subprocess.run(
                    [
                        "python3",
                        "-m",
                        "pytest",
                        "-v",
                        "--tb=line",
                        "-rsfE",
                        "--no-header",
                        rel_test,
                    ],
                    cwd=str(pkg_root),
                    capture_output=True,
                    text=True,
                    timeout=120,
                )
                output = proc.stdout + proc.stderr
                counts = parse_run_result(output)

                if proc.returncode == 0:
                    status = "PASS"
                    pass_count += 1
                elif proc.returncode == 5:
                    status = "ERROR"
                    fail_count += 1
                    m = re.search(
                        r"ModuleNotFoundError: No module named '([^']+)'", output
                    )
                    if m:
                        counts["detail"] = f"Missing module: {m.group(1)}"
                    else:
                        counts["detail"] = "Collection error"
                else:
                    if counts["failed"] > 0 or counts["errors"] > 0:
                        status = "FAIL"
                    else:
                        status = "PASS"
                    pass_count += 1 if status == "PASS" else 0
                    fail_count += 1 if status == "FAIL" else 0

                total_passed += counts["passed"]
                total_failed += counts["failed"]
                total_skipped += counts["skipped"]
                total_xfailed += counts["xfailed"]

                # Parse individual test details for non-passed tests
                test_details = parse_test_details(output, rel_display)
                for td in test_details:
                    if td["result"] != "PASSED":
                        all_details.append(
                            {
                                "file": rel_display,
                                "test_id": td["test_id"],
                                "result": td["result"],
                                "reason": td["reason"],
                            }
                        )

                detail = counts.get("detail", "")
                results.append(
                    f"{status}\t{rel_display}\t{counts['passed']}\t{counts['failed']}\t{counts['skipped']}\t{counts['xfailed']}\t{counts['errors']}\t{detail}"
                )

            except subprocess.TimeoutExpired:
                results.append(
                    f"TIMEOUT\t{rel_display}\t0\t0\t0\t0\t0\tTimeout (>120s)"
                )
                fail_count += 1
            except Exception as e:
                results.append(f"ERROR\t{rel_display}\t0\t0\t0\t0\t0\t{e!s}")
                fail_count += 1
        else:
            # Collect only mode (original behavior)
            try:
                proc = subprocess.run(
                    ["python3", "-m", "pytest", "--collect-only", "-q", rel_test],
                    cwd=str(pkg_root),
                    capture_output=True,
                    text=True,
                    timeout=30,
                )

                if proc.returncode == 0:
                    m = re.search(r"(\d+) tests? collected", proc.stdout + proc.stderr)
                    count = m.group(1) if m else "0"
                    results.append(f"PASS\t{rel_display}\t{count} tests")
                    pass_count += 1
                else:
                    output = proc.stdout + proc.stderr
                    m = re.search(
                        r"ModuleNotFoundError: No module named '([^']+)'", output
                    )
                    if m:
                        reason = f"Missing module: {m.group(1)}"
                    else:
                        m = re.search(
                            r"(ImportError|ValueError|NameError|AttributeError|TypeError): (.+)",
                            output,
                        )
                        if m:
                            reason = f"{m.group(1)}: {m.group(2)}"
                        else:
                            reason = "Collection error"
                    results.append(f"FAIL\t{rel_display}\t{reason}")
                    fail_count += 1
            except subprocess.TimeoutExpired:
                results.append(f"FAIL\t{rel_display}\tTimeout (>30s)")
                fail_count += 1
            except Exception as e:
                results.append(f"FAIL\t{rel_display}\tError: {e!s}")
                fail_count += 1

    # Write results
    output_file.write_text("\n".join(results) + "\n")

    # Write detailed test results (failed/skipped/xfailed) if in run mode
    if run_mode and all_details:
        detail_file = output_file.with_name(output_file.stem + "_details.tsv")
        detail_lines = ["Result\tFile\tTest ID\tReason"]
        for d in all_details:
            detail_lines.append(
                f"{d['result']}\t{d['file']}\t{d['test_id']}\t{d['reason']}"
            )
        detail_file.write_text("\n".join(detail_lines) + "\n")
        print(f"  Details file: {detail_file} ({len(all_details)} entries)")

    print(f"\nDone!")
    print(f"  Total files: {len(test_files)}")
    print(f"  Files collectible/passed: {pass_count}")
    print(f"  Files failed/uncollectible: {fail_count}")
    if run_mode:
        print(f"  ---")
        print(f"  Total tests passed: {total_passed}")
        print(f"  Total tests failed: {total_failed}")
        print(f"  Total tests skipped: {total_skipped}")
        print(f"  Total tests xfailed: {total_xfailed}")
    print(f"  Output: {output_file}")


if __name__ == "__main__":
    main()
