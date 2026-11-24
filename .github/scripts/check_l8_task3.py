#!/usr/bin/env python3
"""Check l8-task3: coverage of the temp_alarm module.

CI runs the tests with coverage itself, into a folder outside the repository, and
compares the result with the report the student committed (`twister-out/coverage/`,
as tests/temp_alarm/HOMEWORK.md asks):

    west twister -T tests/temp_alarm -p native_sim --outdir twister-out \\
        --coverage --coverage-tool gcovr --gcov-tool gcov --coverage-basedir app/modules/temp_alarm
    python3 .github/scripts/check_l8_task3.py --twister-out twister-out --mutants mutants.json

Checks:
  1. The l8-task2 checks: the fake, all 13 tests pass, every mutant is caught.
  2. gcovr's summary of app/modules/temp_alarm is 100% lines, functions and branches.
     When a target is missed, the message names the first lines never run.
  3. The committed report (gcovr's index.html, or coverage_summary.json) shows the
     same numbers as CI's run, within TOLERANCE: a stale or hand-made report fails.

Exit code 0 when approved, 1 otherwise.
"""
import json
import os
import re
import sys

from homework_report import Report
from check_l8_task0 import ERRORS, common_args
from check_l8_task2 import all_checks

MODULE_DIR = "app/modules/temp_alarm"
COVERAGE_CMD = ("west twister -T tests/temp_alarm -p native_sim --coverage --coverage-tool gcovr "
                "--gcov-tool gcov --coverage-basedir app/modules/temp_alarm")
TARGETS = (  # (summary key, label, minimum %), most actionable first
    ("function_percent", "Function", 100.0),
    ("line_percent", "Line", 100.0),
    ("branch_percent", "Branch", 100.0),
)
MAX_LISTED = 5
COMMITTED = "twister-out/coverage/index.html"
# Allowed difference, in percentage points, between the committed report and CI's run. Lines and
# functions depend only on the code; branch counts vary a little between gcc versions.
TOLERANCE = {"line_percent": 1.0, "function_percent": 0.5, "branch_percent": 5.0}
HTML_ROWS = {"Lines": "line_percent", "Functions": "function_percent", "Branches": "branch_percent"}
SKIP_DIRS = {".git", ".github", "deps", ".west", "build"}


def load(path):
    try:
        with open(path) as f:
            return json.load(f)
    except (OSError, ValueError):
        return None


def module_files(report_json):
    """gcovr --json files, keyed by path relative to the module directory."""
    return {f["file"]: f for f in (report_json or {}).get("files", [])}


def source_path(rel):
    """'src/temp_alarm.c' (relative to the coverage basedir) -> repo-relative path."""
    return f"{MODULE_DIR}/{rel}"


def uncovered_functions(files):
    out = []
    for rel, f in sorted(files.items()):
        for fn in f.get("functions", []):
            if not fn.get("execution_count"):
                name = fn.get("demangled_name") or fn.get("name")
                out.append((name, source_path(rel), fn.get("lineno")))
    return out


def merged_lines(f):
    """gcovr 8 may list a source line once per function; merge them by line number."""
    merged = {}
    for ln in f.get("lines", []):
        if ln.get("gcovr/noncode") or ln.get("gcovr/excluded"):
            continue
        m = merged.setdefault(ln["line_number"], {"count": 0, "branches": []})
        m["count"] += ln.get("count") or 0
        m["branches"] += [b for b in ln.get("branches") or [] if not b.get("gcovr/excluded")]
    return sorted(merged.items())


def uncovered_lines(files):
    return [(source_path(rel), n) for rel, f in sorted(files.items())
            for n, m in merged_lines(f) if not m["count"]]


def partial_branch_lines(files):
    """Lines that ran but where some branch outcome never happened."""
    return [(source_path(rel), n) for rel, f in sorted(files.items())
            for n, m in merged_lines(f)
            if m["count"] and any(not b.get("count") for b in m["branches"])]


def lines_text(items):
    """[(path, 12), (path, 40)] -> '12, 40' (single file) for a short message."""
    nums = sorted({n for _, n in items})
    shown = ", ".join(str(n) for n in nums[:MAX_LISTED])
    return shown + (f" and {len(nums) - MAX_LISTED} more" if len(nums) > MAX_LISTED else "")


