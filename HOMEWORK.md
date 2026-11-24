# Submitting homework

Each task is a branch in your fork, named after the task (`lX-taskY`, e.g. `l8-task1`). You
submit a task by opening a pull request from its branch to `iomico-public/zephyr-intermediate`.
Automatic checks build and run your code, and a reviewer bot comments on the pull request with
what to change. When the task is approved, the bot closes the pull request. Nothing is ever merged.

The names, files and log lines the checks read are in [TASKS.md](TASKS.md).

## Once: fork and clone

Fork `iomico-public/zephyr-intermediate` on GitHub. **Uncheck "Copy the `main` branch only"**:
the starter branches (`l8-demo-homework`, ...) must be in your fork too. Then clone your fork into
your west workspace, as shown in the course.

## Start a task

Each task's branch starts from the task's starter branch, or from `main` when there is none. The
lecture 8 tasks build on each other, so each one starts from the previous task's branch:

| Task | Start from | Pull request base |
| --- | --- | --- |
| `l1-task1`, `l2-task1`, `l4-task1`, `l5-task1` | `main` | `main` |
| `l3-task1` | `l3-homework` | `l3-homework` |
| `l6-task1` | `l6-homework` | `l6-homework` |
| `l7-task1` | `l7-homework` | `l7-homework` |
| `l8-task0` | `l8-demo-homework` | `l8-demo-homework` |
| `l8-task1` | your `l8-task0` | `l8-demo-homework` |
| `l8-task2` | your `l8-task1` | `l8-demo-homework` |
| `l8-task3` | your `l8-task2` | `l8-demo-homework` |

```sh
git switch l3-homework
git switch -c l3-task1
```

## Submit a task

1. Do the task on its branch, commit, and push the branch to your fork:

   ```sh
   git add -A
   git commit -m "l3-task1: replace polling with a work item"
   git push -u origin l3-task1
   ```

2. On GitHub, open a pull request from `<you>/zephyr-intermediate:l3-task1` to
   `iomico-public/zephyr-intermediate:l3-homework`. Check the base branch: GitHub suggests `main`,
   and tasks with a starter branch are reviewed against it (see the table above). Fill in the
   template.

3. Wait for the review. The checks take a few minutes. The bot then posts a review on the pull
   request:

   - ✅ **approved**: the task is done. The bot closes the pull request.
   - ❌ **changes needed**: the review lists what to change, most important first, with the file
     and line where it can. Some items also show as comments on the lines in "Files changed".
     Fix them as shown below.
   - 👀 **an instructor will review this**: the checks couldn't decide everything. An instructor
     looks at it; you don't need to do anything.
   - ⏳ **the checks couldn't run**: a problem on our side, not in your code. An instructor runs them
     again and the review appears on the same pull request; you don't need to push anything.

Your first pull request may wait for an instructor to approve running the checks. That happens
once for new GitHub accounts.

## Fix a task

Commit the fix on the task's own branch and push it again:

```sh
git switch l8-task1          # if you're not on it already
git add -A
git commit -m "l8-task1: check the sensor channel"
git push
```

The same pull request gets a new review. Don't open a new pull request.

## Good to know

- The L8 checks run your tests three ways: as they are, with coverage (`l8-task3`), and against
  broken copies of `temp_alarm.c`. A test that still passes when the module has a bug doesn't check
  what `TEST_SPEC.md` asks; the review then says which check is missing.
- Don't change files in `.github/`, `tests/l2_counter/` or `app/modules/temp_alarm/`. A pull
  request that does gets no automatic review. The checks always use the course's versions.
- Results also show in the pull request's "Checks" tab, with the full log.
