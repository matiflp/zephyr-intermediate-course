#!/usr/bin/env python3
"""Run the student's temp_alarm tests against broken copies of temp_alarm.c (l8-task1..3).

Tests that still pass when the module has a known bug don't check what
TEST_SPEC.md asks. This script builds tests/temp_alarm once for native_sim, in
fixed order, then for each mutant (one small bug, see MUTANTS) it edits
app/modules/temp_alarm/src/temp_alarm.c, rebuilds (only that file recompiles),
runs zephyr.exe and records which tests failed. The original file is always
restored.

    python3 l8_mutants.py --root zephyr-course --task l8-task2 --json mutants.json
    python3 l8_mutants.py --list        # print the mutants and what catches them

Needs ZEPHYR_BASE, cmake and ninja, and ZEPHYR_TOOLCHAIN_VARIANT=host for
native_sim. The check scripts read mutants.json and turn the REQUIREMENTS whose
mutants survived into feedback.

mutants.json:
    {"task": "l8-task2", "error": null | "why the run is unusable",
     "baseline": {"ok": bool, "results": {"test_x": "PASS" | "FAIL" | "SKIP"}},
     "mutants": {"<id>": {"killed": bool, "failed": ["test_x"], "error": null | "..."}}}
"""
import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time

MODULE_SRC = "app/modules/temp_alarm/src/temp_alarm.c"
TEST_DIR = "tests/temp_alarm"
TASK_NUM = {"l8-task1": 1, "l8-task2": 2, "l8-task3": 3}

# Pieces of the course's temp_alarm.c the mutants edit. Each must be in the file.
RANGE_CHECK = "if (threshold < TEMP_ALARM_THRESHOLD_MIN || threshold > TEMP_ALARM_THRESHOLD_MAX) {"
FETCH = "ret = sensor_sample_fetch_chan(ctx_sensor_dev, SENSOR_CHAN_AMBIENT_TEMP);"
FETCH_FAIL = "\t\tta_data.last_error = ret;\n\t\tLOG_ERR(\"Sensor fetch failed: %d\", ret);\n\t\treturn ret;"
GET_FAIL = "\t\tta_data.last_error = ret;\n\t\tLOG_ERR(\"Sensor channel get failed: %d\", ret);\n\t\treturn ret;"
CHECK_START = "\tstruct sensor_value val;\n\tint ret;\n\n"
SET_BODY = "\tta_data.threshold   = threshold;\n\tta_data.is_alarming = false;\n"
RESET_BODY = ("\tta_data.is_alarming = false;\n\tta_data.alarm_count = 0U;\n\tta_data.last_error  = 0;\n\n"
              "\tLOG_INF(\"Alarm state reset\");")

