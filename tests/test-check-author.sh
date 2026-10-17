#!/bin/bash

# Self-contained tests for scripts/check-author.py. See lib.sh.

. "$( dirname -- "${BASH_SOURCE[0]}" )/lib.sh"

# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT=$SCRIPTS_DIR/check-author.py

function base_files() {
    # Latest release is 2.0.0 (bob); alice and carol maintain older ones
    mkdir -p index/he/hello
    manifest hello 1.0.0 '"alice"' > index/he/hello/hello-1.0.0.toml
    manifest hello 2.0.0-rc1 '"carol"' > index/he/hello/hello-2.0.0-rc1.toml
    manifest hello 2.0.0 '"bob", "other"' > index/he/hello/hello-2.0.0.toml

    # Latest release is 10.0.0 (erin), which sorts before 9.0.0 as text
    mkdir -p index/wo/world
    manifest world 9.0.0 '"dave"' > index/wo/world/world-9.0.0.toml
    manifest world 10.0.0 '"erin"' > index/wo/world/world-10.0.0.toml

    # Crate with only an external manifest (frank)
    mkdir -p index/cu/curl
    external curl '"frank"' > index/cu/curl/curl-external.toml

    # Crate with a release (gina) and an external (henry): the release rules
    mkdir -p index/li/libfoo
    manifest libfoo 1.0.0 '"gina"' > index/li/libfoo/libfoo-1.0.0.toml
    external libfoo '"henry"' > index/li/libfoo/libfoo-external.toml

    # Crate whose latest releases tie, as build metadata is ignored
    mkdir -p index/ti/tie
    manifest tie 2.0.0 '"ivan"' > index/ti/tie/tie-2.0.0.toml
    manifest tie 2.0.0+x '"judy"' > index/ti/tie/tie-2.0.0+x.toml

    # Crate with a tie below its latest release (lena)
    mkdir -p index/ti/tied
    manifest tied 1.0.0 '"mallory"' > index/ti/tied/tied-1.0.0.toml
    manifest tied 1.0.0+x '"mallory"' > index/ti/tied/tied-1.0.0+x.toml
    manifest tied 2.0.0 '"lena"' > index/ti/tied/tied-2.0.0.toml

    # Crate with a version that is not strict semver
    mkdir -p index/od/odd
    manifest odd 1.0 '"kate"' > index/od/odd/odd-1.0.toml
}

function as() {
    SCRIPT_ARGS=("$1")
}

# --- Authorised changes

new_repo
manifest hello 2.1.0 '"bob"' > index/he/hello/hello-2.1.0.toml
pr_commit
as bob
expect pass "maintainer of latest release adds a release"
as BoB
expect pass "login comparison is case-insensitive"
as other
expect pass "any maintainer of latest release may add a release"

new_repo
manifest world 11.0.0 '"erin"' > index/wo/world/world-11.0.0.toml
pr_commit
as erin
expect pass "latest release found by numeric ordering"

new_repo
manifest curl 1.0.0 '"frank"' > index/cu/curl/curl-1.0.0.toml
pr_commit
as frank
expect pass "first release of external-only crate, by external maintainer"

new_repo
echo '# edited' >> index/cu/curl/curl-external.toml
pr_commit
as frank
expect pass "external modified by its maintainer"

new_repo
echo '# edited' >> index/li/libfoo/libfoo-external.toml
pr_commit
as gina
expect pass "external modified by maintainer of latest release"

new_repo
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
pr_commit
as bob
expect pass "withdrawal of older release by current maintainer"

new_repo
mkdir -p index/ne/newcrate
manifest newcrate 0.1.0 '"mallory"' > index/ne/newcrate/newcrate-0.1.0.toml
pr_commit
as mallory
expect pass "new crate is skipped (manual review)"

new_repo
echo "notes" > README.md
pr_commit
as mallory
expect pass "no manifest changes"

# --- Unauthorised changes

new_repo
manifest hello 2.1.0 '"alice"' > index/he/hello/hello-2.1.0.toml
pr_commit
as alice
expect fail "maintainer of an older release only"
as carol
expect fail "maintainer of a pre-release of the latest version only"
as mallory
expect fail "unrelated user"

new_repo
manifest hello 0.0.1 '"mallory"' > index/he/hello/hello-0.0.1.toml
pr_commit
as mallory
expect fail "release lower than any existing one"

new_repo
manifest hello 1.5.0 '"mallory"' > index/he/hello/hello-1.5.0.toml
manifest hello 3.0.0 '"mallory"' > index/he/hello/hello-3.0.0.toml
pr_commit
as mallory
expect fail "intermediate release plus self-declared latest one"

new_repo
manifest world 11.0.0 '"dave"' > index/wo/world/world-11.0.0.toml
pr_commit
as dave
expect fail "maintainer of the textually last, not latest, release"

new_repo
echo '# edited' >> index/li/libfoo/libfoo-external.toml
pr_commit
as henry
expect fail "external maintainer when the crate has releases"

new_repo
echo '# edited' >> index/cu/curl/curl-external.toml
pr_commit
as mallory
expect fail "external modified by unrelated user"

new_repo
mkdir -p index/cu/curl
external curl '"mallory"' > index/cu/curl/curl-external.toml
pr_commit
as mallory
expect fail "external replaced with self-declared maintainers"

new_repo
mkdir -p index/he/hello
external hello '"mallory"' > index/he/hello/hello-external.toml
pr_commit
as mallory
expect fail "external added to a crate with releases"

new_repo
sed -i '3a available = false' index/he/hello/hello-2.0.0.toml
pr_commit
as mallory
expect fail "withdrawal by unrelated user"

new_repo
git rm -q index/he/hello/hello-1.0.0.toml
pr_commit
as mallory
expect fail "deletion by unrelated user"

new_repo
manifest hello 2.1.0 '"bob"' > index/he/hello/hello-2.1.0.toml
manifest world 11.0.0 '"bob"' > index/wo/world/world-11.0.0.toml
pr_commit
as bob
expect fail "two crates, author maintains only one"

new_repo
manifest tie 2.1.0 '"ivan"' > index/ti/tie/tie-2.1.0.toml
pr_commit
as ivan
expect fail "release on top of tied latest releases, fails closed"

new_repo
manifest tied 3.0.0 '"lena"' > index/ti/tied/tied-3.0.0.toml
pr_commit
as lena
expect pass "tie below the latest release does not matter"

new_repo
mkdir -p index/ti/tie
manifest tie 2.0.0+y '"ivan"' > index/ti/tie/tie-2.0.0+y.toml
pr_commit
as ivan
expect fail "latest releases tie (build metadata), fails closed"
as judy
expect fail "latest releases tie, for either maintainer"

new_repo
manifest odd 1.1.0 '"kate"' > index/od/odd/odd-1.1.0.toml
pr_commit
as kate
expect fail "non-semver version in base, fails closed"

new_repo
pr_commit
# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT_ARGS=()
if "$SCRIPT" > /dev/null 2>&1; then
    echo "FAIL - missing author argument should be a usage error"
    failures=$((failures + 1))
else
    echo "ok   - missing author argument is a usage error"
fi

finish
