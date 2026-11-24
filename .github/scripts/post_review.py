#!/usr/bin/env python3
"""Post the homework review on a pull request.

Runs in the "Homework review" workflow (workflow_run, from the default branch,
with secrets). It never checks out or runs anything from the PR:

1. Finds the PR through the API from the check run's head repository, branch
   and SHA, and skips a SHA that was already reviewed. A "retry" review (the
   check run left no result) doesn't count: the next result for that SHA, after
   an instructor re-runs the failed jobs, is posted as usual.
2. Takes the task from the PR's branch name, and rejects a PR that targets the
   wrong branch (BASES: each task's starter branch) or changes the files the
   checks rely on (PROTECTED: `.github/`, whose workflow the check run used, and
   the module under test in `app/modules/temp_alarm/`): changes needed, without
   reading its results.
3. Reads result.json from the check run's artifact and checks it is for that task.
   The scripts' verdict is final; there is no AI review.
4. Posts one review (event COMMENT) with the status, the changes and the
   checklist, inline comments on lines in the diff, and labels. Approved PRs
   are closed. A PR left for an instructor gets a review request for the
   instructors in MANUAL_REVIEWER (one or more logins, separated by commas or spaces); real problems (a broken
   check run) also mention them in the review. A check run that left no result
   (or a checker error: a missing tool, a step that didn't finish) is our problem, not the student's:
   the review says so and asks for nothing.

    python3 post_review.py --artifacts DIR            # in the workflow
    python3 post_review.py --artifacts DIR --pr 12 --dry-run   # print, post nothing
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import github_api as gh  # noqa: E402
from homework_report import render_markdown  # noqa: E402

TASKS = ["l1-task1", "l2-task1", "l3-task1", "l4-task1", "l5-task1", "l6-task1", "l7-task1",
         "l8-task0", "l8-task1", "l8-task2", "l8-task3"]
TASK_RE = re.compile(r"^(l[1-8]-task[0-3])(?:$|[^0-9])")
# The branch each task's pull request must target: its starter branch, or main when there is none.
BASES = {"l3-task1": "l3-homework", "l6-task1": "l6-homework", "l7-task1": "l7-homework",
         "l8-task0": "l8-demo-homework", "l8-task1": "l8-demo-homework",
         "l8-task2": "l8-demo-homework", "l8-task3": "l8-demo-homework"}
# `ai=` is kept (always 0) so reviews posted while there was an AI review still parse.
MARKER = "<!-- homework-bot sha={sha} task={task} status={status} ai=0 -->"
MARKER_RE = re.compile(r"<!-- homework-bot sha=(\w+) task=(\S*) status=(\w+) ai=(\d) -->")
RANK = {"approved": 0, "changes_needed": 1, "under_review": 2}
LABELS = {
    "approved": ("approved", "0e8a16", "Homework approved by the checks"),
    "changes_needed": ("changes-needed", "d93f0b", "Homework needs changes"),
    "under_review": ("needs-instructor", "fbca04", "An instructor reviews this by hand"),
    "retry": ("checks-not-run", "b60205", "The checks couldn't run; an instructor runs them again"),
}
TASK_LABEL_COLOR = "1d76db"
# Folders the checks rely on: the workflows and scripts, the course test for L2, and the module the L8 tests run against
# (CI also takes it from the target branch). A PR that changes them is rejected without looking
# at its check run.
PROTECTED = (".github/", "app/modules/temp_alarm/", "tests/l2_counter/")
BOT_LOGINS = {"github-actions[bot]"}


def log(msg):
    print(msg, file=sys.stderr)


# ---------------------------------------------------------------- inputs

def task_of(branch):
    m = TASK_RE.match(branch or "")
    return m.group(1) if m and m.group(1) in TASKS else None


def find_pr(repo, run, pr_number=None):
    """The open PR whose head is the check run's commit, or None."""
    if pr_number:
        return gh.get(f"repos/{repo}/pulls/{pr_number}")
    sha = run["head_sha"]
    candidates = [p["number"] for p in run.get("pull_requests") or []]
    if not candidates:  # always empty for PRs from forks
        owner = run["head_repository"]["owner"]["login"]
        candidates = [p["number"] for p in gh.get_all(
            f"repos/{repo}/pulls", state="open", head=f"{owner}:{run['head_branch']}")]
    for n in candidates:
        pr = gh.get(f"repos/{repo}/pulls/{n}")
        if pr["head"]["sha"] == sha and pr["base"]["repo"]["full_name"] == repo:
            return pr
    return None