# id -> list of (old, new, occurrence). occurrence: which match to replace (1-based), 0 for all.
MUTANTS = {
    # Task 1: happy paths
    "alarm_at_threshold": [("if (temp > ta_data.threshold) {", "if (temp >= ta_data.threshold) {", 1)],
    "wrong_channel": [("SENSOR_CHAN_AMBIENT_TEMP", "SENSOR_CHAN_DIE_TEMP", 0)],
    "double_fetch": [(FETCH, "(void)sensor_sample_fetch_chan(ctx_sensor_dev, SENSOR_CHAN_AMBIENT_TEMP);\n\t" + FETCH, 1)],
    "last_temp_not_saved": [("\tta_data.last_temp = temp;\n", "", 1)],
    "alarm_not_raised": [("\t\tta_data.is_alarming = true;\n", "\t\tta_data.is_alarming = false;\n", 1)],
    "alarm_not_counted": [("\t\tta_data.alarm_count++;\n", "", 1)],
    "alarm_count_stuck_at_1": [("\t\tta_data.alarm_count++;\n", "\t\tta_data.alarm_count = 1U;\n", 1)],
    "threshold_keeps_alarm": [(SET_BODY, "\tta_data.threshold   = threshold;\n", 1)],
    "reset_keeps_alarm": [(RESET_BODY, RESET_BODY.replace("\tta_data.is_alarming = false;\n", "", 1), 1)],
    "reset_keeps_count": [(RESET_BODY, RESET_BODY.replace("\tta_data.alarm_count = 0U;\n", "", 1), 1)],
    "reset_clears_last_temp": [(RESET_BODY, RESET_BODY.replace("\n\n\tLOG_INF", "\n\tta_data.last_temp   = 0;\n\n\tLOG_INF", 1), 1)],
    # Task 2: error paths
    "fetch_error_not_saved": [(FETCH_FAIL, FETCH_FAIL.replace("\t\tta_data.last_error = ret;\n", "", 1), 1)],
    "fetch_error_reads_channel": [(FETCH_FAIL, FETCH_FAIL.replace(
        "\t\treturn ret;", "\t\t(void)sensor_channel_get(ctx_sensor_dev, SENSOR_CHAN_AMBIENT_TEMP, &val);\n\t\treturn ret;", 1), 1)],
    "get_error_not_saved": [(GET_FAIL, GET_FAIL.replace("\t\tta_data.last_error = ret;\n", "", 1), 1)],
    "get_error_fetches_again": [(GET_FAIL, GET_FAIL.replace(
        "\t\treturn ret;", "\t\t(void)sensor_sample_fetch_chan(ctx_sensor_dev, SENSOR_CHAN_AMBIENT_TEMP);\n\t\treturn ret;", 1), 1)],
    "success_clears_error": [("\tta_data.last_temp = temp;\n", "\tta_data.last_temp = temp;\n\tta_data.last_error = 0;\n", 1)],
    "no_recovery": [(CHECK_START, CHECK_START + "\tif (ta_data.last_error != 0) {\n\t\treturn ta_data.last_error;\n\t}\n\n", 1)],
    "null_status_wrong_code": [("\tif (status == NULL) {\n\t\treturn -EINVAL;", "\tif (status == NULL) {\n\t\treturn -EFAULT;", 1)],
    "no_device_wrong_code": [("\t\tLOG_ERR(\"Sensor device is not ready\");\n\t\treturn -ENODEV;",
                              "\t\tLOG_ERR(\"Sensor device is not ready\");\n\t\treturn -EINVAL;", 1)],
    "init_accepts_low": [(RANGE_CHECK, "if (threshold > TEMP_ALARM_THRESHOLD_MAX) {", 1)],
    "init_accepts_high": [(RANGE_CHECK, "if (threshold < TEMP_ALARM_THRESHOLD_MIN) {", 1)],
    "set_accepts_low": [(RANGE_CHECK, "if (threshold > TEMP_ALARM_THRESHOLD_MAX) {", 2)],
    "set_accepts_high": [(RANGE_CHECK, "if (threshold < TEMP_ALARM_THRESHOLD_MIN) {", 2)],
    "rejected_high_stored": [(RANGE_CHECK, "if (threshold > TEMP_ALARM_THRESHOLD_MAX) {\n\t\tta_data.threshold = threshold;\n\t}\n\t"
                              + RANGE_CHECK, 2)],
    "rejected_low_stored": [(RANGE_CHECK, "if (threshold < TEMP_ALARM_THRESHOLD_MIN) {\n\t\tta_data.threshold = threshold;\n\t}\n\t"
                             + RANGE_CHECK, 2)],
}

