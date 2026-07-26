#!/usr/bin/env python3
"""
Generate Excel report of all test files in a given directory.

Usage:
    python3 generate_test_excel.py [TARGET_DIR] [PER_FILE_TSV] [OUTPUT_XLSX]

    TARGET_DIR: path to scan for test files (default: llama-index-integrations)
    PER_FILE_TSV: per-file results TSV (default: test_per_file_results.tsv)
    OUTPUT_XLSX: output Excel file (default: integration_test_report.xlsx)

Examples:
    python3 generate_test_excel.py
    python3 generate_test_excel.py /home/dut3806/sy/llama_index/llama-index-core core_per_file_results.tsv core_test_report.xlsx

"""

import sys
import re
from pathlib import Path
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill, Alignment, Border, Side

BASE_DIR = Path("/home/dut3806/sy/llama_index")
INTEGRATIONS_DIR = (
    Path(sys.argv[1]) if len(sys.argv) > 1 else BASE_DIR / "llama-index-integrations"
)
PER_FILE_TSV = (
    Path(sys.argv[2]) if len(sys.argv) > 2 else BASE_DIR / "test_per_file_results.tsv"
)
OUTPUT_FILE = str(
    Path(sys.argv[3])
    if len(sys.argv) > 3
    else BASE_DIR / "integration_test_report.xlsx"
)

# Make relative paths absolute
if not PER_FILE_TSV.is_absolute():
    PER_FILE_TSV = BASE_DIR / PER_FILE_TSV
if not Path(OUTPUT_FILE).is_absolute():
    OUTPUT_FILE = str(BASE_DIR / OUTPUT_FILE)


def analyze_test_file(filepath: Path) -> list[dict]:
    """Analyze a single test file and return rows of data."""
    try:
        content = filepath.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return []

    rel_path = str(filepath.relative_to(INTEGRATIONS_DIR))

    # Extract category and package name
    parts = rel_path.split("/")
    category = parts[0] if len(parts) > 0 else ""
    package = parts[1] if len(parts) > 1 else ""

    # Find test functions
    test_funcs = re.findall(r"def (test_\w+)", content)
    if not test_funcs:
        test_funcs = ["(no test functions)"]

    # Find imports / class paths
    imports = re.findall(r"from ([\w.]+) import ([\w, *]+)", content)
    class_paths = []
    for module, names in imports:
        for name in names.split(","):
            name = name.strip()
            if name and name != "*":
                class_paths.append(f"{module}.{name}")

    # Check for MRO / isinstance class checks
    has_mro = bool(
        re.search(r"__mro__|names_of_base_classes|isinstance\(.*Base", content)
    )

    # Check for mocking
    has_mock = bool(
        re.search(
            r"from unittest\.mock import|from unittest import mock|"
            r"@patch|MagicMock|Mock\(|mocker\.|monkeypatch\.|@mock",
            content,
        )
    )

    # Device detection
    has_cuda = bool(re.search(r"\bcuda\b", content, re.IGNORECASE))
    has_xpu = bool(re.search(r"\bxpu\b", content, re.IGNORECASE))
    has_cpu = bool(re.search(r"device.*cpu|cpu.*device", content, re.IGNORECASE))
    has_torch_device = bool(
        re.search(r"torch\.device|\.to\(device|accelerator", content)
    )

    # Determine device column
    devices = []
    if has_cuda:
        devices.append("CUDA/GPU")
    if has_xpu:
        devices.append("XPU")
    if has_cpu:
        devices.append("CPU")
    if has_torch_device and not devices:
        devices.append("torch.device (generic)")
    if not devices:
        devices.append("CPU (no device ref)")

    # Hardware dependency
    hw_deps = []
    if has_cuda:
        hw_deps.append("NVIDIA GPU (CUDA)")
    if has_xpu:
        hw_deps.append("Intel GPU (XPU)")
    if has_torch_device:
        hw_deps.append("PyTorch device")
    if re.search(r"vllm|Vllm", content):
        hw_deps.append("vLLM (GPU inference)")
    if re.search(r"boto3|sagemaker|bedrock", content, re.IGNORECASE):
        hw_deps.append("AWS services")
    if re.search(r"os\.environ|os\.getenv|API_KEY|api_key.*=.*os\.", content):
        hw_deps.append("API key / env var")

    # Can run on XPU?
    if has_mock and not has_cuda and not has_torch_device:
        xpu_possible = "Yes (mocked, no HW dep)"
    elif has_mro and len(test_funcs) <= 2:
        xpu_possible = "Yes (class path check only)"
    elif has_cuda:
        xpu_possible = "Needs adaptation (CUDA->XPU)"
    elif has_xpu:
        xpu_possible = "Yes (already XPU)"
    elif has_torch_device:
        xpu_possible = "Maybe (needs device config)"
    elif "API key / env var" in hw_deps:
        xpu_possible = "N/A (cloud/API service)"
    else:
        xpu_possible = "N/A (no local compute)"

    # Test type classification
    if has_mro and len(test_funcs) <= 2 and not has_mock:
        test_type = "Class path check"
    elif has_mock:
        test_type = "Unit test (mocked)"
    elif "API key / env var" in hw_deps:
        test_type = "Integration test (API)"
    elif has_cuda or has_xpu or has_torch_device:
        test_type = "Integration test (HW)"
    else:
        test_type = "Unit test"

    # Determine collection status
    # Read from our results file
    collection_status = "Unknown"

    rows = []
    for func in test_funcs:
        rows.append(
            {
                "Category": category,
                "Package": package,
                "Test File": rel_path,
                "Test Function": func,
                "Class/Import Paths": "; ".join(class_paths[:5])
                + ("..." if len(class_paths) > 5 else ""),
                "Test Type": test_type,
                "Device": ", ".join(devices),
                "Has CUDA": "Yes" if has_cuda else "No",
                "Has XPU": "Yes" if has_xpu else "No",
                "Has Mock": "Yes" if has_mock else "No",
                "Can Run on XPU?": xpu_possible,
                "Hardware Dependencies": "; ".join(hw_deps) if hw_deps else "None",
            }
        )

    return rows


