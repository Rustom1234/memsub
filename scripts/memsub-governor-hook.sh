#!/usr/bin/env bash
# memsub governor: PreToolUse hook. Reads the hook JSON on stdin, consults
# memsub/governor.py (static regex rules, fail-open, ~no latency), emits a
# deny/warn response or stays silent. See ~/.memsub/governor/rules.json.
exec /home/user/projects/memory-sublayer/.venv/bin/python -m memsub.governor