def load_result(artifacts, task):
    """result.json from the check run, or (None, why)."""
    path = Path(artifacts) / "homework-result" / "result.json"
    if not path.is_file():
        matches = list(Path(artifacts).rglob("result.json"))
        path = matches[0] if len(matches) == 1 else path
    if not path.is_file():
        return None, "The checks didn't produce a result for this commit."
    try:
        if path.stat().st_size > 1_000_000:
            raise ValueError("too large")
        result = json.loads(path.read_text())
        if not isinstance(result, dict):
            raise ValueError("not an object")
    except (OSError, ValueError) as e:
        return None, f"The checks' result can't be read ({e})."
    if result.get("task") != task:
        return None, f"The checks ran for `{result.get('task')}`, not `{task}`."
    if result.get("status") not in ("approved", "changes_needed", "under_review", "error", "not_checked"):
        return None, "The checks' result has an unknown status."
    return result, None


def bot_reviews(repo, number):
    out = []
    for r in gh.get_all(f"repos/{repo}/pulls/{number}/reviews"):
        m = MARKER_RE.search(r.get("body") or "")
        if m and (r.get("user") or {}).get("login") in BOT_LOGINS:
            out.append({"sha": m.group(1), "task": m.group(2), "status": m.group(3),
                        "ai": m.group(4) == "1", "at": r.get("submitted_at")})
    return out


def diff_lines(pr_files):
    """{path: set of new-side line numbers GitHub accepts inline comments on}."""
    out = {}
    for f in pr_files:
        lines, new = set(), 0
        for row in (f.get("patch") or "").splitlines():
            m = re.match(r"@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@", row)
            if m:
                new = int(m.group(1))
                continue
            if row.startswith("-"):
                continue
            if row.startswith(("+", " ")):
                lines.add(new)
                new += 1
        out[f["filename"]] = lines
    return out


# ---------------------------------------------------------------- decision

def protected_files(pr_files):
    """The PR's files under PROTECTED (either side of a rename), in the PR's order."""
    out = []
    for f in pr_files:
        for name in (f.get("previous_filename"), f["filename"]):
            if name and name.startswith(PROTECTED) and name not in out:
                out.append(name)
    return out


def protected_review(touched, base):
    """(summary, changes) for a PR that changes PROTECTED files."""
    folders = ", ".join(f"`{p}`" for p in PROTECTED)
    summary = (f"This pull request changes files the checks rely on ({folders}), so its checks can't be "
               f"trusted and weren't read. Undo those changes, e.g. `git checkout {base} -- <file>` "
               "(or `git rm` a file you added), and push again.")
    changes = [{"message": f"Undo your change to `{name}`: the checks rely on it."} for name in touched]
    return summary, changes


def decide(task, result, result_problem):
    """Returns (status, summary, changes, reason, routine). routine: an expected instructor review
    (a rule the scripts can't settle), not a problem worth a mention."""
    if result is None:  # the check run broke before the checker wrote its result: CI's problem, not the student's
        return ("retry", "The checks couldn't run on this commit because of a problem on our side, not in your "
                "code. You don't need to do anything: an instructor will run them again and the review will "
                "appear here.", [], result_problem, False)
    status = result["status"]
    if status == "error":  # the checker stopped on a CI problem (a missing tool, a run that didn't finish)
        msg = (result.get("error") or {}).get("message", "The checks could not evaluate this commit.")
        return ("retry", "The checks couldn't run on this commit because of a problem on our side, not in your "
                "code. You don't need to do anything: an instructor will run them again and the review will "
                "appear here.", [], msg, False)
    if status == "changes_needed":
        return ("changes_needed", "The automatic checks found something to change.",
                result.get("changes") or [], "", False)
    if status == "under_review":  # the scripts passed what they could decide; the rest is the instructor's
        unsure = [c.get("label", "") for c in result.get("checks") or [] if c.get("ok") is None]
        return ("under_review", "The automatic checks passed everything they can decide. An instructor will check "
                "the rest; you don't need to do anything.", [],
                "Not decided by the checks: " + "; ".join(unsure) + ".", True)
    if status == "approved":
        return ("approved", "All automatic checks passed.", [], "", False)
    return ("under_review", "This task has no automatic checks yet. An instructor will review it.",
            [], "No checks for this task.", False)


# ---------------------------------------------------------------- output

HEADINGS = {
    "approved": "✅ {task} approved",
    "changes_needed": "❌ {task}: changes needed",
    "under_review": "👀 {task}: an instructor will review this",
    "retry": "⏳ {task}: the checks couldn't run",
}


def where(c):
    if not c.get("path"):
        return ""
    return f" (`{c['path']}" + (f":{c['line']}" if c.get("line") else "") + "`)"