def load_collection_status() -> tuple[dict, dict, dict]:
    """Load per-file collection/run status, failure reasons, and run counts."""
    status = {}
    reasons = {}
    run_counts = {}  # filepath -> {passed, failed, skipped, xfailed, errors}

    per_file = PER_FILE_TSV
    if per_file.exists():
        lines = per_file.read_text().splitlines()
        # Detect if it's a run-mode TSV (has header with Passed/Failed columns)
        is_run_mode = len(lines) > 0 and "Passed" in lines[0]
        start_idx = 1 if is_run_mode else 0

        for line in lines[start_idx:]:
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            st, filepath = parts[0], parts[1]

            if is_run_mode and len(parts) >= 7:
                # Run mode: Status, File, Passed, Failed, Skipped, Xfailed, Errors, Detail
                try:
                    counts = {
                        "passed": int(parts[2]) if parts[2] else 0,
                        "failed": int(parts[3]) if parts[3] else 0,
                        "skipped": int(parts[4]) if parts[4] else 0,
                        "xfailed": int(parts[5]) if parts[5] else 0,
                        "errors": int(parts[6]) if parts[6] else 0,
                    }
                except (ValueError, IndexError):
                    counts = {
                        "passed": 0,
                        "failed": 0,
                        "skipped": 0,
                        "xfailed": 0,
                        "errors": 0,
                    }
                run_counts[filepath] = counts
                detail = parts[7] if len(parts) > 7 else ""
                status[filepath] = st
                reasons[filepath] = detail if st in ("FAIL", "ERROR", "TIMEOUT") else ""
            else:
                # Collect-only mode: Status, File, Detail
                reason = parts[2] if len(parts) > 2 else ""
                status[filepath] = st
                reasons[filepath] = reason if st == "FAIL" else ""

    return status, reasons, run_counts


