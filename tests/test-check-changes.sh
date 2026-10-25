#!/bin/bash

# Self-contained tests for scripts/check-changes.py. See lib.sh.

. "$( dirname -- "${BASH_SOURCE[0]}" )/lib.sh"

# shellcheck disable=SC2034 # Used by lib.sh
SCRIPT=$SCRIPTS_DIR/check-changes.py

function base_files() {
    mkdir -p index/he/hello index/li/libhello .github
    manifest hello 1.0.0 > index/he/hello/hello-1.0.0.toml
    manifest libhello 1.0.0 > index/li/libhello/libhello-1.0.0.toml
    echo "# CI" > .github/README
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

# --- Length after X.Y.Z, separators included (32 characters, as in alr)

function chars() { printf 'a%.0s' $(seq "$1"); }

new_repo
manifest hello "1.1.0-$(chars 31)" > "index/he/hello/hello-1.1.0-$(chars 31).toml"
pr_commit
expect pass "pre-release at the length limit"

new_repo
V="1.1.0-$(chars 10)+$(chars 20)"
manifest hello "$V" > "index/he/hello/hello-$V.toml"
pr_commit
expect pass "pre-release and build metadata at the length limit"

new_repo
manifest hello "1.1.0-$(chars 32)" > "index/he/hello/hello-1.1.0-$(chars 32).toml"
pr_commit
expect fail "pre-release over the length limit"

new_repo
manifest hello "1.1.0+$(chars 32)" > "index/he/hello/hello-1.1.0+$(chars 32).toml"
pr_commit
expect fail "build metadata over the length limit"

new_repo
V="1.1.0-$(chars 10)+$(chars 21)"
manifest hello "$V" > "index/he/hello/hello-$V.toml"
pr_commit
expect fail "pre-release and build metadata together over the length limit"

new_repo
# Older indexes let the build metadata differ between file name and contents
manifest hello "1.1.0+$(chars 32)" > index/he/hello/hello-1.1.0+x.toml
pr_commit
expect fail "length over the limit only in contents"

new_repo
V="999999999999999999.999999999999999999.999999999999999999"
manifest hello "$V" > "index/he/hello/hello-$V.toml"
pr_commit
expect pass "long X.Y.Z is not limited"

# --- Manifest size (16 KiB)

function pad_to() { # Pad a manifest with a comment to the given size
    local file=$1 size=$2
    local len; len=$(stat -c %s "$file")
    printf '#%s\n' "$(chars $((size - len - 2)))" >> "$file"
}

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
pad_to index/he/hello/hello-1.1.0.toml 16384
pr_commit
expect pass "manifest at the size limit"

new_repo
manifest hello 1.1.0 > index/he/hello/hello-1.1.0.toml
pad_to index/he/hello/hello-1.1.0.toml 16385
pr_commit
expect fail "manifest over the size limit"

new_repo
mkdir -p index/wo/world
manifest world external > index/wo/world/world-external.toml
pad_to index/wo/world/world-external.toml 16384
pr_commit
expect pass "external manifest at the size limit"

new_repo
mkdir -p index/wo/world
manifest world external > index/wo/world/world-external.toml
pad_to index/wo/world/world-external.toml 16385
pr_commit
expect fail "external manifest over the size limit"

# --- Nesting depth (128 levels of tables and arrays)

function nest() { # Top-level TOML of the given kind nested $2 levels deep
    local kind=$1 depth=$2
    local keys; keys=$(printf '.a%.0s' $(seq $((depth - 1))))
    case $kind in
        dotted) echo "x$keys = 1" ;;
        header) printf '[x%s]\nk = 1\n' "${keys%.a}" ;;
        array)  printf 'x = %s%s\n' "$(printf '[%.0s' $(seq "$depth"))" \
                                     "$(printf ']%.0s' $(seq "$depth"))" ;;
        inline) printf 'x = %s1%s\n' "$(printf '{a=%.0s' $(seq $((depth - 1))))" \
                                      "$(printf '}%.0s' $(seq $((depth - 1))))" ;;
    esac
}

function nested_manifest() { # New release of hello with top-level nesting
    if [[ $1 == header ]]; then # Last, or it would capture the keys below it
        { manifest hello 1.1.0; nest "$1" "$2"; }
    else
        { nest "$1" "$2"; manifest hello 1.1.0; }
    fi > index/he/hello/hello-1.1.0.toml
}

for kind in dotted header array inline; do
    new_repo
    nested_manifest $kind 128
    pr_commit
    expect pass "$kind nesting at the depth limit"

    new_repo
    nested_manifest $kind 129
    pr_commit
    expect fail "$kind nesting over the depth limit"
done

new_repo
nested_manifest array 2000 # tomllib raises RecursionError
pr_commit
expect fail "array nesting too deep for the parser"

new_repo
nested_manifest dotted 5000 # tomllib builds a deep table
pr_commit
expect fail "dotted key nesting far over the depth limit"

# --- ALL_CRATES: every manifest in the index is checked

new_repo
pr_commit
ALL_CRATES=1 expect pass "ALL_CRATES on a valid index"

new_repo
mkdir -p index/xx/hello
manifest hello 0.9.0 > index/xx/hello/hello-0.9.0.toml
git add -A
git commit -q -m "misplaced manifest already in base"
pr_commit
expect pass "misplaced manifest in base is not a PR change"
ALL_CRATES=1 expect fail "ALL_CRATES detects misplaced manifest in base"
ALL_CRATES=1 DRY_RUN=1 expect pass "ALL_CRATES with DRY_RUN reports but passes"

finish