def compose(task, sha, status, summary, changes, reason, result, mention, run_url=None):
    """mention: instructors to @-mention (a list), for problems only."""
    lines = [MARKER.format(sha=sha, task=task or "-", status=status),
             f"## {HEADINGS[status].format(task=task or 'Submission')}", "", summary, ""]
    if changes:
        lines += ["### What to change", ""]
        lines += [f"{i}. {c['message']}{where(c)}" for i, c in enumerate(changes, 1)]
        lines.append("")
    if status == "under_review" and mention:
        who = " ".join(f"@{m}" for m in mention)
        lines += [f"{who}, please review this by hand: {reason or 'see above.'}", ""]
    if status == "retry" and mention:
        who = " ".join(f"@{m}" for m in mention)
        run = f" ([check run]({run_url}))" if run_url else ""
        lines += [f"{who}, the checks couldn't run{run}: {reason or 'see the run.'} Fix the cause on the "
                  "target branch, then re-run the failed jobs; this commit gets its review then.", ""]
    if result:
        checks = result.get("checks") or []
        passed = sum(1 for c in checks if c.get("ok") is True)
        unsure = sum(1 for c in checks if c.get("ok") is None)
        label = (f"Automatic checks ({passed}/{len(checks)} passed" + (f", {unsure} for an instructor)" if unsure else ")")
                 if checks else "Automatic checks")
        lines += [f"<details><summary>{label}</summary>", "",
                  render_markdown(result, heading=4), "</details>", ""]
    if status == "approved":
        lines.append(f"Reviewed commit {sha[:7]}. Nothing else to do for this task; "
                     "this pull request will be closed.")
    elif status == "retry":
        lines.append(f"Commit {sha[:7]}. No need to push again.")
    else:
        lines.append(f"Reviewed commit {sha[:7]}. Push a new commit to this branch "
                     "to get a new review.")
    return "\n".join(lines)


def post(repo, pr, sha, body, changes, pr_files, dry_run):
    commentable = diff_lines(pr_files)
    comments = [{"path": c["path"], "line": c["line"], "side": "RIGHT", "body": c["message"]}
                for c in changes
                if c.get("path") and c.get("line") in commentable.get(c["path"], ())]
    payload = {"commit_id": sha, "event": "COMMENT", "body": body, "comments": comments}
    if dry_run:
        log(f"[dry-run] review on #{pr['number']} with {len(comments)} inline comments:\n{body}")
        return
    try:
        gh.post(f"repos/{repo}/pulls/{pr['number']}/reviews", payload)
    except gh.GitHubError as e:
        if e.status != 422 or not comments:
            raise
        log(f"Inline comments rejected ({e}); posting the review without them.")
        payload["comments"] = []
        gh.post(f"repos/{repo}/pulls/{pr['number']}/reviews", payload)


def set_labels(repo, pr, task, status, dry_run):
    wanted = {LABELS[status][0]} | ({task} if task else set())
    managed = {v[0] for v in LABELS.values()}
    current = {lab["name"] for lab in pr.get("labels") or []}
    remove = (current & managed) - wanted
    add = wanted - current
    if dry_run:
        log(f"[dry-run] labels: add {sorted(add)}, remove {sorted(remove)}")
        return
    for name in add:
        try:
            gh.get(f"repos/{repo}/labels/{name}")
        except gh.GitHubError as e:
            if e.status != 404:
                raise
            spec = next((v for v in LABELS.values() if v[0] == name), (name, TASK_LABEL_COLOR, f"Homework {name}"))
            try:
                gh.post(f"repos/{repo}/labels", {"name": spec[0], "color": spec[1], "description": spec[2]})
            except gh.GitHubError as e2:
                if e2.status != 422:  # created meanwhile
                    raise
    for name in remove:
        try:
            gh.delete(f"repos/{repo}/issues/{pr['number']}/labels/{name}")
        except gh.GitHubError as e:
            if e.status != 404:
                raise
    if add:
        gh.post(f"repos/{repo}/issues/{pr['number']}/labels", {"labels": sorted(add)})


def request_review(repo, pr, instructors, dry_run):
    """Ask the instructors to review the PR: it shows in their GitHub review queue and notifies them."""
    who = [i for i in instructors if i.lower() != ((pr.get("user") or {}).get("login") or "").lower()]
    if not who:
        return
    if dry_run:
        log(f"[dry-run] request review from {who}")
        return
    # One request per instructor: GitHub rejects the whole list when one of them can't review this repository.
    for login in who:
        try:
            gh.post(f"repos/{repo}/pulls/{pr['number']}/requested_reviewers", {"reviewers": [login]})
        except gh.GitHubError as e:  # not a collaborator, or already reviewing: the label and mention still mark it
            log(f"Couldn't request a review from {login}: {e}")


