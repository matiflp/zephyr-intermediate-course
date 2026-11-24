#!/usr/bin/env python3
"""Check l4-task1: a zbus channel with a listener and a subscriber (see TASKS.md, L4).

Static (any names): CONFIG_ZBUS=y; a channel carrying a struct, defined once; a
listener and a subscriber (or a message subscriber) observe it; when other modules
use the channel, a header declares it (ZBUS_CHAN_DECLARE) and defines the struct;
the listener callback doesn't block or publish.
Run: builds app/ for native_sim, runs it for 2 s and reads the fixed log lines
  [PUB] seq=<n> t=<ms>   [DISPLAY] seq=<n> t=<ms>   [LOGGER] seq=<n> t=<ms>
Samples are published about every 100 ms, the display shows each one in the same
instant (the listener runs inside the publish), and the logger runs later.

    python3 .github/scripts/check_l4_task1.py --root zephyr-course --json result.json

Exit code 0 when approved, 1 otherwise.
"""
import argparse
import os
import re
import statistics
import sys

from homework_report import Report
import homework_app as app

APP = "app"
MAIN = f"{APP}/src/main.c"
SECONDS = 2
BLOCKING = ("k_msleep", "k_sleep", "k_usleep", "k_busy_wait", "zbus_chan_pub", "zbus_chan_read", "k_yield")


def defined(code, macro):
    """[(path, args, line)] for every use of `macro` in the .c files."""
    return [(p, a, ln) for p, c in code.items() for a, ln, _ in app.calls(c, macro) if a]


def observers(code, chans, name):
    """Names of the observers of channel `name`: ZBUS_OBSERVERS in its definition, or added later."""
    out = set()
    for _, args, _ in chans:
        if args[0] == name:
            for m in re.finditer(r"\bZBUS_OBSERVERS\s*\(([^)]*)\)", ", ".join(args[2:])):
                out |= {o.strip() for o in m.group(1).split(",") if o.strip()}
    for c in code.values():
        for args, _, _ in app.calls(c, "ZBUS_CHAN_ADD_OBS"):
            if len(args) > 1 and args[0] == name:
                out.add(args[1])
        for args, _, _ in app.calls(c, "zbus_chan_add_obs"):
            if len(args) > 1 and args[0].lstrip("&").strip() == name:
                out.add(args[1].lstrip("&").strip())
    return out


