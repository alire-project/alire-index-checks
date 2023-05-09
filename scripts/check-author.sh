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

# Get author and show for the record
PR_AUTHOR=$1
echo PR author: $PR_AUTHOR

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

# Match maintainers against PR author
for file in $CHANGES; do

   if [[ $file == index.toml ]]; then
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
   previous="$crate<$version"

   echo
   box "$milestone"
   echo

   # Remember that version can be "external", in which case we do not have yet
   # a reliable way of checking maintainership, as properties can't be listed
   # right now for undetected externals.

   if [[ $version = external ]]; then
      echo SKIPPING check for external crate $milestone
      exit 0
   fi

   # If there is no previous version, there's no test to perform
   if alr show $previous | grep -q "Not found"; then
      echo SKIPPING check for first release $milestone
      exit 0
   fi

   # Show all maintainers for the record (using previous release)
   echo $previous maintainers are:
   alr show $previous | head -1
   alr show $previous | grep Maintainers_Logins

   for maint in $(alr show $previous | grep Maintainers_Logins: | cut -f2 -d: | tr -d ' '); do
      if [[ $maint = $PR_AUTHOR ]]; then
         echo SUCCESS: PR author $PR_AUTHOR is in the list of maintainers
         continue 2 # Skip to next milestone
      else
         echo Maintainer $maint does not match PR author $PR_AUTHOR
      fi
   done

   echo FAILED: PR author is not in the list of maintainers of $milestone
   exit 1

done

exit 0