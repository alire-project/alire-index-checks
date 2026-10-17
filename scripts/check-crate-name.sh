#!/bin/bash

trap 'echo "ERROR at line ${LINENO} (code: $?)" >&2' ERR
trap 'echo "Interrupted" >&2 ; exit 1' INT

set -o errexit
set -o nounset

# Import reusable bits
pushd $( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
. common.sh
popd

# Detect changes
CHANGES=$( changed_manifests )

# Bulk changes for the record
echo Changed manifests: $CHANGES

declare -A checked=()

for file in $CHANGES; do

   crate=$(basename $file .toml | cut -f1 -d-)
   crate_dir=$(dirname $file)

   # With ALL_CRATES, every crate is checked as if new, once
   if [[ "${ALL_CRATES:-unset}" != "unset" ]]; then
      [[ -n "${checked[$crate]:-}" ]] && continue
      checked[$crate]=1
   else
      # Skip files that already existed (modifications of existing manifests)
      if exists_in_base $file; then
         echo Skipping modified existing manifest: $file
         continue
      fi

      # Skip if this crate directory already existed in the base commit
      # (i.e. this is a new version of an existing crate)
      if git ls-tree -r HEAD~1 --name-only "$crate_dir/" \
            | grep -q '\.toml$'; then
         echo Skipping new version of existing crate: $crate
         continue
      fi
   fi

   echo
   box "$crate"
   echo

   # This is a new crate — check its name
   crate_lower=$(echo "$crate" | tr '[:upper:]' '[:lower:]')

   if [[ $crate_lower == lib* ]]; then
      fail "REVIEW: crate '$crate' starts with 'lib'"
   elif [[ $crate_lower == ada* ]]; then
      fail "REVIEW: crate '$crate' starts with 'ada'"
   elif [[ $crate_lower == spark* ]]; then
      fail "REVIEW: crate '$crate' starts with 'spark'"
   elif [[ $crate_lower == *ada* ]]; then
      fail "REVIEW: crate '$crate' contains 'ada'"
   elif [[ $crate_lower == *spark* ]]; then
      fail "REVIEW: crate '$crate' contains 'spark'"
   else
      echo "OK: crate name '$crate' requires no manual review"
   fi

done

exit 0
