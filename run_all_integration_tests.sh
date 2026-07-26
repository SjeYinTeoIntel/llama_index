#!/bin/bash
# Run pytest --collect-only for all integration test packages
# and summarize results in an output file.

source ~/sy/llama_index/.venv/bin/activate

INTEGRATIONS_DIR="/home/dut3806/sy/llama_index/llama-index-integrations"
OUTPUT_FILE="/home/dut3806/sy/llama_index/test_collect_results.txt"

echo "============================================" > "$OUTPUT_FILE"
echo "  Integration Tests Collection Report" >> "$OUTPUT_FILE"
echo "  Date: $(date)" >> "$OUTPUT_FILE"
echo "  Python: $(python3 --version)" >> "$OUTPUT_FILE"
echo "  Pytest: $(pytest --version 2>&1 | head -1)" >> "$OUTPUT_FILE"
echo "============================================" >> "$OUTPUT_FILE"
echo "" >> "$OUTPUT_FILE"

total_packages=0
success_packages=0
failed_packages=0
total_tests_collected=0

for dir in "$INTEGRATIONS_DIR"/*/*/; do
    if [ -d "$dir/tests" ]; then
        total_packages=$((total_packages + 1))
        pkg_name=$(echo "$dir" | sed "s|$INTEGRATIONS_DIR/||" | sed 's|/$||')

        # Run pytest --collect-only
        result=$(cd "$dir" && pytest --collect-only -q 2>&1)
        exit_code=$?

        if [ $exit_code -eq 0 ]; then
            # Extract number of tests collected
            collected=$(echo "$result" | grep -oP '\d+(?= tests? collected)')
            if [ -z "$collected" ]; then
                collected=0
            fi
            total_tests_collected=$((total_tests_collected + collected))
            success_packages=$((success_packages + 1))
            echo "[PASS] $pkg_name => $collected tests collected" >> "$OUTPUT_FILE"
        else
            failed_packages=$((failed_packages + 1))
            # Get the error summary (last 3 lines)
            error_summary=$(echo "$result" | tail -3 | tr '\n' ' ')
            echo "[FAIL] $pkg_name => $error_summary" >> "$OUTPUT_FILE"
        fi
    fi
done

echo "" >> "$OUTPUT_FILE"
echo "============================================" >> "$OUTPUT_FILE"
echo "  SUMMARY" >> "$OUTPUT_FILE"
echo "============================================" >> "$OUTPUT_FILE"
echo "Total packages with tests: $total_packages" >> "$OUTPUT_FILE"
echo "Packages collected OK:     $success_packages" >> "$OUTPUT_FILE"
echo "Packages with errors:      $failed_packages" >> "$OUTPUT_FILE"
echo "Total tests collected:     $total_tests_collected" >> "$OUTPUT_FILE"
echo "============================================" >> "$OUTPUT_FILE"

echo ""
echo "Done! Results saved to: $OUTPUT_FILE"
echo "Total packages: $total_packages | OK: $success_packages | Failed: $failed_packages | Tests: $total_tests_collected"
