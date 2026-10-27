# alire-index-checks

Checks on submissions to Alire indexes.

The scripts in `scripts/` are run by the workflows of
[alire-index](https://github.com/alire-project/alire-index) on every
submission. alire-index checks out this repository at the branch matching the
PR target, so changes merged here take effect there immediately.

## Auto-merge eligibility

`scripts/automerge.py` decides whether a PR may be merged without a manual
review, as described in
[AEP-6](https://github.com/alire-project/alire/blob/master/doc/AEPs/aep-0006-auto-merge.md):
new releases of existing crates, by their maintainers, keeping the properties
that only a manual review may change, or withdrawals. It is meant for the
auto-merge bot, not for a check on every PR, so it has no mirror workflow.

The bot also requires every release to build (or, for binary and system
crates, to be fetched) on at least one platform, as builds skipped for missing
dependencies or unavailable releases still succeed. `gh-build-crate.sh`
reports the outcome of each crate in a single `alire-build` annotation, e.g.
`built hello=2.1.0;skipped world=1.0.0 (missing dependencies)`.

`scripts/check-automerge.py <login>` reports the same from the command
line. With `ALL_CRATES` it replays the history of an index instead, comparing
every release with the previous one, and prints how many would have been
eligible and which properties changed in the rest:

```
cd <alire-index checkout>
ALL_CRATES=1 DRY_RUN=1 <alire-index-checks>/scripts/check-automerge.py
```

`scripts/automerge-bot.py` is the bot itself, run periodically by
alire-index. For each open PR, it merges the head locally with the base branch
in a bare repository, evaluates it with `automerge.py`, and then requires no
veto (the `auto-merge: veto` label, or changes requested by a maintainer),
passing checks, a successful build of every release, one hour since the last
push or the PR leaving draft (except withdrawals), and at most 10 releases by
the author merged automatically in the last day. In `dry-run` mode it only
logs its decisions to a tracking issue; in `on` mode it labels, comments and
merges. Its offline tests are in `tests/test_automerge_bot.py`.

## Mirror workflows

Every check run by alire-index has a **mirror workflow** in this repository,
which runs the same script on PRs to this repository. Mirrors double-check
that the scripts still do what they should, against the sample index in
`index/`. That index contains a few real crates of different kinds (regular
releases, externals, toolchains).

Mirrors are named after their sibling in alire-index:

- file: `.github/workflows/mirror-<alire-index file name>.yml`
- workflow name: `Mirror: <alire-index workflow name>`
- job name: `Mirror: <alire-index job name>`, e.g. `Mirror: Author` for
  `check / Author`. Job names are what branch protection matches as required
  checks, so they must be unique. Build matrix jobs are not required directly:
  the build mirror's summary job `Mirror: native / all` stands for the whole
  matrix, mirroring alire-index (see its `.github/WORKFLOWS.md`). Renaming a
  required job still requires updating the branch protection settings.

| alire-index workflow | Mirror |
|---|---|
| `build-native.yml` (Build Crate (Alire toolchain)) | `mirror-build-native.yml` |
| `check-authorship.yml` (Check Author) | `mirror-check-authorship.yml` |
| `check-changes.yml` (Check Files) | `mirror-check-changes.yml` |
| `check-crate-name.yml` (Check Crate Name) | `mirror-check-crate-name.yml` |
| `check-index.yml` (Check Index) | `mirror-check-index.yml` |
| `check-schema.yml` (Check Schema) | `mirror-check-schema.yml` |
| `diff-release.yml` (Diff release) | `mirror-diff-release.yml` |

Not mirrored:

- `build-distros.yml`, `build-native-master.yml` and `build-ports.yml`. They
  run the same `gh-build-crate.sh` as `build-native.yml`, only on more
  platforms.
- `check-with-script.yml`, a reusable workflow used by the checks.
- `cleaner.yml`, which closes stale PRs.

Other workflows here are not mirrors:

- `website.yml` runs `check-website.sh`, which alire-index does not use yet.
  Rename it as a mirror when it is.
- `selftest.yml` (`Self-test`) runs the offline tests in `tests/`, which
  exercise the scripts on crafted throwaway repositories. They complement
  mirrors, they do not replace them.

### `ALL_CRATES` and `DRY_RUN`

Scripts normally check only the manifests changed by the PR, by comparing the
PR merge commit (`HEAD`) with the base commit (`HEAD~1`). When mirrors run
them, the PR changes scripts rather than manifests, so mirrors set these
environment variables:

- `ALL_CRATES`: treat every manifest in `index/` (`*-*.toml`, except
  `index.toml`) as changed by the PR, as if just added. In Python scripts this
  is handled by `changed_files()` in `common.py`. In shell scripts, it is
  handled by `changed_manifests` in `common.sh`.
- `DRY_RUN`: report problems but do not fail. Use it in mirrors whose outcome
  depends on who opens the PR, such as the author check: the PR author is not
  a maintainer of every sample crate. In Python scripts this is handled by
  `report()` in `common.py`, and in shell scripts by `fail` in `common.sh`.

Mirrors must not set `DRY_RUN` when the sample index is expected to pass, so
that a broken script is noticed. For the same reason, mirrors differ from their
siblings in two ways:

- They have no `paths:` filter, as they must run on PRs that change only
  scripts.
- They do not ignore the script exit status (`|| true`).

Scripts must honour both variables on every path:

- With `ALL_CRATES`, nothing may be skipped as "not changed by the PR". For
  example, the crate name check treats every crate as new.
- With `DRY_RUN`, every failure must go through `fail`/`report()`, not
  `exit 1`.

### Adding a check

1. Add the script to `scripts/`, supporting `ALL_CRATES` (and `DRY_RUN` when
   it can fail) through the helpers above.
2. Add offline tests to `tests/` and a job to `selftest.yml`.
3. Add the mirror workflow here, named as described above.
4. Once merged here, add the calling workflow to alire-index.
