"""Long-task evaluation of the platform's agent runtime (docs/adr/idp-host-context-management-proposal.md, sections 1.1, 4, 5).

It drives real long tasks through a platform that is already running (compose, a live model) exactly as a member would
(upload in the Workspace, ask an agent, wait, download what it delivers), then reads from the HOST's own database what each
run cost. The same numbers can be computed from a pg_dump of an earlier platform (history), so that the recorded past and new
measurements go through one report. See tools/long_eval/README.md.

Layout: cases.py (reading cases, success conditions), files.py (sanity checks of delivered files), metrics.py (turning the
HOST's rows into one record), report.py (tables), live.py (the platform: HTTP and Docker; the only code that needs it),
run.py (the evaluation loop), history.py (a dump), cli.py (arguments).
"""
