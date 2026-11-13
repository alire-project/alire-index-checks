#!/bin/bash

# Show or switch the mode of the auto-merge bot of an Alire index (see
# "Auto-merge eligibility" in README.md). Needs `gh`, logged in as an admin
# of the index.
#
# Usage: automerge-mode.sh [off|dry-run|on] [--period year|month|week|day]
#                          [--run]
#
# Without a mode, prints the current settings and log issue. Otherwise sets
# the AUTO_MERGE variable of the index, after checking that the App the bot
# runs as is configured. --period sets how often the bot starts a new log
# issue, and --run starts a bot run at once.
#
# When leaving `on`, the promises to merge made by the bot are withdrawn:
# open PRs lose their `auto-merge: pending` or `auto-merge: cooling` label,
# and get a comment saying that they will be reviewed manually.
#
# Environment:
#   REPO  the index (default: alire-project/alire-index)

set -o errexit
set -o nounset
set -o pipefail

REPO=${REPO:-alire-project/alire-index}
PROMISES=("auto-merge: pending" "auto-merge: cooling")
WITHDRAWN="Automatic merging is paused, so this PR will be reviewed by the \
index maintainers instead."

usage() {
   echo "Usage: $(basename "$0") [off|dry-run|on]" \
        "[--period year|month|week|day] [--run]" >&2
   exit 2
}

# Value of a repository variable, empty if unset
variable() {
   gh variable list --repo "$REPO" --json name,value \
      --jq ".[] | select(.name == \"$1\") | .value"
}

mode=""
period=""
run=false
while (( $# > 0 )); do
   case $1 in
      off|dry-run|on) mode=$1 ;;
      --period)
         [[ ${2:-} =~ ^(year|month|week|day)$ ]] || usage
         period=$2
         shift ;;
      --run) run=true ;;
      *) usage ;;
   esac
   shift
done

current=$(variable AUTO_MERGE)

if [[ -z $mode && -z $period ]]; then
   echo "Mode:   ${current:-off (unset)}"
   period=$(variable AUTO_MERGE_LOG_PERIOD)
   echo "Period: ${period:-year (unset)}"
   echo "Open log issues:"
   gh issue list --repo "$REPO" --state open \
      --search 'in:title "Auto-merge log"' --json number,title,author \
      --jq '.[] | "  #\(.number) \(.title) (by \(.author.login))"'
   $run || exit 0
fi

if [[ $mode == dry-run || $mode == on ]]; then
   # Both modes post as the App, so that the bot recognizes its own log
   if [[ -z $(variable AUTO_MERGE_APP_ID) ]] ||
      ! gh api "repos/$REPO/actions/secrets/AUTO_MERGE_APP_KEY" \
         --silent 2>/dev/null
   then
      echo "ERROR: the AUTO_MERGE_APP_ID variable or the AUTO_MERGE_APP_KEY" \
           "secret is missing in $REPO" >&2
      exit 1
   fi
fi

if [[ -n $period ]]; then
   gh variable set AUTO_MERGE_LOG_PERIOD --repo "$REPO" --body "$period"
   echo "Log period: $period"
fi

if [[ -n $mode ]]; then
   gh variable set AUTO_MERGE --repo "$REPO" --body "$mode"
   echo "Mode: ${current:-off} -> $mode"

   # Set first, so that no further run renews the promises withdrawn here
   if [[ $current == on && $mode != on ]]; then
      for label in "${PROMISES[@]}"; do
         for pr in $(gh pr list --repo "$REPO" --state open --label "$label" \
                        --json number --jq '.[].number'); do
            gh pr edit "$pr" --repo "$REPO" --remove-label "$label"
            gh pr comment "$pr" --repo "$REPO" --body "$WITHDRAWN"
            echo "Withdrawn: PR $pr ($label)"
         done
      done
   fi
fi

if $run; then
   gh workflow run auto-merge.yml --repo "$REPO"
   echo "Run started, see: gh run list --repo $REPO --workflow auto-merge.yml"
fi
