#!/usr/bin/env python3
"""Check l8-task1: the five happy-path tests in tests/temp_alarm/src/test_basic.c.

Layers:
  1. The l8-task0 fake is in place (the tests need it).
  2. Static: the five Task 1 stubs no longer call ztest_test_skip() and have
     zassert_*/zexpect_* checks; test_status_after_init and the before hook are intact.
  3. Run (--twister-out): the six tests in test_basic.c pass in both scenarios.
  4. Mutants (--mutants, from l8_mutants.py): every Task 1 requirement catches its
     broken copy of temp_alarm.c, so each TEST_SPEC.md "Verify" item is checked.

Exit code 0 when approved, 1 otherwise.
"""
import json
import os
import re
import sys

from homework_report import Report
from check_l8_task0 import (BASIC, PROVIDED, ASSERT_RE, SKIP_RE, common_args, case_checks, fake_checks,
                            find_tests, function_body, load_twister, read, strip_comments, test_lines)
from l8_mutants import REQUIREMENTS

TASK1 = [
    (BASIC, "temp_alarm_check", "test_below_threshold"),
    (BASIC, "temp_alarm_check", "test_at_threshold"),
    (BASIC, "temp_alarm_check", "test_above_threshold"),
    (BASIC, "temp_alarm_threshold", "test_new_threshold_clears_alarm"),
    (BASIC, "temp_alarm_reset", "test_reset_clears_state"),
]
BEFORE_CALLS = ("sensor_fake_reset", "sensor_fake_init", "temp_alarm_init")


def stub_checks(report, root, stubs, done_label=""):
    """Each stub exists, doesn't skip and asserts something."""
    ok = True
    by_file = {}
    for rel, suite, name in stubs:
        if rel not in by_file:
            src = read(root, rel)
            by_file[rel] = find_tests(src) if src is not None else None
        tests = by_file[rel]
        if tests is None:
            report.check(False, f"`{rel}` exists at the repository root", path=rel,
                         fix=f"Implement the tests in `{rel}` at the repository root, not in a copy of the project.")
            return False
        if (suite, name) not in tests:
            ok &= report.check(False, f"`{name}` exists", path=rel,
                               fix=f"Keep the stub `ZTEST({suite}, {name})` and implement it as `TEST_SPEC.md` describes.")
            continue
        body, line = tests[(suite, name)]
        if not report.check(not SKIP_RE.search(body), f"`{name}` is implemented (no `ztest_test_skip()`){done_label}",
                            fix=f"Replace `ztest_test_skip()` in `{name}` with the test body from `TEST_SPEC.md`.",
                            path=rel, line=line):
            ok = False
            continue
        ok &= report.check(ASSERT_RE.search(body), f"`{name}` checks results with `zassert_*`",
                           fix=f"Add `zassert_*` checks to `{name}` for each Verify item in `TEST_SPEC.md`.",
                           path=rel, line=line)
    return bool(ok)


def scaffold_checks(report, root, rel):
    """The provided example and the before hook are intact in `rel`."""
    src = read(root, rel)
    if src is None:
        return
    code = strip_comments(src)
    body, line = function_body(code, "before")
    missing = [c for c in BEFORE_CALLS if body is None or not re.search(r"\b%s\s*\(" % c, body)]
    report.check(not missing, f"the `before` hook in `{os.path.basename(rel)}` is intact", path=rel, line=line,
                 fix=f"Restore the `before` hook in `{rel}`: it must call `sensor_fake_reset()`, `sensor_fake_init()` "
                     "and `temp_alarm_init(&fake_sensor_dev, 30)` before every test.")
    if rel == BASIC:
        tests = find_tests(src)
        found = tests.get((PROVIDED[1], PROVIDED[2]))
        asserts = len(ASSERT_RE.findall(found[0])) if found else 0
        report.check(asserts >= 4, "the provided `test_status_after_init` is intact", path=rel,
                     line=found[1] if found else None,
                     fix="Restore the provided `test_status_after_init` with its four checks; it is part of the suite.")


def mutant_checks(report, root, mutants_json, upto):
    """Every requirement up to task `upto` catches at least one of its mutants."""
    try:
        with open(mutants_json) as f:
            data = json.load(f)
    except (OSError, ValueError, TypeError):
        report.fail_hard("CI couldn't run the tests against the broken copies of `temp_alarm.c` (no mutants.json).")
        return
    if data.get("error"):
        report.fail_hard(f"CI couldn't run the tests against the broken copies of `temp_alarm.c`: {data['error'][:300]}")
        return
    if not (data.get("baseline") or {}).get("ok"):
        report.note("The tests against broken copies of `temp_alarm.c` run once all your tests pass.")
        return
    lines = test_lines(root)
    mutants = data.get("mutants") or {}
    for req in [r for r in REQUIREMENTS if r["task"] <= upto]:
        entries = [mutants.get(m) for m in req["mutants"]]
        if any(e is None or e.get("error") for e in entries):
            why = next((e.get("error") for e in entries if e and e.get("error")), "not run")
            report.fail_hard(f"CI couldn't run a broken copy of `temp_alarm.c` ({', '.join(req['mutants'])}): {why[:300]}")
            return
        path, line = lines.get(req["test"], (None, None))
        report.check(any(e["killed"] for e in entries), req["label"], fix=req["fix"], path=path, line=line)


def main():
    args = common_args(__doc__.splitlines()[0]).parse_args()
    root = os.path.abspath(args.root)
    report = Report("l8-task1", "temp_alarm happy-path tests", where=BASIC)
    if fake_checks(report, root) and stub_checks(report, root, TASK1):
        scaffold_checks(report, root, BASIC)
        if args.twister_out:
            twister = load_twister(report, args.twister_out, args.platform, root)
            if twister:
                case_checks(report, twister, [PROVIDED[2]] + [n for _, _, n in TASK1], test_lines(root))
                if args.mutants and not any(c["ok"] is False for c in report.checks):
                    mutant_checks(report, root, args.mutants, 1)
        else:
            report.note("Tests were not run (no --twister-out); only the source was checked.")
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
