#!/bin/bash

# Kept for the existing callers in the index workflows, which pass the PR
# author login as the only argument. See check-author.py for details.

exec "$( dirname -- "${BASH_SOURCE[0]}" )/check-author.py" "$@"
