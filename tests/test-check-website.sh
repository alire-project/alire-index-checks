#!/bin/bash

# Self-contained tests for scripts/check-website.py. See lib.sh.

. "$( dirname -- "${BASH_SOURCE[0]}" )/lib.sh"

# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT=$SCRIPTS_DIR/check-website.py

function base_files() {
    mkdir -p index/he/hello
    manifest hello 1.0.0 > index/he/hello/hello-1.0.0.toml # No website
}

function with_website() {
    # Manifest for hello version $1 with website $2
    manifest hello "$1" | sed "/^description/a website = \"$2\""
}

# output_has "description" pattern ; checks the output of the last run
function output_has() {
    if grep -q "$2" "$WORK/out"; then
        echo "ok   - $1"
    else
        echo "FAIL - $1 (no '$2' in the output)"
        sed 's/^/       /' "$WORK/out"
        failures=$((failures + 1))
    fi
}

# --- PR changes

new_repo
with_website 1.1.0 https://example.com > index/he/hello/hello-1.1.0.toml
pr_commit
expect pass "new release with a website"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
pr_commit
expect fail "new release without a website"
output_has "the git repository is suggested" \
    'website = "https://github.com/someone/hello"'
output_has "the upstream repository is advised" "upstream repository"

new_repo
with_website 1.1.0 "  " > index/he/hello/hello-1.1.0.toml
pr_commit
expect fail "new release with a blank website"

new_repo
manifest hello 1.1.0 \
    | sed 's|^url = .*|url = "https://example.com/hello-1.1.0.tgz"|' \
    | sed 's|^commit = .*|hashes = ["sha512:00"]|' \
    > index/he/hello/hello-1.1.0.toml
pr_commit
expect fail "new release from an archive without a website"
if grep -q "can serve" "$WORK/out"; then
    echo "FAIL - no repository is suggested for an archive"
    failures=$((failures + 1))
else
    echo "ok   - no repository is suggested for an archive"
fi

new_repo
mkdir -p index/ma/make
external make > index/ma/make/make-external.toml
pr_commit
expect pass "new external without a website"

new_repo
sed -i '1i available = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal of a release without a website"

new_repo
git rm -q index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "deletion is left to the other checks"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
pr_commit
DRY_RUN=1 expect pass "missing website with DRY_RUN reports but passes"

# --- ALL_CRATES: every manifest in the index is checked as if added

new_repo
pr_commit
expect pass "no changes, no releases checked"
ALL_CRATES=1 expect fail "ALL_CRATES checks existing releases"

finish
