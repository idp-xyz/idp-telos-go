"""Arguments and the three commands.

  python3 -m tools.long_eval run --cases .local/long-eval/cases.json [--case L1 ...] --runs 3 --name baseline
  python3 -m tools.long_eval report --name baseline [--against other]
  python3 -m tools.long_eval history --dump idp_host.dump --out .local/long-eval/history
"""
import argparse
import json
import os
import sys
import time
from pathlib import Path

from . import cases as casesmod
from . import history as historymod
from . import report as reportmod
from . import run as runmod
from .live import Platform, PlatformError


def resolve(name: str) -> Path:
    """A result directory by name (.local/long-eval/<name>) or by path."""
    p = Path(name)
    return p if (os.sep in name or p.is_dir()) else runmod.OUT / name


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="python3 -m tools.long_eval", description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="drive the cases through a running platform")
    r.add_argument("--cases", required=True, help="the case file (kept in .local, with its files)")
    r.add_argument("--case", action="append", help="only this case id; repeat for several")
    r.add_argument("--runs", type=int, default=3, help="repetitions of each case (default 3)")
    r.add_argument("--name", required=True, help="results go to .local/long-eval/<name>/; the same name continues a stopped evaluation")
    r.add_argument("--window", type=int, help="the model's context window in tokens, when the provider does not report it")
    r.add_argument("--confirm", action="store_true", help="confirm a tool call that waits for the member (default: record it and reject nothing)")
    r.add_argument("--confirm-wait", type=float, default=5, help="minutes to wait for an answer to a confirmation when --confirm is off (default 5)")
    r.add_argument("--turn-timeout", type=float, default=60, help="minutes after which a run still going is canceled and recorded as a timeout (default 60; the agent's own limit is 45)")
    r.add_argument("--user", default="bob", help="the member who asks (a user of .local/platform/clients.json)")
    r.add_argument("--workspace", default="us-ops")
    r.add_argument("--app", default="office")
    r.add_argument("--action", default="office.run")
    r.add_argument("--model-url", default=os.environ.get("IDP_LIVE_MODEL_URL", "http://127.0.0.1:8080/v1"),
                   help="where to ask the provider for its context window, without a key (default IDP_LIVE_MODEL_URL or http://127.0.0.1:8080/v1)")
    r.add_argument("--accept-changed-platform", action="store_true", help="continue a stopped evaluation although the images of the platform's containers changed")
    p = sub.add_parser("report", help="report.md from the results of a name")
    p.add_argument("--name", required=True)
    p.add_argument("--against", help="another name or directory to compare with")
    p.add_argument("--agent", help="only the runs of this agent (a history has all of a platform's agents)")
    h = sub.add_parser("history", help="the same numbers from a pg_dump of an earlier platform")
    h.add_argument("--dump", required=True)
    h.add_argument("--out", required=True)
    h.add_argument("--window", type=int, help=f"the model's window (default {historymod.DEFAULT_WINDOW}, as in the proposal's section 1.1)")
    h.add_argument("--cases", help="a case file: groups whose message is the one turn of a single-turn case take its id and baseline")
    h.add_argument("--image", default=historymod.IMAGE)
    return ap


def cmd_run(a) -> int:
    if a.runs < 1:
        print("BLOCKED: --runs must be at least 1", file=sys.stderr)
        return 2
    try:
        agent, cases = casesmod.load(a.cases, a.case)
    except casesmod.CaseError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    gone = casesmod.missing_files(cases)
    if gone:
        print("BLOCKED: attachments missing: " + ", ".join(gone), file=sys.stderr)
        return 2
    plat = Platform(workspace=a.workspace, user=a.user, app=a.app, action=a.action)
    try:
        plat.check_up()
        meta = plat.meta(agent or "office-assistant", a.model_url, a.window)
    except PlatformError as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    model = (meta["agent"].get("model") or {}).get("model")
    if not model:
        print(f"BLOCKED: the HOST's database has no agent {agent!r} with a model: {meta['agent'].get('error')}", file=sys.stderr)
        return 2
    if str(model).startswith("fake"):
        print(f"BLOCKED: the agent uses the model double ({model}); a baseline needs MODEL=live bash deploy/compose/e2e.sh", file=sys.stderr)
        return 2
    window = meta["window"]["value"]
    if window is None:
        print(f"warning: the context window is unknown ({meta['window']['provider_note']}); shares of the window are not computed. Pass --window N.", file=sys.stderr)
    out = runmod.OUT / a.name if os.sep not in a.name else Path(a.name)
    out.mkdir(parents=True, exist_ok=True)
    mp = out / "meta.json"
    if mp.exists():
        old = json.loads(mp.read_text(encoding="utf-8"))
        changed = _changed_images(old, meta)
        if changed and not a.accept_changed_platform:
            print("BLOCKED: the platform's images changed since this evaluation started (" + ", ".join(changed) +
                  "); results of two builds would be mixed. Use another --name, or --accept-changed-platform.", file=sys.stderr)
            return 2
        old.setdefault("resumed", []).append({"at": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "changed_images": changed})
        meta = {**old, "resumed": old["resumed"]}
    else:
        meta["started"] = time.strftime("%Y-%m-%dT%H:%M:%S%z")
        meta["arguments"] = {"cases": a.cases, "case": a.case, "runs": a.runs, "confirm": a.confirm, "turn_timeout": a.turn_timeout}
    mp.write_text(json.dumps(meta, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"platform up; model {model}, window {window}; results in {out}", file=sys.stderr)
    try:
        ran = runmod.evaluate(plat, cases, a, out, window, model, log=lambda s: print(s, file=sys.stderr, flush=True))
    except PlatformError as e:
        print(f"STOPPED: {e}\nWhat had ended is in {out}/results.jsonl; run the same command again to continue.", file=sys.stderr)
        return 1
    print(f"{ran} turns run; report: python3 -m tools.long_eval report --name {a.name}", file=sys.stderr)
    return 0


def _changed_images(old: dict, new: dict) -> list:
    a = ((old.get("versions") or {}).get("containers")) or {}
    b = ((new.get("versions") or {}).get("containers")) or {}
    if not isinstance(a, dict) or not isinstance(b, dict):
        return []
    return sorted(k for k in a if k in b and a[k].get("image_id_created") != b[k].get("image_id_created"))


def cmd_report(a) -> int:
    try:
        p = reportmod.write(resolve(a.name), resolve(a.against) if a.against else None, a.agent)
    except (ValueError, OSError) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    print(p)
    return 0


def cmd_history(a) -> int:
    try:
        recs = historymod.run(Path(a.dump), Path(a.out), a.window, a.cases, a.image, log=lambda s: print(s, file=sys.stderr))
    except (PlatformError, casesmod.CaseError, OSError, ValueError) as e:
        print(f"BLOCKED: {e}", file=sys.stderr)
        return 2
    print(f"{len(recs)} runs written to {a.out}/results.jsonl; report: python3 -m tools.long_eval report --name {a.out}", file=sys.stderr)
    return 0


def main(argv=None) -> int:
    a = parser().parse_args(argv)
    return {"run": cmd_run, "report": cmd_report, "history": cmd_history}[a.cmd](a)
