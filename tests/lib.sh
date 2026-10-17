#!/bin/bash

# Harness shared by the self-contained script tests. Each case creates a
# throwaway repository with a base commit and a "PR" commit on top, mimicking
# the merge commit checked out by CI (HEAD~1 is the base), and checks whether
# the script under test accepts or rejects the change.
#
# Test scripts must set SCRIPT (and optionally SCRIPT_ARGS), define a
# base_files function that populates the base commit, and end with `finish`.

set -o errexit
set -o nounset

# shellcheck disable=SC2034 # Used by the sourcing test scripts
SCRIPTS_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )/../scripts" &> /dev/null && pwd )
SCRIPT_ARGS=()
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

failures=0

function manifest() {
    # Minimal manifest for crate $1 version $2, maintained by logins $3
    # (a comma-separated list of quoted strings, defaults to "someone")
    cat <<EOF
name = "$1"
version = "$2"
description = "Test crate"
maintainers-logins = [${3:-\"someone\"}]

[origin]
url = "git+https://github.com/someone/$1.git"
commit = "0123456789abcdef0123456789abcdef01234567"
EOF
}

function external() {
    # Minimal external manifest for crate $1, maintained by logins $2
    cat <<EOF
name = "$1"
description = "Test external"
maintainers-logins = [${2:-\"someone\"}]

[[external]]
kind = "hint"
EOF
}

function new_repo() {
    rm -rf "$WORK/repo"
    mkdir -p "$WORK/repo"
    cd "$WORK/repo"
    git init -q
    git config user.email test@example.com
    git config user.name test
    mkdir -p index
    echo 'version = "1.4.0"' > index/index.toml
    base_files
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
    if "$SCRIPT" "${SCRIPT_ARGS[@]}" > "$WORK/out" 2>&1; then
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

function finish() {
    echo
    if [[ $failures -gt 0 ]]; then
        echo "$failures test(s) failed"
        exit 1
    fi
    echo "All tests passed"
}