def static_checks(report, root):
    """Any names: the channel, its message struct, the listener and the subscriber are found by how
    they are defined (ZBUS_CHAN_DEFINE, ZBUS_LISTENER_DEFINE, ZBUS_[MSG_]SUBSCRIBER_DEFINE)."""
    files = app.sources(root, APP)
    if not report.check(files, f"`{APP}/` has the app's sources", fix=f"Put the app in `{APP}/` (TASKS.md, L4)."):
        return False
    ok = report.check(app.kconfig(root, APP, "ZBUS") == "y", "`CONFIG_ZBUS=y`", path=f"{APP}/prj.conf",
                      fix=f"Enable zbus: add `CONFIG_ZBUS=y` to `{APP}/prj.conf`.")
    code = {p: app.strip_comments(t) for p, t in files.items() if p.endswith(".c")}
    headers = {p: app.strip_comments(t) for p, t in files.items() if p.endswith(".h")}
    chans = defined(code, "ZBUS_CHAN_DEFINE")
    listeners = defined(code, "ZBUS_LISTENER_DEFINE")
    subscribers = defined(code, "ZBUS_SUBSCRIBER_DEFINE") + defined(code, "ZBUS_MSG_SUBSCRIBER_DEFINE")
    msg_subscribers = {a[0] for _, a, _ in defined(code, "ZBUS_MSG_SUBSCRIBER_DEFINE")}
    if not report.check(chans, "a zbus channel is defined", **app.where(files, "[PUB]"),
                        fix="Define the sensor channel in a `.c` file: `ZBUS_CHAN_DEFINE(sensor_chan, struct sensor_msg, "
                            "NULL, NULL, ZBUS_OBSERVERS(...), ZBUS_MSG_INIT(0))`."):
        return False

    # The sensor channel: the one a listener observes (an app may have more channels).
    names = {a[0] for _, a, _ in listeners}
    chan = next((c for c in chans if observers(code, chans, c[1][0]) & names), chans[0])
    cpath, cargs, cline = chan
    name = cargs[0]
    same = [c for c in chans if c[1][0] == name]
    ok &= report.check(len(same) == 1, f"`{name}` is defined once", path=same[-1][0], line=same[-1][2],
                       fix=f"Define `{name}` in one `.c` file only; the other modules declare it with "
                           f"`ZBUS_CHAN_DECLARE({name});` from a shared header.")
    struct = re.fullmatch(r"struct\s+(\w+)", cargs[1].strip()) if len(cargs) > 1 else None
    ok &= report.check(struct, f"`{name}` carries a struct", path=cpath, line=cline,
                       fix=f"Use a struct as the message type: `ZBUS_CHAN_DEFINE({name}, struct sensor_msg, ...)`.")

    # Split into modules: the channel declaration and the message type go in a shared header.
    users = [p for p, c in code.items() if p != cpath and re.search(r"\b%s\b" % re.escape(name), c)]
    if users:
        decl = [p for p, h in headers.items() if re.search(r"\bZBUS_CHAN_DECLARE\s*\([^)]*\b%s\b" % re.escape(name), h)]
        ok &= report.check(decl, f"`{name}` is declared in a shared header", path=users[0],
                           fix=f"`{users[0]}` uses `{name}`: declare it in a header with `ZBUS_CHAN_DECLARE({name});` "
                               "and include that header in every module that uses it.")
        if struct:
            sname = struct.group(1)
            shared = any(re.search(r"\bstruct\s+%s\s*\{" % re.escape(sname), h) for h in headers.values())
            ok &= report.check(shared, f"`struct {sname}` is in a shared header", path=decl[0] if decl else users[0],
                               fix=f"Define `struct {sname}` in the shared header, so every module uses the same type.")

    watching = observers(code, chans, name)
    lis = [l for l in listeners if l[1][0] in watching]
    ok &= report.check(lis, f"a listener observes `{name}`", path=cpath, line=cline,
                       fix=f"Add `{listeners[0][1][0]}` to `{name}`'s observers with `ZBUS_OBSERVERS(...)` in "
                           "`ZBUS_CHAN_DEFINE`." if listeners else
                           "Add a listener for the display, e.g. `ZBUS_LISTENER_DEFINE(display_lis, display_cb);`, "
                           f"and add it to `{name}`'s observers.")
    subs = [s for s in subscribers if s[1][0] in watching]
    ok &= report.check(subs, f"a subscriber observes `{name}`", path=cpath, line=cline,
                       fix=f"Add `{subscribers[0][1][0]}` to `{name}`'s observers with `ZBUS_OBSERVERS(...)` in "
                           "`ZBUS_CHAN_DEFINE`." if subscribers else
                           "Add a subscriber for the logger, e.g. `ZBUS_SUBSCRIBER_DEFINE(logger_sub, 4);`, and add it "
                           f"to `{name}`'s observers.")
    if any(s[1][0] in msg_subscribers for s in subs):
        ok &= report.check(app.kconfig(root, APP, "ZBUS_MSG_SUBSCRIBER") == "y", "`CONFIG_ZBUS_MSG_SUBSCRIBER=y`",
                           path=f"{APP}/prj.conf",
                           fix="A message subscriber needs `CONFIG_ZBUS_MSG_SUBSCRIBER=y` in `prj.conf`.")
    for p, args, ln in lis[:1]:
        cb = args[1] if len(args) > 1 else ""
        path, body, line = app.find_function(files, cb)
        blocking = [b for b in BLOCKING if body and re.search(r"\b%s\s*\(" % b, body)]
        ok &= report.check(body is not None and not blocking, "the listener callback is short",
                           path=path or p, line=line or ln,
                           fix=f"Keep `{cb}()` short: it runs inside the publisher's `zbus_chan_pub()`. Move "
                               f"{', '.join(f'`{b}()`' for b in blocking) or 'slow work'} to the subscriber's thread.")
    return bool(ok)


