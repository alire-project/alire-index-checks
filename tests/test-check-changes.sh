#!/bin/bash

# Self-contained tests for scripts/check-changes.py. Each case creates a
# throwaway repository with a base commit and a "PR" commit on top, mimicking
# the merge commit checked out by CI (HEAD~1 is the base), and checks whether
# the script accepts or rejects the change.

set -o errexit
set -o nounset

SCRIPT=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/.." &> /dev/null && pwd )/scripts/check-changes.py
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

failures=0

function manifest() {
    # Minimal manifest for crate $1 version $2
    cat <<EOF
name = "$1"
version = "$2"
description = "Test crate"
maintainers-logins = ["someone"]

[origin]
url = "git+https://github.com/someone/$1.git"
commit = "0123456789abcdef0123456789abcdef01234567"
EOF
}

function new_repo() {
    rm -rf "$WORK/repo"
    mkdir -p "$WORK/repo"
    cd "$WORK/repo"
    git init -q
    git config user.email test@example.com
    git config user.name test
    mkdir -p index/he/hello index/li/libhello .github
    echo 'version = "1.4.0"' > index/index.toml
    manifest hello 1.0.0 > index/he/hello/hello-1.0.0.toml
    manifest libhello 1.0.0 > index/li/libhello/libhello-1.0.0.toml
    echo "# CI" > .github/README
    git add -A
    git commit -q -m base
}

function pr_commit() {
    git add -A
    git commit -q -m pr --allow-empty
}

# expect pass|fail "description" ; the PR commit must be in place
function expect() {
    local expected=$1
    local description=$2
    local actual
    if "$SCRIPT" > "$WORK/out" 2>&1; then
        actual=pass
    elif grep -q "^FAILED:" "$WORK/out"; then
        actual=fail
    else
        actual=crash # Failed without the expected diagnostic
    fi
    if [[ $actual == "$expected" ]]; then
        echo "ok   - $description"
    else
        echo "FAIL - $description (expected $expected, got $actual)"
        sed 's/^/       /' "$WORK/out"
        failures=$((failures + 1))
    fi
}

# --- Accepted changes

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
pr_commit
expect pass "new release of existing crate"

new_repo
mkdir -p index/wo/world
manifest world 0.1.0 > index/wo/world/world-0.1.0.toml
pr_commit
expect pass "first release of new crate"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
manifest hello 1.2.0-rc1 > index/he/hello/hello-1.2.0-rc1.toml
pr_commit
expect pass "several releases of the same crate"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
manifest libhello 1.1.0 > index/li/libhello/libhello-1.1.0.toml
pr_commit
expect pass "releases of two crates"

new_repo
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal with available = false"

new_repo
sed -i '3a available = true' index/he/hello/hello-1.0.0.toml
git commit -q -am "base with available = true"
sed -i 's/available = true/available = false/' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal replacing available = true"

new_repo
sed -i '3a # Withdrawn due to bug\navailable = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal with an explanatory comment"

new_repo
echo "# comment" >> index/li/libhello/libhello-1.0.0.toml
pr_commit
expect pass "comment-only libhello modification"

new_repo
sed -i 's/^name = "libhello"/name   =   "libhello"/' index/li/libhello/libhello-1.0.0.toml
pr_commit
expect pass "formatting-only libhello modification"

new_repo
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
sed -i 's/^name = "hello"/name   =   "hello" # reformatted/' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal with formatting changes"

new_repo
pr_commit
expect pass "empty PR"

# --- Rejected changes

new_repo
git rm -q index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "deleted manifest"

new_repo
git mv index/he/hello/hello-1.0.0.toml index/he/hello/hello-1.0.1.toml
pr_commit
expect fail "renamed manifest"

new_repo
sed -i 's/Test crate/Changed/' index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "modified manifest"

new_repo
sed -i 's/Test crate/Changed/' index/he/hello/hello-1.0.0.toml
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "withdrawal mixed with other changes"

new_repo
echo 'available = false' >> index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "available = false inside a table (not top-level)"

new_repo
sed -i '3a available = true' index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "setting available = true"

new_repo
echo "# comment" >> index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "comment-only modification of a crate other than libhello"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
echo "# changed" > .github/README
pr_commit
expect fail "change outside index/"

new_repo
echo 'version = "1.5.0"' > index/index.toml
pr_commit
expect fail "index metadata change"

new_repo
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
git commit -q -am "base already withdrawn"
echo "# comment" >> index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "modification of an already withdrawn release"

new_repo
echo 'name = "hello' > index/he/hello/hello-1.1.0.toml
pr_commit
expect fail "added manifest with invalid TOML"

new_repo
echo "notes" > index/he/hello/README.md
pr_commit
expect fail "non-manifest file under index/"

new_repo
mkdir -p index/xx/hello
manifest hello 1.1.0 > index/xx/hello/hello-1.1.0.toml
pr_commit
expect fail "manifest in wrong shelf"

new_repo
manifest libhello 1.1.0 > index/he/hello/libhello-1.1.0.toml
pr_commit
expect fail "manifest in another crate's folder"

new_repo
ln -s hello-1.0.0.toml index/he/hello/hello-1.1.0.toml
pr_commit
expect fail "added symlink"

new_repo
manifest hello 1.1.0 > "index/he/hello/hello-1.1.0 x.toml"
pr_commit
expect fail "file name with a space"

echo
if [[ $failures -gt 0 ]]; then
    echo "$failures test(s) failed"
    exit 1
fi
echo "All tests passed"