def main():
    print("Scanning test files...")
    test_files = sorted(INTEGRATIONS_DIR.rglob("test_*.py"))
    test_files = [f for f in test_files if ".venv" not in str(f)]

    print(f"Found {len(test_files)} test files")

    # Load per-file collection status
    file_status, file_reasons, file_run_counts = load_collection_status()
    has_run_data = len(file_run_counts) > 0

    all_rows = []
    for i, filepath in enumerate(test_files):
        if (i + 1) % 100 == 0:
            print(f"  Processing {i + 1}/{len(test_files)}...")
        rows = analyze_test_file(filepath)

        # Add per-file collection status and failure reason
        rel = str(filepath.relative_to(INTEGRATIONS_DIR))
        coll_status = file_status.get(rel, "Unknown")
        fail_reason = file_reasons.get(rel, "")
        counts = file_run_counts.get(rel, {})

        for row in rows:
            row["Collection Status"] = coll_status
            row["Failure Reason"] = (
                fail_reason if coll_status in ("FAIL", "ERROR", "TIMEOUT") else ""
            )
            if has_run_data:
                row["Tests Passed"] = counts.get("passed", "")
                row["Tests Failed"] = counts.get("failed", "")
                row["Tests Skipped"] = counts.get("skipped", "")
                row["Tests Xfailed"] = counts.get("xfailed", "")
                row["Tests Errors"] = counts.get("errors", "")
        all_rows.extend(rows)

    print(f"Total rows: {len(all_rows)}")

    # Create Excel
    wb = Workbook()
    ws = wb.active
    ws.title = "Integration Tests"

    # Headers
    headers = [
        "Category",
        "Package",
        "Test File",
        "Test Function",
        "Class/Import Paths",
        "Test Type",
        "Device",
        "Has CUDA",
        "Has XPU",
        "Has Mock",
        "Can Run on XPU?",
        "Hardware Dependencies",
        "Collection Status",
        "Failure Reason",
    ]
    if has_run_data:
        headers.extend(
            [
                "Tests Passed",
                "Tests Failed",
                "Tests Skipped",
                "Tests Xfailed",
                "Tests Errors",
            ]
        )

    # Style
    header_font = Font(bold=True, color="FFFFFF", size=11)
    header_fill = PatternFill(
        start_color="4472C4", end_color="4472C4", fill_type="solid"
    )
    thin_border = Border(
        left=Side(style="thin"),
        right=Side(style="thin"),
        top=Side(style="thin"),
        bottom=Side(style="thin"),
    )

    # Write headers
    for col, header in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col, value=header)
        cell.font = header_font
        cell.fill = header_fill
        cell.alignment = Alignment(horizontal="center", wrap_text=True)
        cell.border = thin_border

    # Color fills
    pass_fill = PatternFill(start_color="C6EFCE", end_color="C6EFCE", fill_type="solid")
    fail_fill = PatternFill(start_color="FFC7CE", end_color="FFC7CE", fill_type="solid")
    warn_fill = PatternFill(start_color="FFEB9C", end_color="FFEB9C", fill_type="solid")
    xpu_yes_fill = PatternFill(
        start_color="C6EFCE", end_color="C6EFCE", fill_type="solid"
    )
    xpu_no_fill = PatternFill(
        start_color="FFC7CE", end_color="FFC7CE", fill_type="solid"
    )
    xpu_maybe_fill = PatternFill(
        start_color="FFEB9C", end_color="FFEB9C", fill_type="solid"
    )

    # Write data
    for row_idx, row_data in enumerate(all_rows, 2):
        for col_idx, header in enumerate(headers, 1):
            cell = ws.cell(row=row_idx, column=col_idx, value=row_data.get(header, ""))
            cell.border = thin_border
            cell.alignment = Alignment(wrap_text=True, vertical="top")

            # Color coding
            if header == "Collection Status":
                if row_data.get(header) == "PASS":
                    cell.fill = pass_fill
                elif row_data.get(header) == "FAIL":
                    cell.fill = fail_fill

            if header == "Has CUDA" and row_data.get(header) == "Yes":
                cell.fill = warn_fill
            if header == "Has XPU" and row_data.get(header) == "Yes":
                cell.fill = PatternFill(
                    start_color="BDD7EE", end_color="BDD7EE", fill_type="solid"
                )

            if header == "Can Run on XPU?":
                val = row_data.get(header, "")
                if val.startswith("Yes"):
                    cell.fill = xpu_yes_fill
                elif val.startswith(("Needs", "Maybe")):
                    cell.fill = xpu_maybe_fill
                elif val.startswith("N/A"):
                    cell.fill = PatternFill(
                        start_color="D9D9D9", end_color="D9D9D9", fill_type="solid"
                    )

            # Color code run results
            if header == "Tests Failed":
                val = row_data.get(header, "")
                if val and int(val) > 0:
                    cell.fill = fail_fill
            if header == "Tests Passed":
                val = row_data.get(header, "")
                if val and int(val) > 0:
                    cell.fill = pass_fill

    # Auto-width columns
    col_widths = {
        "Category": 15,
        "Package": 40,
        "Test File": 55,
        "Test Function": 35,
        "Class/Import Paths": 60,
        "Test Type": 22,
        "Device": 20,
        "Has CUDA": 10,
        "Has XPU": 10,
        "Has Mock": 10,
        "Can Run on XPU?": 35,
        "Hardware Dependencies": 35,
        "Collection Status": 18,
        "Failure Reason": 120,
        "Tests Passed": 12,
        "Tests Failed": 12,
        "Tests Skipped": 12,
        "Tests Xfailed": 12,
        "Tests Errors": 12,
    }
    for col_idx, header in enumerate(headers, 1):
        ws.column_dimensions[
            ws.cell(row=1, column=col_idx).column_letter
        ].width = col_widths.get(header, 20)

    # Freeze top row
    ws.freeze_panes = "A2"

    # Auto-filter
    ws.auto_filter.ref = f"A1:{chr(64 + len(headers))}{len(all_rows) + 1}"

    # === Summary sheet ===
    ws2 = wb.create_sheet("Summary")
    ws2.cell(row=1, column=1, value="Integration Test Report Summary").font = Font(
        bold=True, size=14
    )
    ws2.cell(row=3, column=1, value="Metric").font = Font(bold=True)
    ws2.cell(row=3, column=2, value="Count").font = Font(bold=True)

    total_files = len(test_files)
    total_funcs = len(all_rows)
    pass_files = sum(1 for v in file_status.values() if v == "PASS")
    fail_files = sum(1 for v in file_status.values() if v == "FAIL")
    cuda_files = len({r["Test File"] for r in all_rows if r["Has CUDA"] == "Yes"})
    xpu_files = len({r["Test File"] for r in all_rows if r["Has XPU"] == "Yes"})
    mock_files = len({r["Test File"] for r in all_rows if r["Has Mock"] == "Yes"})
    xpu_yes = sum(1 for r in all_rows if r["Can Run on XPU?"].startswith("Yes"))
    xpu_maybe = sum(
        1
        for r in all_rows
        if r["Can Run on XPU?"].startswith("Maybe")
        or r["Can Run on XPU?"].startswith("Needs")
    )
    xpu_na = sum(1 for r in all_rows if r["Can Run on XPU?"].startswith("N/A"))

    # Count unique packages
    all_packages = set()
    pass_packages = set()
    fail_packages = set()
    for r in all_rows:
        pkg = r["Package"]
        all_packages.add(pkg)
        if r["Collection Status"] == "PASS":
            pass_packages.add(pkg)
        elif r["Collection Status"] == "FAIL":
            fail_packages.add(pkg)

    summary_data = [
        ("", "--- Package Level ---"),
        ("Total packages with tests", len(all_packages)),
        ("Packages collected OK", len(pass_packages)),
        ("Packages with errors", len(fail_packages)),
        ("", ""),
        ("", "--- File Level ---"),
        ("Total test files", total_files),
        ("Test files PASS (collectible)", pass_files),
        ("Test files FAIL (missing deps)", fail_files),
        ("", ""),
        ("", "--- Function Level ---"),
        ("Total test functions (def test_*)", total_funcs),
        ("Functions runnable on XPU", xpu_yes),
        ("Functions needing adaptation for XPU", xpu_maybe),
        ("Functions N/A for XPU (cloud/API or no local compute)", xpu_na),
        ("", ""),
        ("", "--- Device / Hardware ---"),
        ("Files with CUDA refs", cuda_files),
        ("Files with XPU refs", xpu_files),
        ("Files with mocking", mock_files),
    ]

    # Add run results if available
    if has_run_data:
        total_run_passed = sum(c.get("passed", 0) for c in file_run_counts.values())
        total_run_failed = sum(c.get("failed", 0) for c in file_run_counts.values())
        total_run_skipped = sum(c.get("skipped", 0) for c in file_run_counts.values())
        total_run_xfailed = sum(c.get("xfailed", 0) for c in file_run_counts.values())
        total_run_errors = sum(c.get("errors", 0) for c in file_run_counts.values())
        summary_data.extend(
            [
                ("", ""),
                ("", "--- Actual Test Run Results ---"),
                ("Tests passed", total_run_passed),
                ("Tests failed", total_run_failed),
                ("Tests skipped", total_run_skipped),
                ("Tests xfailed (expected failures)", total_run_xfailed),
                ("Tests errors", total_run_errors),
                (
                    "Total tests executed",
                    total_run_passed
                    + total_run_failed
                    + total_run_skipped
                    + total_run_xfailed,
                ),
            ]
        )

    for i, (metric, count) in enumerate(summary_data, 4):
        ws2.cell(row=i, column=1, value=metric)
        ws2.cell(row=i, column=2, value=count)

    ws2.column_dimensions["A"].width = 40
    ws2.column_dimensions["B"].width = 15

    # === Detail sheets for Failed / Skipped / Xfailed tests ===
    detail_file = PER_FILE_TSV.with_name(PER_FILE_TSV.stem + "_details.tsv")
    if detail_file.exists():
        detail_lines = detail_file.read_text().splitlines()
        if len(detail_lines) > 1:  # Has header + data
            # Parse details
            failed_tests = []
            skipped_tests = []
            xfailed_tests = []
            error_tests = []

            for line in detail_lines[1:]:  # Skip header
                parts = line.split("\t", 3)
                if len(parts) < 3:
                    continue
                result = parts[0]
                file_path = parts[1]
                test_id = parts[2]
                reason = parts[3] if len(parts) > 3 else ""

                entry = {"File": file_path, "Test": test_id, "Reason": reason}
                if result == "FAILED":
                    failed_tests.append(entry)
                elif result == "SKIPPED":
                    skipped_tests.append(entry)
                elif result in ("XFAIL", "XFAILED"):
                    xfailed_tests.append(entry)
                elif result == "ERROR":
                    error_tests.append(entry)

            def _write_detail_sheet(wb, sheet_name, entries, fill_color):
                ws_d = wb.create_sheet(sheet_name)
                d_headers = ["File", "Test", "Reason"]
                d_fill = PatternFill(
                    start_color=fill_color, end_color=fill_color, fill_type="solid"
                )
                for col, h in enumerate(d_headers, 1):
                    cell = ws_d.cell(row=1, column=col, value=h)
                    cell.font = header_font
                    cell.fill = header_fill
                    cell.alignment = Alignment(horizontal="center", wrap_text=True)
                    cell.border = thin_border
                for row_idx, entry in enumerate(entries, 2):
                    for col_idx, h in enumerate(d_headers, 1):
                        raw = entry.get(h, "") or ""
                        # openpyxl rejects control chars (XML illegal chars); strip them
                        safe = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(raw))
                        cell = ws_d.cell(row=row_idx, column=col_idx, value=safe)
                        cell.border = thin_border
                        cell.alignment = Alignment(wrap_text=True, vertical="top")
                ws_d.column_dimensions["A"].width = 55
                ws_d.column_dimensions["B"].width = 60
                ws_d.column_dimensions["C"].width = 150
                # Auto row height based on Reason text length
                for row_idx, entry in enumerate(entries, 2):
                    reason = str(entry.get("Reason", "") or "")
                    lines = max(1, len(reason) // 140 + reason.count("\n") + 1)
                    ws_d.row_dimensions[row_idx].height = min(lines * 15, 400)
                ws_d.freeze_panes = "A2"
                ws_d.auto_filter.ref = f"A1:C{len(entries) + 1}"
                # Add count in title
                ws_d.cell(row=1, column=4, value=f"Total: {len(entries)}")
                return ws_d

            if failed_tests:
                _write_detail_sheet(
                    wb, f"Failed Tests ({len(failed_tests)})", failed_tests, "FFC7CE"
                )
            if skipped_tests:
                _write_detail_sheet(
                    wb, f"Skipped Tests ({len(skipped_tests)})", skipped_tests, "FFEB9C"
                )
            if xfailed_tests:
                _write_detail_sheet(
                    wb, f"Xfailed Tests ({len(xfailed_tests)})", xfailed_tests, "BDD7EE"
                )
            if error_tests:
                _write_detail_sheet(
                    wb, f"Error Tests ({len(error_tests)})", error_tests, "FFC7CE"
                )

            print(
                f"  - Detail sheets: {len(failed_tests)} failed, {len(skipped_tests)} skipped, {len(xfailed_tests)} xfailed, {len(error_tests)} errors"
            )

    wb.save(OUTPUT_FILE)
    print(f"\nExcel saved to: {OUTPUT_FILE}")
    print(f"  - {total_files} test files")
    print(f"  - {total_funcs} test function rows")
    print(f"  - CUDA files: {cuda_files}, XPU files: {xpu_files}")


if __name__ == "__main__":
    main()
