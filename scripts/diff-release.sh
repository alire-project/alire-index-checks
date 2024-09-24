#!/bin/bash

trap 'echo "ERROR at line ${LINENO} (code: $?)" >&2' ERR
trap 'echo "Interrupted" >&2 ; exit 1' INT

set -o errexit
set -o nounset

# Import reusable bits
pushd $( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
. common.sh
popd

# Required for aliases to work in non-interactive scripts
shopt -s expand_aliases

# Ensure all alr runs are non-interactive
alias alr="alr -n"

# Diagnose environment
env | sort

# Detect changes
CHANGES=$( changed_manifests )

# Bulk changes for the record
echo Changed files: $CHANGES

# Disable assistant. This is necessary despite the setup-alire action doing it
# too, because we sometimes run inside a Docker with fresh configuration
alr toolchain --disable-assistant

# Configure local index. For the case where two consecutive tests are attempted
# (e.g. macOS -/+ Homebrew), remove it first
if alr index | cut -f1 -d' ' | grep -q local; then
   alr index --del local || echo "Local index not yet configured, not removing"
fi
alr index --name local --add ./index

# Remove community index in case it has been added before
alr index --del community >/dev/null || true

diff_opts=(--minimal -U0 --ignore-all-space --ignore-blank-lines --ignore-cr-at-eol)

is_trivial_change() {
    [[ $1 =~ ^[+-](version|commit|url)[[:space:]]*= ]]
}

function diff_one() {
    local file="$1"
    local folder=$(dirname $file)
    local crate=$(basename $file .toml | cut -f1 -d-)
    local version=$(basename $file .toml | cut -f2- -d-)
    local milestone="$crate=$version"

    if [[ "$(echo $folder | cut -f1 -d/)" != "index" ]]; then
        echo SKIPPING non-manifest file: $file
        return
    fi

    echo " "
    echo "------8<------"

    if echo $milestone | grep -q external; then
        echo DIFFING external: $milestone
        git diff "${diff_opts[@]}" HEAD~1 -- $file
    else
        echo DIFFING release: $milestone

        # Locate the immediately precedent release

        # For a first release, there's nothing to compare
        if [ $(ls $folder | grep -v external | wc -l) -eq 1 ]; then
            echo "NOTHING to diff against, first crate release ($milestone)"
            return 0
        fi

        # Othewise, get from alr what's the immediately preceding version
        local prev_milestone=$(alr show "$crate<$version" | head -1 | cut -f1 -d:)
        echo DIFFING milestones $prev_milestone '-->' $milestone

        if [ "$prev_milestone" == "ERROR" ]; then
            echo ERROR extracting milestone:
            alr show "$crate<$version"
            return 1
        elif [ "$prev_milestone" == "Not found" ]; then
            echo "WARNING: release $milestone is first version-wise, no prior to compare against"
            return 0
        fi

        # Convert into filename
        local prev_file=$folder/${prev_milestone//=/-}.toml

        diff_output=$(git diff --no-index "${diff_opts[@]}" -- $prev_file $file)
        # Echo the diff, but prefix every line with "··|" to make it easier to
        # see:
        echo "$diff_output" | sed 's/^/··| /'

        # If all actual diff lines are only changing version and commit, apply
        # label of diff OK:

        echo "AUTOMATIC REVIEW FOLLOWS:"

        echo "$diff_output" | grep -E '^[+-]' | grep -Ev '^[+-]{3}' | while read -r line; do
            if ! is_trivial_change "$line"; then
                echo "Note: non-trivial change detected, review manually:"
                touch non_trivial # Can't use variables as we are in a subshell
                echo "$line"
                # apply_label "diff: review"
                break
            else
                echo "Note: trivial change detected, skipping:"
                echo "$line"
            fi
        done

        # Depending on minimal value, apply label
        if [[ ! -f non_trivial ]]; then
            echo "Note: minimal changes, marking diff OK"
            # apply_label "diff: OK"
        fi
    fi

    return 0
}

for file in $CHANGES; do
    diff_one "$file" || true # keep on trying for different files
done