def coverage_checks(report, twister_out):
    summary = load(os.path.join(twister_out, "coverage_summary.json"))
    if not report.check(summary is not None, "the coverage report was generated",
                        fix=f"Make the tests build and run with `{COVERAGE_CMD}`; no coverage report was produced."):
        return
    files = module_files(load(os.path.join(twister_out, "coverage.json")))
    if not summary.get("files") and not files:
        report.fail_hard(f"The coverage report has no files under `{MODULE_DIR}`; check `--coverage-basedir`.")
        return

    for key, label, target in TARGETS:
        value = summary.get(key)
        value = float(value) if value is not None else 0.0
        ok = value >= target
        fix, path, line = None, None, None
        if not ok:
            fix = (f"{label} coverage of `{MODULE_DIR}` is {value:.1f}%; it should be 100% after Task 2. "
                   "Check each test body against `TEST_SPEC.md`.")
            if key == "function_percent":
                missing = uncovered_functions(files)
                if missing:
                    names = ", ".join(f"`{n}()`" for n, _, _ in missing[:MAX_LISTED])
                    fix = f"Function coverage of `{MODULE_DIR}` is {value:.1f}%; no test calls {names} yet."
                    _, path, line = missing[0]
            elif key == "line_percent":
                missing = uncovered_lines(files)
                if missing:
                    fix += f" No test runs lines {lines_text(missing)} of `{missing[0][0]}`."
                    path, line = missing[0]
            elif key == "branch_percent":
                missing = partial_branch_lines(files)
                if missing:
                    fix += f" Cover both outcomes of the conditions on lines {lines_text(missing)} of `{missing[0][0]}`."
                    path, line = missing[0]
        report.check(ok, f"{label} coverage {value:.1f}% (target 100%)", fix=fix, path=path, line=line)


def find_committed(root):
    """The committed report: twister-out/coverage/index.html, or the closest thing to it."""
    if os.path.isfile(os.path.join(root, COMMITTED)):
        return COMMITTED
    found = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        rel = os.path.relpath(dirpath, root)
        for f in filenames:
            if (f == "index.html" and os.path.basename(dirpath) == "coverage") or f == "coverage_summary.json":
                found.append(os.path.normpath(os.path.join(rel, f)))
    return min(found, key=lambda p: (not p.endswith("index.html"), not p.startswith("twister-out"), p),
               default=None)


def committed_numbers(path):
    """{line_percent, function_percent, branch_percent} from gcovr's HTML or summary JSON, or None."""
    if path.endswith(".json"):
        data = load(path) or {}
        nums = {k: data.get(k) for k in TOLERANCE}
    else:
        try:
            with open(path, encoding="utf-8", errors="replace") as f:
                text = f.read()
        except OSError:
            return None
        nums = {}
        for row, key in HTML_ROWS.items():
            # gcovr: <th scope="row">Lines:</th> <td ...>100.0%</td>
            m = re.search(r">\s*%s:\s*<.{0,400}?([\d.]+)\s*%%" % row, text, re.S)
            nums[key] = m.group(1) if m else None
    try:
        return {k: float(v) for k, v in nums.items()} if all(v is not None for v in nums.values()) else None
    except ValueError:
        return None


def committed_checks(report, root, twister_out):
    rel = find_committed(root)
    if not report.check(rel, "the coverage report is committed", path=COMMITTED,
                        fix="Commit the coverage report: run the Task 3 coverage command at the repository root, then "
                            "`git add twister-out/coverage` and push. CI compares it with its own run."):
        return
    if rel != COMMITTED:
        report.note(f"The coverage report is at `{rel}`; HOMEWORK.md asks for `twister-out/coverage/`.")
    mine = committed_numbers(os.path.join(root, rel))
    if not report.check(mine is not None, "the committed report can be read", path=rel,
                        fix=f"Commit the `index.html` that gcovr wrote to `twister-out/coverage/`; the numbers can't "
                            f"be read from `{rel}`."):
        return
    ci = load(os.path.join(twister_out, "coverage_summary.json"))
    if not ci:
        return  # no CI report: coverage_checks already says so
    off = []
    for key, label, _ in TARGETS:
        if key in ci and abs(float(ci[key]) - mine[key]) > TOLERANCE[key]:
            name = {"Line": "lines", "Function": "functions", "Branch": "branches"}[label]
            off.append(f"{name} {mine[key]:.1f}% (CI: {float(ci[key]):.1f}%)")
    report.check(not off, "the committed report matches CI's run", path=rel,
                 fix=f"Regenerate and commit `{os.path.dirname(rel) or rel}`: it shows " + ", ".join(off) +
                     ", so it doesn't come from the tests in this commit.")


def main():
    args = common_args(__doc__.splitlines()[0]).parse_args()
    root = os.path.abspath(args.root)
    report = Report("l8-task3", "temp_alarm coverage", where=ERRORS)
    if all_checks(report, root, args, upto=3):
        coverage_checks(report, args.twister_out)
        committed_checks(report, root, args.twister_out)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
