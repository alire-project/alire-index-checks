#!/bin/bash

# Self-contained tests for scripts/check-crate-name.sh. See lib.sh.

. "$( dirname -- "${BASH_SOURCE[0]}" )/lib.sh"

# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT=$SCRIPTS_DIR/check-crate-name.sh
# shellcheck disable=SC2034 # Used by lib.sh
FAIL_PATTERN="^REVIEW:"

function base_files() {
    mkdir -p index/he/hello index/li/libhello
    manifest hello 1.0.0 > index/he/hello/hello-1.0.0.toml
    manifest libhello 1.0.0 > index/li/libhello/libhello-1.0.0.toml
}

function new_crate() {
    mkdir -p "index/${1:0:2}/$1"
    manifest "$1" 0.1.0 > "index/${1:0:2}/$1/$1-0.1.0.toml"
}

# --- PR changes

new_repo
new_crate world
pr_commit
expect pass "new crate with an acceptable name"

for name in libworld adaworld sparkworld my_ada_lib my_spark_lib; do
    new_repo
    new_crate $name
    pr_commit
    expect fail "new crate named $name needs review"
done

new_repo
manifest libhello 1.1.0 > index/li/libhello/libhello-1.1.0.toml
pr_commit
expect pass "new release of existing crate with flagged name"

new_repo
new_crate libworld
pr_commit
DRY_RUN=1 expect pass "flagged name with DRY_RUN reports but passes"

# --- ALL_CRATES: every crate in the index is checked as if new

new_repo
pr_commit
expect pass "no changes, no crates checked"
ALL_CRATES=1 expect fail "ALL_CRATES flags existing crate libhello"
ALL_CRATES=1 DRY_RUN=1 expect pass "ALL_CRATES with DRY_RUN reports but passes"

function base_files() {
    mkdir -p index/he/hello
    manifest hello 1.0.0 > index/he/hello/hello-1.0.0.toml
    manifest hello 2.0.0 > index/he/hello/hello-2.0.0.toml
}

new_repo
pr_commit
ALL_CRATES=1 expect pass "ALL_CRATES on an index with acceptable names"

finish
