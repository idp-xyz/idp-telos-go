"""Check the components out next to this repository at the commits releases/platform-bom.yaml pins.

Used by the CI workflows. Public components are cloned over https. idp-eer is private: it is cloned over
ssh with the read-only deploy key in IDP_EER_DEPLOY_KEY (the secret of the same name), and skipped when
the variable is empty (pull requests from forks get no secrets); a skipped component is reported, and the
callers decide whether that is allowed.

    python -I tools/checkout_components.py [NAME ...]      (default: every component of the BOM)
    python -I tools/checkout_components.py --require idp-eer ...   fail instead of skipping
"""

import argparse
import os
import subprocess
import sys
import tempfile

import yaml

PRIVATE = {"idp-eer"}
# GitHub's published host key (https://api.github.com/meta, ssh_keys), so ssh never trusts on first use.
GITHUB_HOST_KEY = "github.com ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIOMqqnkVzrm0SdG6UOoqKLsabgH5C9okWi0dh2l9GKJl"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("names", nargs="*")
    ap.add_argument("--require", action="append", default=[], help="fail when this component cannot be checked out")
    ap.add_argument("--bom", default="releases/platform-bom.yaml")
    args = ap.parse_args()
    components = yaml.safe_load(open(args.bom))["components"]
    known = {c["name"] for c in components}
    for n in args.names + args.require:
        if n not in known:
            print(f"FAIL: {n} is not in {args.bom}", file=sys.stderr)
            return 1
    wanted = set(args.names) or known
    key = os.environ.get("IDP_EER_DEPLOY_KEY", "").strip()
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        if key:
            kf, kh = os.path.join(tmp, "key"), os.path.join(tmp, "known_hosts")
            with open(os.open(kf, os.O_WRONLY | os.O_CREAT, 0o600), "w") as f:
                f.write(key + "\n")
            with open(kh, "w") as f:
                f.write(GITHUB_HOST_KEY + "\n")
            env["GIT_SSH_COMMAND"] = f"ssh -i {kf} -o IdentitiesOnly=yes -o UserKnownHostsFile={kh} -o StrictHostKeyChecking=yes"
        for c in components:
            name = c["name"]
            if name not in wanted:
                continue
            if name in PRIVATE:
                if not key:
                    if name in args.require:
                        print(f"FAIL: {name} is private and IDP_EER_DEPLOY_KEY is not set", file=sys.stderr)
                        return 1
                    print(f"SKIPPED: {name} (private, no deploy key)")
                    continue
                url = f"git@github.com:{c['repository']}.git"
            else:
                url = f"https://github.com/{c['repository']}.git"
            subprocess.run(["git", "clone", "--quiet", "--filter=blob:none", url, name], check=True, env=env)
            subprocess.run(["git", "-C", name, "checkout", "--quiet", c["commit"]], check=True)
            print(f"{name} {c['commit'][:12]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
