#!/usr/bin/env python3
"""Check l8-task0: the sample_fetch fake in tests/temp_alarm.

Two layers:
  1. Static: include/sensor_fake.h declares a value fake returning int with
     (const struct device *, enum sensor_channel); src/sensor_fake.c defines it,
     points fake_sensor_api.sample_fetch at it, and resets it in sensor_fake_reset().
  2. Run (with --twister-out): twister built and ran tests/temp_alarm on native_sim
     and the provided test_status_after_init passed.

    west twister -T tests/temp_alarm -p native_sim --outdir twister-out
    python3 .github/scripts/check_l8_task0.py --twister-out twister-out

This module also holds the helpers the later L8 checkers share.
Exit code 0 when approved, 1 otherwise.
"""
import argparse
import json
import os
import re
import sys

from homework_report import Report
import homework_app as app

TEST_ROOT = "tests/temp_alarm"
FAKE_H = f"{TEST_ROOT}/include/sensor_fake.h"
FAKE_C = f"{TEST_ROOT}/src/sensor_fake.c"
BASIC = f"{TEST_ROOT}/src/test_basic.c"
ERRORS = f"{TEST_ROOT}/src/test_error_paths.c"
PROVIDED = (BASIC, "temp_alarm_get_status", "test_status_after_init")
TWISTER_CMD = "west twister -T tests/temp_alarm -p native_sim"

FAKE_SIG = r"\(\s*int\s*,\s*(\w+)\s*,\s*const\s+struct\s+device\s*\*\s*\w*\s*,\s*enum\s+sensor_channel\s*\w*\s*\)"
DECLARE_RE = re.compile(r"\bDECLARE_FAKE_VALUE_FUNC\s*" + FAKE_SIG)
DEFINE_RE = re.compile(r"\bDEFINE_FAKE_VALUE_FUNC\s*" + FAKE_SIG)
SAMPLE_FETCH_RE = re.compile(r"\.sample_fetch\s*=\s*&?\s*(\w+)")
ZTEST_RE = re.compile(r"\bZTEST(?:_F|_USER|_USER_F)?\s*\(\s*(\w+)\s*,\s*(\w+)\s*\)\s*\{")
ASSERT_RE = re.compile(r"\b(zassert|zexpect)_\w+\s*\(")
SKIP_RE = re.compile(r"\bztest_test_skip\s*\(")
ASSERT_FAIL_RE = re.compile(r"Assertion failed at \S*?(test_basic|test_error_paths)\.c:(\d+): (\w+):")


# ---------------------------------------------------------------- shared helpers

def strip_comments(src):
    """Blank out comments and string contents, keeping offsets (and so line numbers) intact."""
    def blank(m):
        return re.sub(r"[^\n]", " ", m.group(0))
    return re.sub(r'//[^\n]*|/\*.*?\*/|"(?:\\.|[^"\\])*"', blank, src, flags=re.S)


def read(root, rel):
    path = os.path.join(root, rel)
    if not os.path.isfile(path):
        return None
    with open(path, encoding="utf-8", errors="replace") as f:
        return f.read()


def line_of(code, pos):
    return code.count("\n", 0, pos) + 1


def body_after(code, start):
    """The text between the `{` at or after `start` and its matching `}`."""
    i = code.find("{", start)
    if i < 0:
        return ""
    depth, j = 1, i + 1
    while j < len(code) and depth:
        depth += {"{": 1, "}": -1}.get(code[j], 0)
        j += 1
    return code[i + 1:j - 1]


def function_body(code, name):
    """(body, line) of a C function definition `... name(...) {`, or (None, None)."""
    m = re.search(r"\b%s\s*\([^;{)]*\)\s*\{" % re.escape(name), code)
    if not m:
        return None, None
    return body_after(code, m.end() - 1), line_of(code, m.start())


def find_tests(src):
    """Map (suite, name) -> (body, line) for every ZTEST in the file."""
    code = strip_comments(src)
    tests = {}
    for m in ZTEST_RE.finditer(code):
        tests[(m.group(1), m.group(2))] = (body_after(code, m.end() - 1), line_of(code, m.start()))
    return tests


def test_lines(root):
    """{test name: (path, line)} for every ZTEST in both test files."""
    out = {}
    for rel in (BASIC, ERRORS):
        src = read(root, rel)
        if src:
            for (_, name), (_, line) in find_tests(src).items():
                out[name] = (rel, line)
    return out


# ---------------------------------------------------------------- task 0

def fake_checks(report, root):
    """The sample_fetch fake. Returns True when every part is there."""
    header, source = read(root, FAKE_H), read(root, FAKE_C)
    if header is None or source is None:
        missing = FAKE_H if header is None else FAKE_C
        report.check(False, f"`{missing}` exists at the repository root", path=missing,
                     fix=f"Keep `{missing}` at the repository root, as in the starter; the checks don't look elsewhere.")
        return False
    h, c = strip_comments(header), strip_comments(source)

    m = DECLARE_RE.search(h)
    name = m.group(1) if m else None
    if not report.check(m, "the `sample_fetch` fake is declared", path=FAKE_H,
                        fix="Declare the fake in `sensor_fake.h`: `DECLARE_FAKE_VALUE_FUNC(int, fake_sensor_sample_fetch, "
                            "const struct device *, enum sensor_channel);`"):
        return False

    ok = report.check(re.search(r"\bDEFINE_FAKE_VALUE_FUNC\s*\(\s*int\s*,\s*%s\s*," % re.escape(name), c),
                      f"`{name}` is defined", path=FAKE_C,
                      fix=f"Define the fake in `sensor_fake.c` with the same signature: `DEFINE_FAKE_VALUE_FUNC(int, {name}, "
                          "const struct device *, enum sensor_channel);`")

    api = SAMPLE_FETCH_RE.search(c)
    target = api.group(1) if api else None
    ok &= report.check(target == name, "`fake_sensor_api.sample_fetch` points at the fake", path=FAKE_C,
                       line=line_of(c, api.start()) if api else None,
                       fix=f"Set `.sample_fetch = {name},` in `fake_sensor_api`"
                           + (f" (it is `{target}` now)." if target else "."))

    body, line = function_body(c, "sensor_fake_reset")
    ok &= report.check(body is not None and re.search(r"\bRESET_FAKE\s*\(\s*%s\s*\)" % re.escape(name), body),
                       f"`sensor_fake_reset()` resets `{name}`", path=FAKE_C, line=line,
                       fix=f"Add `RESET_FAKE({name});` to `sensor_fake_reset()`, so each test starts with a clean fake.")
    return bool(ok)


