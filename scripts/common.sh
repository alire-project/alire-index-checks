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