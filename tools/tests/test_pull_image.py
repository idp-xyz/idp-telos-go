"""Tests of tools/pull_image.sh: the retries around `docker pull`, with a fake docker on PATH (no daemon, no network).
Standard library only."""
import os
import stat
import subprocess
import tempfile
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "pull_image.sh"
IMAGE = "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine"
LIMIT = "Error response from daemon: error from registry: Data limit exceeded"

# FAKE_PULLS_FAILING pulls are refused with the registry's message, later ones succeed; FAKE_PRESENT names the image the
# daemon already has. Every call is appended to $FAKE_DIR/calls.
FAKE_DOCKER = f"""#!/bin/sh
echo "$*" >> "$FAKE_DIR/calls"
case "$1 $2" in
  "image inspect") [ "$3" = "$FAKE_PRESENT" ]; exit $? ;;
  "pull -q")
    n=$(grep -c '^pull ' "$FAKE_DIR/calls")
    if [ "$n" -le "$FAKE_PULLS_FAILING" ]; then echo "{LIMIT}" >&2; exit 1; fi
    echo sha256:0; exit 0 ;;
esac
exit 99
"""


class PullImage(unittest.TestCase):
    def run_script(self, *images, failing=0, present="", waits="0 0 0 0"):
        with tempfile.TemporaryDirectory() as d:
            fake = Path(d) / "docker"
            fake.write_text(FAKE_DOCKER)
            fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=d, FAKE_PULLS_FAILING=str(failing),
                       FAKE_PRESENT=present, IDP_PULL_WAITS=waits)
            (Path(d) / "calls").touch()
            r = subprocess.run(["bash", str(SCRIPT), *images], env=env, capture_output=True, text=True, timeout=60)
            calls = (Path(d) / "calls").read_text().splitlines()
        return r, [c for c in calls if c.startswith("pull ")]

    def test_retries_and_succeeds(self):
        r, pulls = self.run_script(IMAGE, failing=2)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(len(pulls), 3)
        lines = r.stderr.splitlines()
        self.assertEqual(len(lines), 2)
        for n, line in enumerate(lines, 1):
            self.assertIn(IMAGE, line)
            self.assertIn(f"attempt {n} of 5 failed", line)
            self.assertIn("Data limit exceeded", line)

    def test_gives_up_after_five_attempts(self):
        r, pulls = self.run_script(IMAGE, failing=99)
        self.assertEqual(len(pulls), 5)
        self.assertNotEqual(r.returncode, 0)
        lines = r.stderr.splitlines()
        self.assertEqual(len(lines), 5)   # four failed attempts with a retry, one final line
        self.assertEqual(lines[-1], f"BLOCKED: could not pull {IMAGE} after 5 attempts: {LIMIT}")

    def test_waits_are_15_30_60_120_by_default(self):
        with tempfile.TemporaryDirectory() as d:
            sleep = Path(d) / "sleep"   # records instead of waiting
            sleep.write_text(f'#!/bin/sh\necho "$1" >> "{d}/sleeps"\n')
            sleep.chmod(0o755)
            fake = Path(d) / "docker"
            fake.write_text(FAKE_DOCKER)
            fake.chmod(0o755)
            (Path(d) / "calls").touch()
            env = dict(os.environ, PATH=f"{d}:{os.environ['PATH']}", FAKE_DIR=d, FAKE_PULLS_FAILING="99", FAKE_PRESENT="")
            env.pop("IDP_PULL_WAITS", None)
            subprocess.run(["bash", str(SCRIPT), IMAGE], env=env, capture_output=True, text=True, timeout=60)
            self.assertEqual((Path(d) / "sleeps").read_text().split(), ["15", "30", "60", "120"])

    def test_present_image_is_not_pulled(self):
        r, pulls = self.run_script(IMAGE, failing=99, present=IMAGE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(pulls, [])
        self.assertEqual(r.stderr, "")

    def test_several_images_and_none(self):
        r, pulls = self.run_script(IMAGE, "ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2", present=IMAGE)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(pulls, ["pull -q ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2"])
        r, pulls = self.run_script()
        self.assertEqual((r.returncode, pulls), (0, []))


if __name__ == "__main__":
    unittest.main()
