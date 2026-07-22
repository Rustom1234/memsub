"""Transcript/history parsers for coding tools other than Claude Code.

Each module exposes one `index_*(store, ...)` function that writes rows into
the same `filestates` table memsub.filehist owns, so restore/reconstruct/
check_rollbacks work identically no matter which tool produced a given file
state. Every parser is guarded: the tool being absent on this machine is a
silent no-op (returns 0), never an error, never a crash of the scan.
"""