# What the student's tests must catch. A requirement passes when at least one of its mutants
# makes a test fail. "test" is the stub where the missing check belongs (for the file/line).
REQUIREMENTS = [
    # ---- Task 1 (src/test_basic.c)
    {"task": 1, "mutants": ["wrong_channel"], "test": "test_below_threshold",
     "label": "`test_below_threshold` checks the sensor channel",
     "fix": "Your tests still pass when the module reads the wrong sensor channel. In `test_below_threshold`, "
            "check that the channel was `SENSOR_CHAN_AMBIENT_TEMP`, e.g. `fake_sensor_sample_fetch_fake.arg1_val` "
            "(TEST_SPEC.md, `temp_alarm_check` #1)."},
    {"task": 1, "mutants": ["double_fetch"], "test": "test_below_threshold",
     "label": "`test_below_threshold` checks the sample is fetched once",
     "fix": "Your tests still pass when the module fetches the sample twice. In `test_below_threshold`, check "
            "`fake_sensor_sample_fetch_fake.call_count == 1` (TEST_SPEC.md, `temp_alarm_check` #1)."},
    {"task": 1, "mutants": ["last_temp_not_saved"], "test": "test_below_threshold",
     "label": "the tests check `last_temp` after a reading",
     "fix": "Your tests still pass when the module never stores the reading. Check `status.last_temp` after "
            "`check()`: 20 in `test_below_threshold`, 40 in `test_above_threshold` "
            "(TEST_SPEC.md, `temp_alarm_check` #1 and #3)."},
    {"task": 1, "mutants": ["alarm_at_threshold"], "test": "test_at_threshold",
     "label": "`test_at_threshold` catches an alarm at the threshold",
     "fix": "Your tests still pass when a reading equal to the threshold raises the alarm. In `test_at_threshold`, "
            "check `status.is_alarming == false` and `status.alarm_count == 0` (TEST_SPEC.md, `temp_alarm_check` #2)."},
    {"task": 1, "mutants": ["alarm_not_raised"], "test": "test_above_threshold",
     "label": "`test_above_threshold` checks the alarm is raised",
     "fix": "Your tests still pass when the module never raises the alarm. In `test_above_threshold`, check "
            "`status.is_alarming == true` (TEST_SPEC.md, `temp_alarm_check` #3)."},
    {"task": 1, "mutants": ["alarm_not_counted"], "test": "test_above_threshold",
     "label": "`test_above_threshold` checks the alarm is counted",
     "fix": "Your tests still pass when the module doesn't count alarms. In `test_above_threshold`, check "
            "`status.alarm_count == 1` (TEST_SPEC.md, `temp_alarm_check` #3)."},
    {"task": 1, "mutants": ["threshold_keeps_alarm"], "test": "test_new_threshold_clears_alarm",
     "label": "`test_new_threshold_clears_alarm` checks the alarm right after `set_threshold(50)`",
     "fix": "Your tests still pass when `temp_alarm_set_threshold()` doesn't clear the alarm. In "
            "`test_new_threshold_clears_alarm`, check `status.is_alarming == false` right after `set_threshold(50)`, "
            "before the second `check()` (TEST_SPEC.md, `temp_alarm_threshold` #1)."},
    {"task": 1, "mutants": ["alarm_count_stuck_at_1"], "test": "test_reset_clears_state",
     "label": "`test_reset_clears_state` checks `alarm_count == 3` before the reset",
     "fix": "Your tests still pass when `alarm_count` never goes above 1. In `test_reset_clears_state`, check "
            "`status.alarm_count == 3` after the three checks, before the reset (TEST_SPEC.md, `temp_alarm_reset` #1)."},
    {"task": 1, "mutants": ["reset_keeps_alarm", "reset_keeps_count"], "test": "test_reset_clears_state",
     "label": "`test_reset_clears_state` checks the alarm is cleared",
     "fix": "Your tests still pass when `temp_alarm_reset()` leaves the alarm on. In `test_reset_clears_state`, "
            "check `status.is_alarming == false` and `status.alarm_count == 0` after the reset "
            "(TEST_SPEC.md, `temp_alarm_reset` #1)."},
    {"task": 1, "mutants": ["reset_clears_last_temp"], "test": "test_reset_clears_state",
     "label": "`test_reset_clears_state` checks the reset keeps `last_temp`",
     "fix": "Your tests still pass when `temp_alarm_reset()` clears the last reading. In `test_reset_clears_state`, "
            "check `status.last_temp == 40` after the reset (TEST_SPEC.md, `temp_alarm_reset` #1)."},
    # ---- Task 2 (src/test_error_paths.c)
    {"task": 2, "mutants": ["fetch_error_not_saved"], "test": "test_fetch_error_propagates",
     "label": "`test_fetch_error_propagates` checks `last_error`",
     "fix": "Your tests still pass when a failed fetch isn't stored. In `test_fetch_error_propagates`, check "
            "`status.last_error == -EIO` (TEST_SPEC.md, `temp_alarm_sensor_errors` #1)."},
    {"task": 2, "mutants": ["fetch_error_reads_channel"], "test": "test_fetch_error_propagates",
     "label": "`test_fetch_error_propagates` checks `channel_get` is never called",
     "fix": "Your tests still pass when the module reads the channel after a failed fetch. In "
            "`test_fetch_error_propagates`, check `fake_sensor_channel_get_fake.call_count == 0` "
            "(TEST_SPEC.md, `temp_alarm_sensor_errors` #1)."},
    {"task": 2, "mutants": ["get_error_not_saved"], "test": "test_channel_get_error_propagates",
     "label": "`test_channel_get_error_propagates` checks `last_error`",
     "fix": "Your tests still pass when a failed `channel_get` isn't stored. In `test_channel_get_error_propagates`, "
            "check `status.last_error == -EIO` (TEST_SPEC.md, `temp_alarm_sensor_errors` #2)."},
    {"task": 2, "mutants": ["get_error_fetches_again"], "test": "test_channel_get_error_propagates",
     "label": "`test_channel_get_error_propagates` checks the sample is fetched once",
     "fix": "Your tests still pass when the module fetches again after a failed `channel_get`. In "
            "`test_channel_get_error_propagates`, check `fake_sensor_sample_fetch_fake.call_count == 1` "
            "(TEST_SPEC.md, `temp_alarm_sensor_errors` #2)."},
    {"task": 2, "mutants": ["no_recovery"], "test": "test_fail_then_recover",
     "label": "`test_fail_then_recover` checks the second `check()` succeeds",
     "fix": "Your tests still pass when the module never recovers after an error. In `test_fail_then_recover`, "
            "check that the second `check()` returns `0` (TEST_SPEC.md, `temp_alarm_sensor_errors` #3)."},
    {"task": 2, "mutants": ["success_clears_error"], "test": "test_fail_then_recover",
     "label": "`test_fail_then_recover` checks `last_error` is kept",
     "fix": "Your tests still pass when a successful check clears `last_error`. In `test_fail_then_recover`, check "
            "`status.last_error == -EIO` after the second check (TEST_SPEC.md, `temp_alarm_sensor_errors` #3)."},
    {"task": 2, "mutants": ["null_status_wrong_code"], "test": "test_get_status_null",
     "label": "`test_get_status_null` checks the exact error code",
     "fix": "Your tests still pass when `temp_alarm_get_status(NULL)` returns the wrong error. In "
            "`test_get_status_null`, check it returns exactly `-EINVAL` (TEST_SPEC.md, `temp_alarm_invalid_input` #1)."},
    {"task": 2, "mutants": ["no_device_wrong_code"], "test": "test_init_without_device",
     "label": "`test_init_without_device` checks the exact error code",
     "fix": "Your tests still pass when `temp_alarm_init(NULL, 30)` returns the wrong error. In "
            "`test_init_without_device`, check it returns exactly `-ENODEV` (TEST_SPEC.md, `temp_alarm_invalid_input` #2)."},
    {"task": 2, "mutants": ["init_accepts_low"], "test": "test_init_threshold_out_of_range",
     "label": "`test_init_threshold_out_of_range` checks -1 is rejected",
     "fix": "Your tests still pass when `temp_alarm_init()` accepts a threshold of -1. In "
            "`test_init_threshold_out_of_range`, check `init(&fake_sensor_dev, -1)` returns `-EINVAL` "
            "(TEST_SPEC.md, `temp_alarm_invalid_input` #3)."},
    {"task": 2, "mutants": ["init_accepts_high"], "test": "test_init_threshold_out_of_range",
     "label": "`test_init_threshold_out_of_range` checks 101 is rejected",
     "fix": "Your tests still pass when `temp_alarm_init()` accepts a threshold of 101. In "
            "`test_init_threshold_out_of_range`, check `init(&fake_sensor_dev, 101)` returns `-EINVAL` "
            "(TEST_SPEC.md, `temp_alarm_invalid_input` #3)."},
    {"task": 2, "mutants": ["set_accepts_low"], "test": "test_set_threshold_out_of_range",
     "label": "`test_set_threshold_out_of_range` checks -1 is rejected",
     "fix": "Your tests still pass when `temp_alarm_set_threshold()` accepts -1. In `test_set_threshold_out_of_range`, "
            "check `set_threshold(-1)` returns `-EINVAL` (TEST_SPEC.md, `temp_alarm_invalid_input` #4)."},
    {"task": 2, "mutants": ["set_accepts_high"], "test": "test_set_threshold_out_of_range",
     "label": "`test_set_threshold_out_of_range` checks 101 is rejected",
     "fix": "Your tests still pass when `temp_alarm_set_threshold()` accepts 101. In `test_set_threshold_out_of_range`, "
            "check `set_threshold(101)` returns `-EINVAL` (TEST_SPEC.md, `temp_alarm_invalid_input` #4)."},
    # A test can show "30 is still the threshold" with a reading of 31 (alarm) or of 30 (no alarm);
    # each catches one of these two mutants, so either is enough.
    {"task": 2, "mutants": ["rejected_high_stored", "rejected_low_stored"], "test": "test_set_threshold_out_of_range",
     "label": "`test_set_threshold_out_of_range` checks the threshold is still 30",
     "fix": "Your tests still pass when a rejected `set_threshold()` value replaces the threshold. In "
            "`test_set_threshold_out_of_range`, show that 30 C is still in force after the rejected calls, e.g. a "
            "reading of 31 raises the alarm (TEST_SPEC.md, `temp_alarm_invalid_input` #4)."},
]

