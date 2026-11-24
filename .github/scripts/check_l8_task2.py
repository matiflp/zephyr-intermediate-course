#!/usr/bin/env python3
"""Check l8-task2: the seven error-path tests in tests/temp_alarm/src/test_error_paths.c.

Layers:
  1. Everything l8-task1 checks still holds (the fake, the five Task 1 tests).
  2. Static: the seven Task 2 stubs no longer call ztest_test_skip() and have
     zassert_*/zexpect_* checks; the before hook in test_error_paths.c is intact.
  3. Run (--twister-out): all 13 tests pass in both scenarios.
  4. Mutants (--mutants): every Task 1 and Task 2 requirement catches its broken
     copy of temp_alarm.c.

Exit code 0 when approved, 1 otherwise.
"""
import os
import sys

from homework_report import Report
from check_l8_task0 import ERRORS, PROVIDED, BASIC, common_args, case_checks, fake_checks, load_twister, test_lines
from check_l8_task1 import TASK1, mutant_checks, scaffold_checks, stub_checks

TASK2 = [
    (ERRORS, "temp_alarm_sensor_errors", "test_fetch_error_propagates"),
    (ERRORS, "temp_alarm_sensor_errors", "test_channel_get_error_propagates"),
    (ERRORS, "temp_alarm_sensor_errors", "test_fail_then_recover"),
    (ERRORS, "temp_alarm_invalid_input", "test_get_status_null"),
    (ERRORS, "temp_alarm_invalid_input", "test_init_without_device"),
    (ERRORS, "temp_alarm_invalid_input", "test_init_threshold_out_of_range"),
    (ERRORS, "temp_alarm_invalid_input", "test_set_threshold_out_of_range"),
]
ALL_TESTS = [PROVIDED[2]] + [n for _, _, n in TASK1 + TASK2]


def all_checks(report, root, args, upto=2):
    """The l8-task2 checks; l8-task3 adds coverage on top. Returns True when the tests ran."""
    if not (fake_checks(report, root) and stub_checks(report, root, TASK1 + TASK2)):
        return False
    scaffold_checks(report, root, BASIC)
    scaffold_checks(report, root, ERRORS)
    if not args.twister_out:
        report.note("Tests were not run (no --twister-out); only the source was checked.")
        return False
    twister = load_twister(report, args.twister_out, args.platform, root)
    if not twister:
        return False
    case_checks(report, twister, ALL_TESTS, test_lines(root))
    if args.mutants and not any(c["ok"] is False for c in report.checks):
        mutant_checks(report, root, args.mutants, upto)
    return True


def main():
    args = common_args(__doc__.splitlines()[0]).parse_args()
    report = Report("l8-task2", "temp_alarm error-path tests", where=ERRORS)
    all_checks(report, os.path.abspath(args.root), args)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
