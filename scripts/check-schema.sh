#!/bin/bash

trap 'echo "ERROR at line ${LINENO} (code: $?)" >&2' ERR
trap 'echo "Interrupted" >&2 ; exit 1' INT

set -o errexit
set -o nounset

# Import reusable bits
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
pushd "$SCRIPT_DIR"
. common.sh
popd

# JSON Schema to validate against. The workflow provides it by cloning the
# Alire repository; override the location as the first argument if needed.
SCHEMA_FILE=${1:-alire/schemas/manifest-schema.yaml}

[ -f "$SCHEMA_FILE" ] || fail "schema file not found: $SCHEMA_FILE"

# Detect changes
CHANGES=$( changed_manifests )

# Bulk changes for the record
echo Changed manifests: $CHANGES

for file in $CHANGES; do

   crate=$(basename $file .toml | cut -f1 -d-)
   version=$(basename $file .toml | cut -f2- -d-)
   milestone="$crate=$version"

   echo
   box "$milestone"
   echo

   if "$SCRIPT_DIR/check-schema.py" "$file" "$SCHEMA_FILE"; then
      echo "PASSED: $file validates against the schema"
   else
      fail "FAILED: $file does not validate against the schema"
   fi

done

exit 0