RESULT_RE = re.compile(r"^\s*(PASS|FAIL|SKIP) - (test_\w+)", re.M)


def mutate(src, edits):
    """Apply one mutant's edits to the file text. Raises ValueError when the file doesn't match."""
    for old, new, occurrence in edits:
        count = src.count(old)
        if occurrence == 0:
            if not count:
                raise ValueError(f"`{old.strip()[:60]}` not found")
            src = src.replace(old, new)
            continue
        if count < occurrence:
            raise ValueError(f"`{old.strip()[:60]}` found {count} time(s), need occurrence {occurrence}")
        start = -1
        for _ in range(occurrence):
            start = src.index(old, start + 1)
        src = src[:start] + new + src[start + len(old):]
    return src


def run(cmd, timeout, cwd=None):
    try:
        p = subprocess.run(cmd, cwd=cwd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                           text=True, errors="replace", timeout=timeout)
        return p.returncode, p.stdout
    except subprocess.TimeoutExpired as e:
        out = e.stdout if isinstance(e.stdout, str) else (e.stdout or b"").decode(errors="replace")
        return None, out


def run_tests(build_dir):
    """Rebuild and run the test binary. Returns (results {test: PASS/FAIL/SKIP}, crashed, error)."""
    rc, out = run(["ninja", "-C", build_dir], timeout=600)
    if rc != 0:
        return {}, False, "build failed: " + out[-800:]
    exe = os.path.join(build_dir, "zephyr", "zephyr.exe")
    rc, out = run([exe], timeout=60)
    results = {}
    for status, name in RESULT_RE.findall(out):
        if results.get(name) != "FAIL":  # a test that fails in one place fails
            results[name] = status
    # rc None: timeout; ztest on native_sim exits non-zero when a test fails, or on a crash.
    crashed = rc is None or (rc != 0 and "FAIL" not in results.values())
    return results, crashed, None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--task", choices=sorted(TASK_NUM), default="l8-task3")
    ap.add_argument("--build-dir", default="mutants-build")
    ap.add_argument("--platform", default="native_sim")
    ap.add_argument("--json", help="write the results here")
    ap.add_argument("--list", action="store_true", help="print the mutants and requirements, run nothing")
    args = ap.parse_args()

    upto = TASK_NUM[args.task]
    reqs = [r for r in REQUIREMENTS if r["task"] <= upto]
    ids = [m for r in reqs for m in r["mutants"]]
    if args.list:
        for r in reqs:
            print(f"task{r['task']}  {', '.join(r['mutants']):45} {r['label']}")
        return 0

    root = os.path.abspath(args.root)
    src_path = os.path.join(root, MODULE_SRC)
    out = {"task": args.task, "error": None, "baseline": None, "mutants": {}}

    def finish(code=0):
        if args.json:
            with open(args.json, "w") as f:
                json.dump(out, f, indent=2)
                f.write("\n")
        return code

    try:
        original = open(src_path, encoding="utf-8").read()
    except OSError as e:
        out["error"] = f"can't read {MODULE_SRC}: {e}"
        return finish(1)

    shutil.rmtree(args.build_dir, ignore_errors=True)
    rc, log = run(["cmake", "-GNinja", "-S", os.path.join(root, TEST_DIR), "-B", args.build_dir,
                   f"-DBOARD={args.platform}", "-DCONFIG_ZTEST_SHUFFLE=n",
                   "-DCONFIG_COMPILER_WARNINGS_AS_ERRORS=n"], timeout=600)
    if rc != 0:
        out["error"] = "the tests don't configure: " + log[-800:]
        return finish(1)

    results, crashed, error = run_tests(args.build_dir)
    ok = not error and not crashed and results and all(s != "FAIL" for s in results.values())
    out["baseline"] = {"ok": bool(ok), "results": results, "error": error or ("crashed" if crashed else None)}
    if not ok:
        print("The tests don't all pass on the course's temp_alarm.c; mutants skipped.")
        return finish(0)

    try:
        for mid in ids:
            entry = {"killed": False, "failed": [], "error": None}
            try:
                mutated = mutate(original, MUTANTS[mid])
            except ValueError as e:
                entry["error"] = f"mutant doesn't apply to {MODULE_SRC}: {e}"
                out["mutants"][mid] = entry
                continue
            with open(src_path, "w", encoding="utf-8") as f:
                f.write(mutated)
            os.utime(src_path, (time.time(), time.time()))
            res, crashed, error = run_tests(args.build_dir)
            if error:
                entry["error"] = error
            else:
                entry["failed"] = sorted(n for n, s in res.items() if s == "FAIL")
                entry["killed"] = bool(entry["failed"]) or crashed
            out["mutants"][mid] = entry
            print(f"{mid:28} {'caught' if entry['killed'] else ('ERROR' if entry['error'] else 'SURVIVED')}"
                  f"  {' '.join(entry['failed'])}")
    finally:
        with open(src_path, "w", encoding="utf-8") as f:
            f.write(original)
        os.utime(src_path, (time.time(), time.time()))
    return finish(0)


if __name__ == "__main__":
    sys.exit(main())
