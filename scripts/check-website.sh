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

# Ensure all alr runs are non-interactive and able to output unexpected errors
alias alr="alr -d -n"

# See whats happening
git log --graph --decorate --pretty=oneline --abbrev-commit --all | head -30

# Detect changes
CHANGES=$( changed_manifests )

# Bulk changes for the record
echo Changed files: $CHANGES

# Enable Homebrew on macOS
[ `uname -s` == "Darwin" ] && {
   eval $(brew shellenv)
   echo "Homebrew for macOS enabled"
}

# Configure local index
alr index --name local --add ./index

# Remove community index in case it has been added somehow
alr index --del community || true

for file in $CHANGES; do

   if [[ $(basename $file) == index.toml ]]; then
      echo Skipping index metadata file: $file
      continue
   fi

   if [[ $file != *.toml ]]; then
      echo Skipping non-crate file: $file
      continue
   fi

   if ! [ -f ./$file ]; then
      echo Skipping deleted file: $file
      continue
   fi

   # Checks passed, this is a crate we must test
   is_system=false

   crate=$(basename $file .toml | cut -f1 -d-)
   version=$(basename $file .toml | cut -f2- -d-)
   milestone="$crate=$version"

   echo
   box "$milestone"
   echo

   # Version can be "external", in which case a specific website is
   # not very useful.

   if [[ $version = external ]]; then
      echo SKIPPING check for external crate $milestone
      continue
   fi

   # Avoid alr talk about autoupdating interfering
   alr show $milestone > /dev/null

   output=$(alr --format=json show $milestone 2>&1)
   echo $output

   website=$(echo $output | jq .website)

   if [[ $website = '' ]] || [[ $website = '""' ]] || [[ $website = null ]]; then
      fail FAILED: crate manifest for $milestone has an empty website field
   fi

done

exit 0