def entries(out, tag):
    return [(i, app.field(ln, "seq"), app.field(ln, "t")) for i, ln in app.tagged(out, tag)]


def listener_at(files):
    """Keyword path/line of the listener's callback (or of its definition), for Report.check."""
    for path, text in files.items():
        for args, line, _ in app.calls(app.strip_comments(text), "ZBUS_LISTENER_DEFINE"):
            if len(args) > 1:
                cb_path, _, cb_line = app.find_function(files, args[1])
                return {"path": cb_path or path, "line": cb_line or line}
    return app.where(files, "[DISPLAY]")


def run_checks(report, root, work):
    build_dir = os.path.join(work, "build")
    if not app.check_build(report, root, APP, build_dir):
        return
    files = app.sources(root, APP)
    out, _ = app.run_native(build_dir, SECONDS)
    pub, disp, logger = entries(out, "PUB"), entries(out, "DISPLAY"), entries(out, "LOGGER")
    for tag, items in (("PUB", pub), ("DISPLAY", disp), ("LOGGER", logger)):
        if not report.check(items and all(s is not None and t is not None for _, s, t in items),
                            f"`[{tag}] seq=<n> t=<ms>` lines are printed", **app.where(files, f"[{tag}]"),
                            fix=f"Print `[{tag}] seq=<n> t=<ms>` (TASKS.md, L4); `t` is `k_uptime_get_32()`."):
            return
    gaps = [b[2] - a[2] for a, b in zip(pub, pub[1:])]
    period = statistics.median(gaps) if gaps else 0
    report.check(len(pub) >= SECONDS * 1000 // 120 and 90 <= period <= 120,
                 f"a sample is published about every 100 ms ({len(pub)} in {SECONDS} s)",
                 **app.where(files, "[PUB]", re.compile(r"\bzbus_chan_pub\s*\(")),
                 fix=f"Publish a sample every 100 ms; the run published {len(pub)} in {SECONDS} s"
                     + (f", {period:.0f} ms apart." if gaps else "."))
    shown = {s: t for _, s, t in disp}
    missing = [s for _, s, t in pub if s not in shown or not 0 <= shown[s] - t <= 1]
    report.check(not missing, "the display shows each sample as it is published", **listener_at(files),
                 fix=f"Sample {missing[0] if missing else ''} has no `[DISPLAY]` line in the same instant as its "
                     "`[PUB]`. The listener runs inside the publish: log `[DISPLAY]` from its callback.")
    pub_t = {s: t for _, s, t in pub}
    wrong = [s for _, s, t in logger if s not in pub_t or t < pub_t[s]]
    report.check(not wrong, "the logger handles published samples after they are published",
                 **app.where(files, "[LOGGER]", re.compile(r"\bzbus_sub_wait\w*\s*\(")),
                 fix="`[LOGGER]` printed a sample before it was published or one that never was. Read the channel "
                     "in the subscriber's thread after `zbus_sub_wait()` returns.")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--root", default=".", help="repository root (default: current directory)")
    ap.add_argument("--work", default=".", help="folder for the build and its log (default: current directory)")
    ap.add_argument("--no-run", action="store_true", help="only read the sources")
    ap.add_argument("--json", help="write the result JSON here")
    args = ap.parse_args()
    report = Report("l4-task1", "zbus listener and subscriber", where=MAIN)
    if static_checks(report, os.path.abspath(args.root)) and not args.no_run:
        run_checks(report, os.path.abspath(args.root), os.path.abspath(args.work))
    return report.finish(args.json)


if __name__ == "__main__":
    sys.exit(main())
