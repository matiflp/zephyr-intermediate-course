#!/usr/bin/env python3
"""Check l1-task1: three preemptible threads and a cooperative one (see TASKS.md, L1).

Static: app/ defines threads at priorities 7, 5 and 3 and a cooperative thread
(negative priority) whose entry calls k_yield().
Run: builds app/ for native_sim, runs it for 3 s of simulated time and reads the
fixed log lines:
  [T7] tick=<n>   [T5] tick=<n>   [T3] tick=<n>
  [COOP] iter=<i> (five times), then [COOP] yield, then [COOP] done
The sleep periods (300, 200, 100 ms) show as how often each thread logs, and the
cooperative thread's lines must run from the first iter to done with no [Tn] line
in between: k_yield() only gives the CPU to equal or higher priority threads.

    python3 .github/scripts/check_l1_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import os
import sys

from homework_report import Report
import homework_app as app

APP = "app"
MAIN = f"{APP}/src/main.c"
SECONDS = 3
PERIODS = {"T7": 300, "T5": 200, "T3": 100}


def static_checks(report, root):
    files = app.sources(root, APP)
    main = MAIN
    if not report.check(files, f"`{APP}/` has the app's sources",
                        fix=f"Put the app in `{APP}/` (with `{main}`), as in the starter."):
        return None
    found = app.threads(files)
    prios = [t["prio"] for t in found]
    for prio in (7, 5, 3):
        report.check(prio in prios, f"a thread with priority {prio}", path=main,
                     fix=f"Create a preemptible thread with priority {prio} (TASKS.md, L1).")
    coop = [t for t in found if t["prio"] is not None and t["prio"] < 0]
    if not report.check(coop, "a cooperative thread (negative priority)", path=main,
                        fix="Add the cooperative thread at priority -1 (TASKS.md, L1)."):
        return files
    t = coop[0]
    path, body, line = app.find_function(files, t["entry"])
    report.check(body is not None and "k_yield" in body, "the cooperative thread calls `k_yield()`",
                 path=path or t["path"], line=line or t["line"],
                 fix=f"Call `k_yield()` in `{t['entry']}()` after the five busy iterations.")
    return files


def thread_at(files, prio):
    """(path, line) where the thread with priority `prio` is defined, or (None, None)."""
    return next(((t["path"], t["line"]) for t in app.threads(files) if t["prio"] == prio), (None, None))


def coop_at(files):
    """(path, line) of the cooperative thread's entry function (or of its definition), or (None, None)."""
    t = next((t for t in app.threads(files) if t["prio"] is not None and t["prio"] < 0), None)
    if t is None:
        return None, None
    path, _, line = app.find_function(files, t["entry"])
    return (path, line) if path else (t["path"], t["line"])


def run_checks(report, root, work, files):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, APP, build_dir):
        return
    out, _ = app.run_native(build_dir, SECONDS)
    expected = {tag: SECONDS * 1000 // period for tag, period in PERIODS.items()}
    for tag, period in PERIODS.items():
        n = len(app.tagged(out, tag))
        want = expected[tag]
        path, line = thread_at(files, int(tag[1:]))
        report.check(abs(n - want) <= max(2, want * 0.2),
                     f"`[{tag}] tick` logs about every {period} ms ({n} lines in {SECONDS} s)", path=path, line=line,
                     fix=(f"`[{tag}]` printed {n} lines in {SECONDS} s; a thread that sleeps {period} ms prints about "
                          f"{want}. Log `[{tag}] tick=<n>` once per loop and sleep {period} ms.") if n else
                         f"Log `[{tag}] tick=<n>` from the priority-{tag[1:]} thread each time it wakes (TASKS.md, L1).")

    lines = out.splitlines()
    path, line = coop_at(files)
    coop = app.tagged(out, "COOP")
    iters = [(i, ln) for i, ln in coop if "iter" in ln]
    yields = [i for i, ln in coop if "yield" in ln]
    dones = [i for i, ln in coop if "done" in ln]
    if not report.check(len(iters) == 5, f"`[COOP] iter` printed five times ({len(iters)})", path=path, line=line,
                        fix="Print `[COOP] iter=<i>` in each of the cooperative thread's five busy iterations."):
        return
    if not report.check(yields and dones and iters[-1][0] < yields[0] < dones[0],
                        "`[COOP] yield` and `[COOP] done` follow the iterations", path=path, line=line,
                        fix="After the five iterations print `[COOP] yield`, call `k_yield()`, then print `[COOP] done`."):
        return
    between = [ln for ln in lines[iters[0][0]:dones[0]] if any(f"[{t}]" in ln for t in PERIODS)]
    report.check(not between, "no other thread runs between the first `iter` and `done`", path=path, line=line,
                 fix="A preemptible thread logged while the cooperative thread was running. Keep the cooperative "
                     "thread at a negative priority and busy-wait (`k_busy_wait()`) instead of sleeping in its loop.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for the build and its log (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    root = os.path.abspath(args.root)
    report = Report("l1-task1", "Thread priorities and k_yield()", where=MAIN)
    files = static_checks(report, root)
    if files is not None and not args.no_run:
        run_checks(report, root, os.path.abspath(args.work), files)
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
