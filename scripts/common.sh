#!/bin/bash

# Functions that can be reused

function box() {
   printf '=%.0s' $(seq 1 $((${#1}+4)) )
   echo
   echo "= $1 ="
   printf '=%.0s' $(seq 1 $((${#1}+4)) )
   echo
}

function changed_manifests() {
    if [[ "${ALL_CRATES:-unset}" != "unset" ]]; then
        find index -name '*-*.toml'
    else
        git diff --name-only HEAD~1
    fi
}

function fail() {
    # Fail if not DRY_RUN is set, otherwise just print the $1 error
    if [[ "${DRY_RUN:-unset}" == "unset" ]]; then
        echo $@
        exit 1
    else
        echo $@
        echo "DRY_RUN set, continuing"
    fi
}

function exists_in_base() {
    # Check whether $1 exists in the base commit
    if [[ -n $(git ls-tree HEAD~1 --name-only "$1") ]]; then
        return 0
    else
        return 1
    fi
}