# ---------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--artifacts", required=True, help="folder with the check run's artifacts")
    ap.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"))
    ap.add_argument("--event", default=os.environ.get("GITHUB_EVENT_PATH"),
                    help="workflow_run event JSON (default: the current event)")
    ap.add_argument("--pr", type=int, help="review this PR instead of finding it from the event")
    ap.add_argument("--dry-run", action="store_true", help="print the review; post nothing")
    args = ap.parse_args()

    # "matiflp, carlassaraf", "@matiflp @carlassaraf" or one per line: commas, spaces and newlines all separate.
    instructors = list(dict.fromkeys(i.lstrip("@") for i in re.split(r"[,\s]+", os.environ.get("MANUAL_REVIEWER") or "")
                                     if i.lstrip("@")))
    close_approved = os.environ.get("CLOSE_APPROVED", "true") != "false"

    run = {}
    if args.event and Path(args.event).is_file():
        run = json.loads(Path(args.event).read_text()).get("workflow_run") or {}
    if not run and not args.pr:
        log("No workflow_run event and no --pr; nothing to review.")
        return 1

    pr = find_pr(args.repo, run, args.pr)
    if pr is None:
        log("No open PR has this commit as its head (closed, or a newer push); nothing to post.")
        return 0
    if pr["state"] != "open":
        log(f"PR #{pr['number']} is {pr['state']}; nothing to post.")
        return 0
    sha = pr["head"]["sha"]
    if run and run.get("head_sha") != sha:
        log("The PR head moved since this check run; the newer run will review it.")
        return 0

    reviews = bot_reviews(args.repo, pr["number"])
    if any(r["sha"] == sha and r["status"] != "retry" for r in reviews):
        log(f"Commit {sha[:7]} was already reviewed on PR #{pr['number']}.")
        return 0
    retried = any(r["sha"] == sha for r in reviews)  # a "retry" review is already there

    task = task_of(pr["head"]["ref"])
    pr_files = gh.get_all(f"repos/{args.repo}/pulls/{pr['number']}/files")
    if task is None:
        body = compose(None, sha, "changes_needed",
                       f"`{pr['head']['ref']}` is not a task name, so no checks ran.",
                       [{"message": "Push your work to a branch named after the task, e.g. `l8-task1`, "
                                    "and open a pull request from that branch."}],
                       "", None, None)
        post(args.repo, pr, sha, body, [], pr_files, args.dry_run)
        set_labels(args.repo, pr, None, "changes_needed", args.dry_run)
        return 0

    base = BASES.get(task, "main")
    if pr["base"]["ref"] != base:  # the checks need the task's starter code and the course's module
        body = compose(task, sha, "changes_needed",
                       f"This pull request targets `{pr['base']['ref']}`, but `{task}` is reviewed against "
                       f"`{base}`, so its checks weren't read.",
                       [{"message": f"Close this pull request and open a new one from the same branch with "
                                    f"`{base}` as the base branch."}],
                       "", None, None)
        post(args.repo, pr, sha, body, [], pr_files, args.dry_run)
        set_labels(args.repo, pr, task, "changes_needed", args.dry_run)
        return 0

    touched = protected_files(pr_files)
    if touched:  # rejected whatever its check run says: that run may have used the PR's own checks
        summary, changes = protected_review(touched, base)
        body = compose(task, sha, "changes_needed", summary, changes, "", None, None)
        post(args.repo, pr, sha, body, [], pr_files, args.dry_run)
        set_labels(args.repo, pr, task, "changes_needed", args.dry_run)
        return 0

    result, problem = load_result(args.artifacts, task)
    if result is None and run.get("conclusion") == "cancelled":
        log("The check run was cancelled; nothing to post.")
        return 0

    status, summary, changes, reason, routine = decide(task, result, problem)
    if status == "retry" and retried:
        log(f"The checks left no result again for {sha[:7]}; the \"retry\" review is already posted.")
        return 0
    mention = None if routine else instructors
    body = compose(task, sha, status, summary, changes, reason, result, mention, run.get("html_url"))
    post(args.repo, pr, sha, body, changes, pr_files, args.dry_run)
    set_labels(args.repo, pr, task, status, args.dry_run)
    if status in ("under_review", "retry"):
        request_review(args.repo, pr, instructors, args.dry_run)
    if status == "approved" and close_approved:
        if args.dry_run:
            log(f"[dry-run] close PR #{pr['number']}")
        else:
            gh.patch(f"repos/{args.repo}/pulls/{pr['number']}", {"state": "closed"})

    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a") as f:
            f.write(f"PR #{pr['number']} ({task}, {sha[:7]}): **{status}**\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
