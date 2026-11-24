#!/usr/bin/env python3
"""Check l5-task1: bounded queue, task watchdog and health check (see TASKS.md, L5).

Static: CONFIG_TASK_WDT=y; the queue is a k_msgq (any name); the consumer (the
thread that reads it) feeds the task watchdog after real progress, and a task
watchdog channel is added; a health thread reads the queue's occupancy.
Run: builds app/ for native_sim, runs it for 4 s and reads the fixed log lines
  [CONS] simulating stall          (when the consumer reaches seq 20 and sleeps 5 s)
  [HEALTH] occupancy=<used>/<capacity>        (every check; `WARN` in the line at 75% or more)
  [WDT] ...                         (from the task watchdog callback)
Every check at 75% or more of the capacity must warn and none below, and the
watchdog fires only after the stall begins.

    python3 .github/scripts/check_l5_task1.py --root zephyr-course --json result.json

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
SECONDS = 4


def static_checks(report, root):
    files = app.sources(root, APP)
    if not report.check(files, f"`{APP}/` has the app's sources", fix=f"Put the app in `{APP}/` (TASKS.md, L5)."):
        return False
    ok = report.check(app.kconfig(root, APP, "TASK_WDT") == "y", "`CONFIG_TASK_WDT=y`", path=f"{APP}/prj.conf",
                      fix=f"Enable the task watchdog: add `CONFIG_TASK_WDT=y` to `{APP}/prj.conf`.")
    code = "\n".join(app.strip_comments(t) for t in files.values())
    # Any name: the queues are the app's message queues; the consumer is the thread that reads one.
    queues = (re.findall(r"\bK_MSGQ_DEFINE\s*\(\s*(\w+)\s*,", code)
              + re.findall(r"\bk_msgq_init\s*\(\s*&\s*(\w+)", code))
    ok &= report.check(queues, "the queue is a `k_msgq`",
                       **app.where(files, re.compile(r"\bK_\w+_DEFINE\s*\(\s*\w*q\w*\s*,"), "[CONS]"),
                       fix="Use a message queue for the samples, e.g. `K_MSGQ_DEFINE(sample_q, sizeof(struct sample), 8, 4);`.")
    ok &= report.check(re.search(r"\btask_wdt_add\s*\(", code), "a task watchdog channel is added",
                       **app.where(files, re.compile(r"\btask_wdt_init\s*\("), re.compile(r"\bk_msgq_get\s*\(")),
                       fix="Add a task watchdog channel for the consumer with `task_wdt_add()`.")
    reads = r"\bk_msgq_get\s*\(\s*&\s*(%s)\b" % "|".join(map(re.escape, queues)) if queues else None
    consumer = health = None
    queue = queues[0] if queues else "sample_q"
    for t in app.threads(files):
        path, body, line = app.find_function(files, t["entry"])
        m = reads and body and re.search(reads, body)
        if m:
            consumer, queue = (path, body, line), m.group(1)
    for t in app.threads(files):
        path, body, line = app.find_function(files, t["entry"])
        if body and re.search(r"\bk_msgq_num_(?:used|free)_get\s*\(\s*&\s*%s\b" % re.escape(queue), body):
            health = (path, body, line)
    if report.check(consumer, "a consumer thread reads the queue", **app.where(files, re.compile(r"\bk_msgq_get\s*\(")),
                    fix=f"Read the samples in the consumer thread with `k_msgq_get(&{queue}, ...)`."):
        ok &= report.check(re.search(r"\btask_wdt_feed\s*\(", consumer[1]), "the consumer feeds the watchdog",
                           path=consumer[0], line=consumer[2],
                           fix="Call `task_wdt_feed()` in the consumer's loop after it processes a sample, so a stall "
                               "stops the feeding.")
    else:
        ok = False
    ok &= report.check(health, f"a health thread reads the occupancy of `{queue}`", **app.where(files, "[HEALTH]"),
                       fix=f"Add a health-check thread that reads `k_msgq_num_used_get(&{queue})` periodically.")
    return bool(ok)


def run_checks(report, root, work):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, APP, build_dir):
        return
    files = app.sources(root, APP)
    consumer = app.where(files, "simulating stall", "[CONS]", re.compile(r"\bk_msgq_get\s*\("))
    health = app.where(files, "[HEALTH]", re.compile(r"\bk_msgq_num_(?:used|free)_get\s*\("))
    out, _ = app.run_native(build_dir, SECONDS)
    stall = [i for i, ln in app.tagged(out, "CONS") if "simulating stall" in ln]
    if not report.check(stall, "the consumer stalls (`[CONS] simulating stall`)", **consumer,
                        fix="When the consumer receives seq 20, print `[CONS] simulating stall` and sleep 5 s "
                            "(TASKS.md, L5)."):
        return
    samples = []
    for i, ln in app.tagged(out, "HEALTH"):
        m = re.search(r"occupancy\s*=\s*(\d+)\s*/\s*(\d+)", ln)
        if m:
            samples.append((int(m.group(1)), int(m.group(2)), "WARN" in ln))
    if not report.check(samples, "the health check prints the occupancy", **health,
                        fix="Print `[HEALTH] occupancy=<used>/<capacity>` on every check, with `WARN` when the queue "
                            "is at 75% or more (TASKS.md, L5)."):
        return
    report.check(any(w for _, _, w in samples), "the health check warns while the consumer is stalled", **health,
                 fix="Print `[HEALTH] WARN occupancy=<used>/<capacity>` when the queue reaches 75% of its capacity.")
    missed = [(u, c) for u, c, w in samples if u * 100 >= 75 * c and not w]
    early = [(u, c) for u, c, w in samples if u * 100 < 75 * c and w]
    report.check(not missed, "every check at 75% or more warns", **health,
                 fix="The health check printed occupancy %d/%d without `WARN`. Warn when `used * 100 >= 75 * capacity`."
                     % missed[0] if missed else None)
    report.check(not early, "no warning below 75%", **health,
                 fix="The health check warned at %d/%d, below 75%%. Warn when `used * 100 >= 75 * capacity`."
                     % early[0] if early else None)
    wdt = [i for i, _ in app.tagged(out, "WDT")]
    report.check(wdt and wdt[0] > stall[0], "the watchdog fires after the stall begins",
                 **(app.where(files, re.compile(r"\btask_wdt_feed\s*\(")) if wdt else
                    app.where(files, "[WDT]", re.compile(r"\btask_wdt_add\s*\("))),
                 fix=("The watchdog fired before the stall: feed it after every processed sample, with a timeout "
                      "longer than the consumer's normal gap (e.g. 1000 ms).") if wdt else
                     "Print `[WDT] ...` from the task watchdog callback, and use a timeout shorter than the 5 s stall.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for the build and its log (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    report = Report("l5-task1", "Queue, watchdog and health check", where=MAIN)
    if static_checks(report, os.path.abspath(args.root)) and not args.no_run:
        run_checks(report, os.path.abspath(args.root), os.path.abspath(args.work))
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
