#!/usr/bin/env python3
"""Check l2-task1: a shared counter, without and with a mutex (see TASKS.md, L2).

Static: app/src/counter.c defines counter_increment_unsafe(), counter_increment(),
counter_get() and counter_reset(); counter_increment() takes and releases the same
mutex around the whole update, and doesn't swap the mutex for atomics.
Course test: tests/l2_counter (from the course repository) calls the student's
counter_increment() from two threads and expects an exact total.
Run: builds app/ for native_sim and reads the two fixed lines
  [RESULT] mode=unsafe total=<n> expected=<n>
  [RESULT] mode=mutex total=<n> expected=<n>
The mutex total must equal the expected one. The unsafe total may come out right:
a lost update doesn't happen on every run.

    python3 .github/scripts/check_l2_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import os
import re
import sys

from homework_report import Report
import homework_app as app

APP = "app"
MAIN = f"{APP}/src/main.c"
COUNTER = f"{APP}/src/counter.c"
TEST = "tests/l2_counter"
FUNCTIONS = ("counter_increment_unsafe", "counter_increment", "counter_get", "counter_reset")
SECONDS = 5


def static_checks(report, root):
    text = app.read(os.path.join(root, COUNTER))
    if not report.check(text is not None, f"`{COUNTER}` exists", path=COUNTER,
                        fix=f"Put the counter in `{COUNTER}`, with `counter.h` next to it (TASKS.md, L2)."):
        return False
    code = app.strip_comments(text)
    ok = True
    for name in FUNCTIONS:
        body, line = app.function_body(code, name)
        ok &= report.check(body is not None, f"`{name}()` is defined", path=COUNTER, line=line,
                           fix=f"Define `{name}()` in `{COUNTER}` (TASKS.md, L2).")
    cmake = app.read(os.path.join(root, APP, "CMakeLists.txt")) or ""
    ok &= report.check("counter.c" in cmake or "GLOB" in cmake, "`counter.c` is built", path=f"{APP}/CMakeLists.txt",
                       line=app.locate({"c": cmake}, "target_sources")[1],
                       fix=f"Add `src/counter.c` to `target_sources` in `{APP}/CMakeLists.txt`.")
    body, line = app.function_body(code, "counter_increment")
    if body is None:
        return False
    locks = re.findall(r"\bk_mutex_lock\s*\(\s*&?\s*([\w.>-]+)", body)
    unlocks = re.findall(r"\bk_mutex_unlock\s*\(\s*&?\s*([\w.>-]+)", body)
    same = bool(locks) and set(locks) == set(unlocks)
    ok &= report.check(same, "`counter_increment()` locks and unlocks the same mutex", path=COUNTER, line=line,
                       fix="In `counter_increment()`, take a mutex with `k_mutex_lock()` before the update and release "
                           "the same mutex with `k_mutex_unlock()` after it.")
    if same:
        mutex = locks[0]
        defined = re.search(r"\bK_MUTEX_DEFINE\s*\(\s*%s\s*\)|\bk_mutex_init\s*\(\s*&?\s*%s\s*\)"
                            % (re.escape(mutex), re.escape(mutex)), code)
        ok &= report.check(defined, f"the mutex `{mutex}` is defined", path=COUNTER, line=line,
                           fix=f"Define the mutex with `K_MUTEX_DEFINE({mutex});` (or `k_mutex_init()` before use).")
        lock_at = body.find("k_mutex_lock")
        unlock_at = body.rfind("k_mutex_unlock")
        between = body[lock_at:unlock_at]
        ok &= report.check("return" not in between, "the mutex is released on every path", path=COUNTER, line=line,
                           fix="Don't `return` between `k_mutex_lock()` and `k_mutex_unlock()` in `counter_increment()`.")
    ok &= report.check(not re.search(r"\batomic_\w+\s*\(", body), "`counter_increment()` uses the mutex, not atomics",
                       path=COUNTER, line=line,
                       fix="Keep the mutex in `counter_increment()`: the task asks for a mutex, not `atomic_*`.")
    return bool(ok)


def test_checks(report, root, work):
    outdir = os.path.join(work, "twister-out")
    cases, problem = app.run_twister(os.path.join(root, TEST), outdir)
    path, line = app.twister_build_location(outdir, os.path.join(root, APP), APP)
    if not report.check(problem is None and cases, "the course's counter test builds", path=path or COUNTER, line=line,
                        fix=f"Make `{COUNTER}` build in the course's test: twister reports \"{problem}\". "
                            f"Run `west twister -T {TEST} -p native_sim -v`."):
        return
    code = app.strip_comments(app.read(os.path.join(root, COUNTER)) or "")
    report.check(cases.get("test_reset_clears_counter") == "passed", "`counter_reset()` sets the counter to 0",
                 path=COUNTER, line=app.function_body(code, "counter_reset")[1],
                 fix="Make `counter_reset()` set the counter to 0, so `counter_get()` returns 0.")
    report.check(cases.get("test_two_threads_mutex_total") == "passed",
                 "two threads calling `counter_increment()` get the exact total", path=COUNTER,
                 line=app.function_body(code, "counter_increment")[1],
                 fix="Two threads calling `counter_increment()` 100000 times each didn't get 200000. Protect the "
                     "whole read-modify-write with the mutex, and make `counter_get()` return the counter.")


def run_checks(report, root, work):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, APP, build_dir):
        return
    files = app.sources(root, APP)
    out, _ = app.run_native(build_dir, SECONDS)
    results = {}
    for _, line in app.tagged(out, "RESULT"):
        m = re.search(r"mode\s*=\s*(\w+)", line)
        if m:
            results[m.group(1)] = (app.field(line, "total"), app.field(line, "expected"))
    for mode in ("unsafe", "mutex"):
        path, line = app.locate(files, f"mode={mode}", "[RESULT]", re.compile(r"\bk_thread_join\s*\("))
        report.check(mode in results and None not in results[mode], f"`[RESULT] mode={mode}` is printed",
                     path=path, line=line,
                     fix=f"Print `[RESULT] mode={mode} total=<n> expected=<n>` after both threads finish "
                         "(TASKS.md, L2).")
    if "mutex" in results and None not in results["mutex"]:
        total, expected = results["mutex"]
        path, line = app.locate(files, "mode=mutex", re.compile(r"\bk_thread_join\s*\("))
        report.check(expected > 0 and total == expected, f"the mutex total is exact ({total} of {expected})",
                     path=path, line=line,
                     fix=f"With the mutex the total was {total}, not {expected}. Print the result only after "
                         "`k_thread_join()` on both threads, and protect the whole update with the mutex.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for builds and logs (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    root, work = os.path.abspath(args.root), os.path.abspath(args.work)
    report = Report("l2-task1", "Shared counter with a mutex", where=MAIN)
    if static_checks(report, root) and not args.no_run:
        test_checks(report, root, work)
        run_checks(report, root, work)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