# ---------------------------------------------------------------- twister results

def short(identifier):
    """'homework.unit.temp_alarm.temp_alarm_check.below_threshold' -> 'test_below_threshold'."""
    last = identifier.rsplit(".", 1)[-1]
    return last if last.startswith("test_") else f"test_{last}"


def load_twister(report, twister_out, platform, root="."):
    """{test name: [status per scenario]} and the failing assertion lines, or None (already reported)."""
    path = os.path.join(twister_out, "twister.json")
    try:
        with open(path) as f:
            data = json.load(f)
    except (OSError, ValueError):
        report.check(False, f"twister ran the tests on `{platform}`",
                     fix=f"Make the tests build and run: `{TWISTER_CMD}` produced no results.")
        return None
    suites = [s for s in data.get("testsuites", []) if s.get("platform", "").split("/")[0] == platform.split("/")[0]]
    if not report.check(suites, f"twister ran the tests on `{platform}`",
                        fix=f"Make `{TWISTER_CMD}` run the `temp_alarm` tests; twister selected none."):
        return None
    for suite in suites:
        ran = [c for c in suite.get("testcases") or [] if c.get("status") not in ("blocked", "skipped", None)]
        if suite.get("status") in ("error", "failed") and not ran and "rc=" not in (suite.get("reason") or ""):
            reason = (suite.get("reason") or suite.get("status"))[:400]
            path, line = app.twister_build_location(twister_out, os.path.join(root, TEST_ROOT), TEST_ROOT)
            report.check(False, "the test binary builds and runs", path=path, line=line,
                         fix=f"Fix the build of `tests/temp_alarm`: twister reports \"{reason}\". "
                             f"Run `{TWISTER_CMD} -v` to see the error.")
            return None
    cases, failed_at, crashed = {}, {}, []
    for suite in suites:
        for case in suite.get("testcases", []):
            cases.setdefault(short(case.get("identifier", "")), []).append(case.get("status", "unknown"))
        log = suite.get("log") or ""
        # Only the file and line are kept; the assertion text may contain student strings.
        for m in ASSERT_FAIL_RE.finditer(log):
            rel = BASIC if m.group(1) == "test_basic" else ERRORS
            for name in list(cases):
                if m.group(3).endswith(name):
                    failed_at.setdefault(name, (rel, int(m.group(2))))
        if "rc=-11" in (suite.get("reason") or "") or "Segmentation" in log:
            crashed.append(suite.get("name"))
    if crashed:
        report.check(False, "the test binary runs to the end", path=FAKE_C,
                     fix="The test binary crashed (segmentation fault). If a test calls `temp_alarm_check()`, the "
                         "`sample_fetch` fake must be in `fake_sensor_api` (task 0).")
        return None
    return cases, failed_at


def case_checks(report, twister, names, lines):
    """Each test in `names` passed in every scenario (shuffled and ordered)."""
    cases, failed_at = twister
    for name in names:
        statuses = cases.get(name)
        path, line = lines.get(name, (None, None))
        if not statuses:
            report.check(False, f"`{name}` ran", path=path, line=line,
                         fix=f"Make `{name}` run under twister; it is missing from the results.")
            continue
        if all(s == "passed" for s in statuses):
            report.check(True, f"`{name}` passes")
            continue
        if "skipped" in statuses:
            fix = f"Implement `{name}`; twister reports it as skipped."
        elif name in failed_at:
            path, line = failed_at[name]
            fix = f"Fix `{name}`: the assertion on line {line} fails. Check the expected value against `TEST_SPEC.md`."
        elif "passed" in statuses:
            fix = (f"Fix `{name}`: it passes in fixed order but fails in random order, so it depends on another test. "
                   "Set up everything it needs in the test itself.")
        else:
            fix = f"Fix `{name}`: it {statuses[0]} under twister. Check its assertions against `TEST_SPEC.md`."
        report.check(False, f"`{name}` passes", fix=fix, path=path, line=line)


def common_args(description):
    ap = argparse.ArgumentParser(description=description)
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--twister-out", help="twister --outdir of the run on tests/temp_alarm")
    ap.add_argument("--mutants", help="mutants.json from l8_mutants.py")
    ap.add_argument("--platform", default="native_sim", help="platform twister ran on (default: native_sim)")
    ap.add_argument("--json", help="write the result JSON here")
    return ap


def main():
    args = common_args(__doc__.splitlines()[0]).parse_args()
    root = os.path.abspath(args.root)
    report = Report("l8-task0", "temp_alarm sensor fake", where=FAKE_C)
    if fake_checks(report, root):
        if args.twister_out:
            twister = load_twister(report, args.twister_out, args.platform, root)
            if twister:
                case_checks(report, twister, [PROVIDED[2]], test_lines(root))
        else:
            report.note("Tests were not run (no --twister-out); only the source was checked.")
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
