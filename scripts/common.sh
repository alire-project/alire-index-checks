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

function under_github() {
    # Check whether we are running under GitHub Actions
    if [[ -n "${GITHUB_EVENT_PATH:-}" ]]; then
        return 0
    else
        return 1
    fi
}

function escape_JSON() {
    # Escape a string for use in JSON
    echo $(jq -aRs . <<< "$1")
}

function apply_label() {
    # Apply a label to a PR from within a workflow

    # Do nothing if not running under GitHub Actions
    if ! under_github; then
        echo "Note: not running under GitHub Actions, not applying label"
        return
    fi

    # Extract the PR number from the GitHub event data
    local PR_NUMBER=$(jq --raw-output .number "$GITHUB_EVENT_PATH")
    local LABEL=$1

    echo "Applying label $LABEL to PR $PR_NUMBER"

    # This is more painful that using gh, but gh may not be available in Docker
    # images

    # Create label first
    curl -L -S -X POST \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "{\"name\":\"$LABEL\", \"color\":\"#FBAA04\", \"description\":\"\"}" \
        "https://api.github.com/repos/$GITHUB_REPOSITORY/labels"

    # Apply label
    curl -L -S -X POST \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "{\"labels\":[\"$LABEL\"]}" \
        "https://api.github.com/repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/labels"
}

function post_comment() {
    # Write a comment to a PR from within a workflow

    # Do nothing if not running under GitHub Actions
    if ! under_github; then
        echo "Note: not running under GitHub Actions, not posting comment"
        return
    fi

    # Extract the PR number from the GitHub event data
    local PR_NUMBER=$(jq --raw-output .number "$GITHUB_EVENT_PATH")
    local COMMENT=$(escape_JSON "$1")

    echo "Posting comment '$COMMENT' to PR $PR_NUMBER"

    # This is more painful that using gh, but gh may not be available in Docker images
    curl -L -S -X POST \
        -H "Authorization: Bearer $GITHUB_TOKEN" \
        -H "Accept: application/vnd.github.v3+json" \
        -d "{\"body\":$COMMENT}" \
        "https://api.github.com/repos/$GITHUB_REPOSITORY/issues/$PR_NUMBER/comments"
}