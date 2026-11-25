"""Tests of tools/mirror_images.py: the list validation, the naming rule, the reference finder on fixture files, the plan table, the
checks over fixture repositories. No network, no Docker. Standard library only."""
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import mirror_images as m  # noqa: E402

D1 = "sha256:" + "1" * 64
D2 = "sha256:" + "2" * 64
D3 = "sha256:" + "3" * 64


def entry(host_path="public.ecr.aws/docker/library/postgres", tag="17-alpine", digest=D1, **kw):
    default = m.target_of(m.parse_ref(host_path), tag)
    return (f"  - source: {host_path}@{digest}\n    tag: \"{tag}\"\n"
            f"    target: {kw.get('target', default)}\n"
            + (f"    aliases: [{kw['aliases']}]\n" if "aliases" in kw else "")
            + (f"    resolvedVia: {kw['via']}\n" if "via" in kw else ""))


def listing(*entries):
    return "# comment\nimages:\n" + "".join(entries)


class Naming(unittest.TestCase):
    def test_rule_keeps_host_and_path(self):
        r = m.parse_ref("public.ecr.aws/docker/library/postgres:17-alpine")
        self.assertEqual(m.target_of(r), "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine")
        r = m.parse_ref("quay.io/keycloak/keycloak:26.8.2@" + D1)
        self.assertEqual(m.target_of(r), "ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2")

    def test_docker_hub_is_written_in_full(self):
        self.assertEqual(m.parse_ref("python:3.12-slim"), m.Ref("docker.io", "library/python", "3.12-slim"))
        self.assertEqual(m.parse_ref("rustfs/rustfs:1.0.4-glibc"), m.Ref("docker.io", "rustfs/rustfs", "1.0.4-glibc"))
        self.assertEqual(m.parse_ref("registry-1.docker.io/prom/prometheus:v3"), m.Ref("docker.io", "prom/prometheus", "v3"))
        self.assertEqual(m.target_of(m.parse_ref("rustfs/rustfs:1")), "ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1")
        self.assertEqual(m.target_of(m.parse_ref("python:3.12-slim")), "ghcr.io/idp-xyz/mirror:docker.io__library__python__3.12-slim")

    def test_the_encoding_stays_inside_the_tag_alphabet_and_limit(self):
        for e in m.load_list():
            self.assertRegex(e.target.removeprefix("ghcr.io/idp-xyz/mirror:"), r"^[A-Za-z0-9_][A-Za-z0-9_.-]{0,127}$")
            self.assertTrue(e.target.startswith("ghcr.io/idp-xyz/mirror:"))

    def test_not_references(self):
        for s in ("65534:65534", "7233:7233", "UPPER/case:1", "a//b:1", "x@sha256:abc", "-v", "/tmp/x:ro"):
            self.assertIsNone(m.parse_ref(s), s)

    def test_own_images(self):
        for s in ("idp-cmi:dev", "idp-cmi", "eer-runtime:dev", "local/temporal:1.10.0", "scratch", "ghcr.io/idp-xyz/idp-cmi:abc",
                  "ghcr.io/idpxyz/eer-runtime:v1"):
            r = m.parse_ref(s)
            self.assertTrue(r and m.is_own(r), s)
        self.assertFalse(m.is_own(m.parse_ref("ghcr.io/cloudnative-pg/postgresql:17")))
        self.assertFalse(m.is_own(m.parse_ref("python:3.12-slim")))


class ListValidation(unittest.TestCase):
    def errors(self, text):
        return m.validate(m.parse_list(text))

    def test_valid(self):
        self.assertEqual(self.errors(listing(entry(), entry("quay.io/keycloak/keycloak", "26.8.2", D2))), [])

    def test_repository_list_is_valid_when_resolved(self):
        entries = m.load_list()
        self.assertGreater(len(entries), 20)
        # the only errors allowed in the committed list are unresolved digests, each one marked TODO
        self.assertEqual([e for e in m.validate(entries) if not e.startswith("TODO:")], [])

    def test_missing_or_bad_digest(self):
        for src in ("public.ecr.aws/docker/library/postgres", "public.ecr.aws/docker/library/postgres@sha256:abc",
                    "public.ecr.aws/docker/library/postgres:17-alpine"):
            text = f'images:\n  - source: {src}\n    tag: "17-alpine"\n    target: ghcr.io/idp-xyz/mirror/public.ecr.aws/docker/library/postgres:17-alpine\n'
            self.assertTrue(self.errors(text), src)

    def test_todo_digest_is_an_error_that_says_so(self):
        errs = self.errors(listing(entry(digest="sha256:TODO")))
        self.assertEqual(len(errs), 1)
        self.assertTrue(errs[0].startswith("TODO:"))

    def test_target_must_follow_the_rule(self):
        errs = self.errors(listing(entry(target="ghcr.io/idp-xyz/mirror/public.ecr.aws/docker/library/postgres:17-alpine")))
        self.assertTrue(any("naming rule" in e for e in errs), errs)
        errs = self.errors(listing(entry(target="ghcr.io/idp-xyz/mirror:postgres-17")))
        self.assertTrue(any("naming rule" in e for e in errs), errs)
        errs = self.errors(listing(entry(target="ghcr.io/idp-xyz/postgres:17-alpine")))
        self.assertTrue(any("naming rule" in e for e in errs), errs)

    def test_duplicates(self):
        errs = self.errors(listing(entry(), entry(digest=D2)))
        self.assertTrue(any("duplicate" in e for e in errs), errs)

    def test_two_entries_that_encode_to_the_same_tag(self):
        a = entry("quay.io/a__b/c", "1", D1)
        b = entry("quay.io/a/b__c", "1", D2)
        self.assertEqual(m.encode_tag(m.parse_ref("quay.io/a__b/c:1")), m.encode_tag(m.parse_ref("quay.io/a/b__c:1")))
        errs = self.errors(listing(a, b))
        self.assertTrue(any("same tag" in e for e in errs), errs)

    def test_tag_longer_than_128_characters(self):
        long_path = "quay.io/" + "/".join(["a" * 30] * 4)
        errs = self.errors(listing(entry(long_path, "1.0")))
        self.assertTrue(any("128" in e for e in errs), errs)

    def test_resolved_via(self):
        self.assertEqual(self.errors(listing(entry(via="mirrors"))), [])
        self.assertTrue(self.errors(listing(entry(via="guess"))))
        self.assertEqual(m.parse_list(listing(entry(via="mirrors")))[0].resolved_via, "mirrors")

    def test_unknown_registry_and_own_image(self):
        self.assertTrue(self.errors(listing(entry("registry.example.com/x/y", "1"))))
        self.assertTrue(any("own image" in e for e in self.errors(listing(entry("ghcr.io/idp-xyz/idp-cmi", "abc")))))

    def test_alias_rules(self):
        ok = entry("public.ecr.aws/docker/library/alpine", "3.22", aliases="docker.io/library/alpine:3.22")
        self.assertEqual(self.errors(listing(ok)), [])
        other_tag = entry("public.ecr.aws/docker/library/alpine", "3.22", aliases="docker.io/library/alpine:3.21")
        self.assertTrue(self.errors(listing(other_tag)))
        twice = ok + entry("public.ecr.aws/docker/library/busybox", "3.22", D2, aliases="docker.io/library/alpine:3.22")
        self.assertTrue(any("already used" in e for e in self.errors(listing(twice))))

    def test_unknown_key_and_garbage(self):
        with self.assertRaises(ValueError):
            m.parse_list("images:\n  - source: x\n    colour: red\n")
        with self.assertRaises(ValueError):
            m.parse_list("images:\n  not yaml at all\n")

    def test_the_parser_agrees_with_yaml_when_it_is_installed(self):
        try:
            import yaml
        except ImportError:
            self.skipTest("PyYAML is not installed")
        text = m.LIST.read_text()
        want = yaml.safe_load(text)["images"]
        got = m.parse_list(text)
        self.assertEqual([(w["source"], w["tag"], w["target"], w.get("aliases", [])) for w in want],
                         [(g.source, g.tag, g.target, g.aliases) for g in got])


def refs(rel, text, repo="r"):
    return [(f.line, f.text, f.ref.text() if f.ref else None) for f in m.scan_text(repo, rel, text)]


class Finder(unittest.TestCase):
    def test_dockerfile_from_with_and_without_digest_and_as(self):
        text = (f"FROM public.ecr.aws/docker/library/postgres:17-alpine\n"
                f"FROM --platform=linux/amd64 golang:1.26 AS build\n"
                f"FROM quay.io/x/y:1@{D1} AS final\n"
                f"FROM build AS test\n"          # a stage, not an image
                f"FROM scratch\n"
                f"FROM ghcr.io/idp-xyz/idp-cmi:abc\n")
        self.assertEqual(refs("Dockerfile", text), [
            (1, "public.ecr.aws/docker/library/postgres:17-alpine", "public.ecr.aws/docker/library/postgres:17-alpine"),
            (2, "golang:1.26", "docker.io/library/golang:1.26"),
            (3, f"quay.io/x/y:1@{D1}", f"quay.io/x/y:1@{D1}"),
        ])

    def test_dockerfile_arg_default_is_reported_at_the_arg(self):
        text = "ARG GO_IMAGE=golang:1.26.11-bookworm\nARG OTHER=1\nFROM ${GO_IMAGE} AS source\nRUN true\n"
        self.assertEqual(refs("runtime/Dockerfile", text), [(1, "golang:1.26.11-bookworm", "docker.io/library/golang:1.26.11-bookworm")])

    def test_dockerfile_variable_without_default_cannot_be_checked(self):
        found = m.scan_text("r", "Dockerfile", "FROM ${BASE}\n")
        self.assertEqual(len(found), 1)
        self.assertIsNone(found[0].ref)
        self.assertIn("variable", found[0].why)

    def test_yaml_image_key(self):
        text = ("services:\n  db:\n    image: ${IDP_PG_IMAGE:-pgvector/pgvector:0.8.7-pg17}\n"
                "  app:\n    image: ${IDP_APP_IMAGE:-idp-host:dev}\n"
                "    containers:\n      - image: quay.io/keycloak/keycloak:26.8.2   # keycloak\n"
                "      - image: idp-cmi\n      - image: \"python:3.12-slim\"\n      - image: local/temporal:1.10.0\n")
        self.assertEqual([r[2] for r in refs("compose.yaml", text)], [
            "docker.io/pgvector/pgvector:0.8.7-pg17", "quay.io/keycloak/keycloak:26.8.2", "docker.io/library/python:3.12-slim"])

    def test_yaml_image_from_a_variable_is_reported_not_skipped(self):
        found = m.scan_text("r", "k.yaml", "      image: $IMG\n")
        self.assertEqual([(f.text, f.ref, f.why) for f in found], [("$IMG", None, "built from a variable")])

    def test_variable_resolved_from_the_same_file(self):
        text = "CALICO=v3.30.3\nIMAGES=(quay.io/calico/cni:$CALICO quay.io/calico/node:${CALICO})\n"
        self.assertEqual([r[2] for r in refs("x.sh", text)], ["quay.io/calico/cni:v3.30.3", "quay.io/calico/node:v3.30.3"])

    def test_variable_not_resolved_in_a_qualified_reference(self):
        found = m.scan_text("r", "x.sh", 'crane pull "quay.io/calico/$i:v3" x.tar\n')
        self.assertEqual([(f.ref, f.why) for f in found], [(None, "built from a variable")])

    def test_shell_and_python_strings(self):
        sh = ('bash tools/pull_image.sh public.ecr.aws/docker/library/python:3.12-slim public.ecr.aws/docker/library/alpine:3.22\n'
              'docker run --rm --user 65534:65534 -v "$T:/keys:ro" -p 127.0.0.1::5432 temporalio/temporal:1.10.0 server\n'
              'PG_IMAGE ?= public.ecr.aws/docker/library/postgres:17-alpine\n'
              'ensure_image docker.io/rustfs/rustfs:1.0.4-glibc\n'
              'NODE=kindest/node:v1.35.0\n'
              'docker run -it idp-sandbox-office:dev sh\n')
        got = [r[2] for r in refs("run.sh", sh)]
        self.assertEqual(got, ["public.ecr.aws/docker/library/alpine:3.22", "public.ecr.aws/docker/library/python:3.12-slim",
                               "docker.io/temporalio/temporal:1.10.0", "public.ecr.aws/docker/library/postgres:17-alpine",
                               "docker.io/rustfs/rustfs:1.0.4-glibc", "docker.io/kindest/node:v1.35.0"])
        py = 'IMAGE = "public.ecr.aws/docker/library/postgres:17-alpine"\nOTHER = "ghcr.io/idp-xyz/idp-host:abc"\nX = "localhost:8080"\n'
        self.assertEqual([r[2] for r in refs("h.py", py)], ["public.ecr.aws/docker/library/postgres:17-alpine"])

    def test_pull_script_argument_from_a_variable_is_reported(self):
        found = m.scan_text("r", "x.sh", 'bash tools/pull_image.sh "$BASE"\n')
        self.assertEqual([(f.text, f.ref) for f in found], [("$BASE", None)])

    def test_digest_only_reference(self):
        text = f"          docker run --rm prom/prometheus@{D1} test rules x\n"
        self.assertEqual([r[2] for r in refs("ci.yml", text)], [f"docker.io/prom/prometheus@{D1}"])

    def test_own_scratch_and_prose_ignored(self):
        text = ("# see ghcr.io/idp-xyz/idp-cmi:abc and idp-host:dev and local/temporal:1.10.0\n"
                "FROM scratch\n  image: idp-host-fakemodel:dev\nurl: http://example.org:8080/x\nmount: /tmp:rw,noexec\n")
        self.assertEqual(refs("Dockerfile", text), [])

    def test_docs_count_only_qualified_references_with_a_tag(self):
        md = ("Use python:3.12-slim for it.\nThe image quay.io/keycloak/keycloak:26.8.2 runs it.\n"
              "Prefix quay.io/calico/ and ghcr.io/idpxyz/eer-runtime:vX.Y.Z and ghcr.io/huggingface/text-embeddings-inference\n")
        self.assertEqual([r[2] for r in refs("README.md", md)], ["quay.io/keycloak/keycloak:26.8.2"])

    def test_fallback_mirrors_of_the_developer_are_ignored(self):
        self.assertEqual(refs("a.sh", '  "docker.m.daocloud.io/temporalio/temporal:1.7.3" \\\n'), [])

    def test_unknown_registry_is_reported(self):
        self.assertEqual([r[2] for r in refs("a.yaml", "image: registry.example.com/team/app:1.2\n")], ["registry.example.com/team/app:1.2"])


class Matching(unittest.TestCase):
    def setUp(self):
        self.entries = m.parse_list(listing(
            entry("public.ecr.aws/docker/library/python", "3.12-slim", D1, aliases="docker.io/library/python:3.12-slim"),
            entry("docker.io/prom/prometheus", "v3.14.0", D2)))
        self.idx = m.index_entries(self.entries)

    def test_alias_and_tag(self):
        e, why = m.match(m.parse_ref("python:3.12-slim"), self.idx)
        self.assertEqual((e, why), (self.entries[0], ""))
        e, why = m.match(m.parse_ref("python:3.13"), self.idx)
        self.assertEqual((e, why), (None, "not in the list"))

    def test_digest_only_and_other_digest(self):
        self.assertEqual(m.match(m.parse_ref(f"prom/prometheus@{D2}"), self.idx), (self.entries[1], ""))
        self.assertEqual(m.match(m.parse_ref(f"prom/prometheus@{D3}"), self.idx)[0], None)
        e, why = m.match(m.parse_ref(f"public.ecr.aws/docker/library/python:3.12-slim@{D3}"), self.idx)
        self.assertEqual(e, self.entries[0])
        self.assertIn("the list has", why)


class Repositories(unittest.TestCase):
    def make(self, root, files):
        for rel, text in files.items():
            p = Path(root, rel)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)

    def run_tool(self, *argv):
        out = io.StringIO()
        with redirect_stdout(out):
            code = m.main(list(argv))
        return code, out.getvalue()

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.make(self.root / "svc", {
            "Dockerfile": "FROM scratch\n",
            "compose.yaml": "services:\n  db:\n    image: public.ecr.aws/docker/library/postgres:17-alpine\n"
                            "  kc:\n    image: quay.io/keycloak/keycloak:26.8.2\n",
            "run.sh": "docker run --rm $WHAT\nbash tools/pull_image.sh $BASE\n",
        })
        self.make(self.root / "other", {"ci.yml": "      image: python:3.12-slim\n"})
        self.list = self.root / "list.yaml"
        self.list.write_text(listing(
            entry(),
            entry("public.ecr.aws/docker/library/python", "3.12-slim", D2, aliases="docker.io/library/python:3.12-slim"),
            entry("docker.io/rustfs/rustfs", "1.0.4-glibc", D3)))

    def test_reports_unlisted_unused_and_unchecked(self):
        code, out = self.run_tool("--list", str(self.list), "check", "--repos", str(self.root / "svc"), str(self.root / "other"))
        self.assertEqual(code, 1)
        self.assertIn("NOT IN THE LIST: quay.io/keycloak/keycloak:26.8.2", out)
        self.assertIn("note: unused in these repositories: ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc", out)
        self.assertIn("note: cannot be checked: svc:run.sh:2 $BASE", out)
        self.assertNotIn("postgres:17-alpine  (", out)
        self.assertNotIn("python:3.12-slim  (", out)      # an alias of a listed entry

    def test_a_source_reference_of_a_listed_image_is_not_the_mirror(self):
        self.list.write_text(self.list.read_text() + entry("quay.io/keycloak/keycloak", "26.8.2", D1))
        code, out = self.run_tool("--list", str(self.list), "check", "--repos", str(self.root / "svc"), str(self.root / "other"))
        self.assertEqual(code, 1)
        self.assertIn("NOT THE MIRROR: svc:compose.yaml:3 public.ecr.aws/docker/library/postgres:17-alpine -> ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine", out)
        self.assertIn("NOT THE MIRROR: other:ci.yml:1 python:3.12-slim", out)      # also by an alias or a short name

    def test_clean_once_switched(self):
        self.list.write_text(self.list.read_text() + entry("quay.io/keycloak/keycloak", "26.8.2", D1))
        repos = [str(self.root / "svc"), str(self.root / "other")]
        code, out = self.run_tool("--list", str(self.list), "switch", "--repos", *repos)
        self.assertEqual(code, 0, out)
        self.assertIn("3 switched, 0 to do by hand", out)
        self.assertIn("image: ghcr.io/idp-xyz/mirror:quay.io__keycloak__keycloak__26.8.2", (self.root / "svc" / "compose.yaml").read_text())
        code, out = self.run_tool("--list", str(self.list), "check", "--repos", *repos)
        self.assertEqual(code, 0, out)
        self.assertIn("3 name the mirror", out)
        code, out = self.run_tool("--list", str(self.list), "check", "--strict", "--repos", *repos)
        self.assertEqual(code, 1)    # strict: the unused entry and the unchecked reference fail
        self.assertIn("UNUSED", out)
        self.assertIn("UNCHECKED: svc:run.sh:2 $BASE", out)

    def test_markers(self):
        self.list.write_text(self.list.read_text() + entry("quay.io/keycloak/keycloak", "26.8.2", D1))
        self.make(self.root / "svc", {
            "compose.yaml": "services:\n  db:\n    image: ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine\n"
                            "  kc:\n    image: quay.io/keycloak/keycloak:26.8.2   # mirror:upstream: what to watch for releases\n",
            "run.sh": "docker run --rm $WHAT\n# mirror:variable (BASE is a mirror name)\nbash tools/pull_image.sh $BASE\n"})
        self.make(self.root / "other", {"ci.yml": f"      image: ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc@{D3}\n"})
        code, out = self.run_tool("--list", str(self.list), "check", "--strict", "--repos", str(self.root / "svc"), str(self.root / "other"))
        self.assertEqual(code, 1, out)         # the python entry is unused; nothing else is wrong
        self.assertIn("1 name an upstream on purpose", out)
        self.assertIn("1 are built from variables and reviewed", out)
        self.assertNotIn("UNCHECKED", out)
        self.assertNotIn("NOT THE MIRROR", out)
        self.assertEqual([l for l in out.splitlines() if l.startswith(("UNUSED", "NOT", "DIFFERS"))],
                         ["UNUSED: ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim (source public.ecr.aws/docker/library/python:3.12-slim)"])

    def test_short_names_without_a_registry_are_found_anywhere_in_code(self):
        self.make(self.root / "svc", {
            "main.go": 'cfg := New(env("PY", "python:3.12-slim"), "alpine:3.22")\n// docs say python:3.12-slim\n',
            "sb.yaml": "env:\n  - value: python:3.12-slim\n",
            "ok.sh": "x=ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim\nnot-python:3.12-slim-x\n"})
        code, out = self.run_tool("--list", str(self.list), "check", "--repos", str(self.root / "svc"))
        self.assertIn("NOT THE MIRROR: svc:main.go:1 python:3.12-slim", out)
        self.assertIn("NOT THE MIRROR: svc:sb.yaml:2 python:3.12-slim", out)
        self.assertNotIn("main.go:2", out)         # a comment line
        self.assertNotIn("ok.sh", out)
        self.run_tool("--list", str(self.list), "switch", "--repos", str(self.root / "svc"))
        self.assertIn('env("PY", "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim")', (self.root / "svc" / "main.go").read_text())

    def test_a_mirror_reference_must_be_in_the_list_and_keep_the_listed_digest(self):
        self.make(self.root / "svc", {
            "a.yaml": "image: ghcr.io/idp-xyz/mirror:docker.io__nobody__nothing__1\n",
            "b.yaml": f"image: ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc@{D1}\n",
            "c.md": "the package ghcr.io/idp-xyz/mirror is public\n"})
        code, out = self.run_tool("--list", str(self.list), "check", "--repos", str(self.root / "svc"))
        self.assertIn("NOT IN THE LIST: ghcr.io/idp-xyz/mirror:docker.io__nobody__nothing__1", out)
        self.assertIn("DIFFERS FROM THE LIST: svc:b.yaml:1", out)
        self.assertNotIn("c.md", out)          # a sentence about the package is not an image

    def test_switch_changes_a_digest_pin_that_the_mirror_does_not_hold(self):
        self.make(self.root / "svc", {"Dockerfile": f"FROM docker.io/rustfs/rustfs:1.0.4-glibc@{D1}\n"})
        code, out = self.run_tool("--list", str(self.list), "switch", "--repos", str(self.root / "svc"))
        self.assertIn(f"FROM ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc@{D3}", (self.root / "svc" / "Dockerfile").read_text())

    def test_switch_dry_run_writes_nothing_and_a_default_in_a_variable_is_found(self):
        self.make(self.root / "svc", {"x.sh": 'IMG="${X_IMAGE:-public.ecr.aws/docker/library/postgres:17-alpine}"\n'})
        before = (self.root / "svc" / "x.sh").read_text()
        code, out = self.run_tool("--list", str(self.list), "switch", "--dry-run", "--repos", str(self.root / "svc"))
        self.assertIn("WOULD SWITCH svc:x.sh:1", out)
        self.assertEqual((self.root / "svc" / "x.sh").read_text(), before)
        self.run_tool("--list", str(self.list), "switch", "--repos", str(self.root / "svc"))
        self.assertEqual((self.root / "svc" / "x.sh").read_text(),
                         'IMG="${X_IMAGE:-ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine}"\n')

    def test_invalid_list_fails_check_without_repos(self):
        self.list.write_text(listing(entry(), entry()))
        code, out = self.run_tool("--list", str(self.list), "check")
        self.assertEqual(code, 1)
        self.assertIn("duplicate", out)

    def test_plan_table(self):
        code, out = self.run_tool("--list", str(self.list), "plan", "--repos", str(self.root / "svc"), str(self.root / "other"))
        self.assertEqual(code, 0)
        rows = [line.split() for line in out.splitlines()]
        self.assertEqual(rows[0], ["repository", "file", "line", "current", "mirror"])
        self.assertIn(["svc", "compose.yaml", "3", "public.ecr.aws/docker/library/postgres:17-alpine",
                       "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine"], rows)
        self.assertIn(["other", "ci.yml", "1", "docker.io/library/python:3.12-slim",
                       "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__python__3.12-slim"], rows)
        self.assertTrue(any(r[:4] == ["svc", "compose.yaml", "5", "quay.io/keycloak/keycloak:26.8.2"] and r[4:6] == ["NOT", "IN"] for r in rows))
        self.assertTrue(any(r[:2] == ["svc", "run.sh"] and "CANNOT" in r for r in rows))
        self.assertIn("occurrences per repository: other 1, svc 3; total 4", out)

    def test_plan_keeps_a_digest(self):
        self.make(self.root / "svc", {"ci.yml": f"      image: docker.io/rustfs/rustfs:1.0.4-glibc@{D3}\n"})
        _, out = self.run_tool("--list", str(self.list), "plan", "--repos", str(self.root / "svc"))
        self.assertIn(f"ghcr.io/idp-xyz/mirror:docker.io__rustfs__rustfs__1.0.4-glibc@{D3}", out)

    def test_list_for_the_workflow(self):
        code, out = self.run_tool("--list", str(self.list), "list")
        self.assertEqual(code, 0)
        first = out.splitlines()[0].split("\t")
        self.assertEqual(first, [f"public.ecr.aws/docker/library/postgres@{D1}",
                                 "ghcr.io/idp-xyz/mirror:public.ecr.aws__docker__library__postgres__17-alpine", "17-alpine", "-"])
        code, out = self.run_tool("--list", str(self.list), "aliases")
        self.assertEqual(out.strip(), f"docker.io/library/python:3.12-slim\t{D2}")

    def test_local_names_for_retag(self):
        e = m.parse_list(listing(entry("docker.io/rustfs/rustfs", "1.0.4-glibc", D3),
                                 entry("public.ecr.aws/docker/library/python", "3.12-slim", D2, aliases="docker.io/library/python:3.12-slim")))
        self.assertEqual(m.local_names(e[0]), ["docker.io/rustfs/rustfs:1.0.4-glibc"])
        self.assertEqual(m.local_names(e[1]), ["public.ecr.aws/docker/library/python:3.12-slim", "docker.io/library/python:3.12-slim"])


class ResolveThroughMirrors(unittest.TestCase):
    """`resolve --via` with a fake crane on PATH: the answer of each mirror comes from the environment."""

    def run_resolve(self, answers):
        import os
        import stat
        import subprocess
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        bin_dir = Path(tmp.name, "bin")
        bin_dir.mkdir()
        crane = bin_dir / "crane"
        cases = "\n".join(f'  "digest {host}/prom/prometheus:v1") {"echo " + d if d else "echo nope >&2; exit 1"};;' for host, d in answers.items())
        crane.write_text(f'#!/bin/sh\ncase "$*" in\n{cases}\n  *) echo nope >&2; exit 1;;\nesac\n')
        crane.chmod(crane.stat().st_mode | stat.S_IEXEC)
        lst = Path(tmp.name, "list.yaml")
        lst.write_text(f"images:\n  # c\n  - source: docker.io/prom/prometheus@sha256:TODO\n    tag: \"v1\"\n"
                       f"    target: ghcr.io/idp-xyz/mirror:docker.io__prom__prometheus__v1\n")
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", PYTHONDONTWRITEBYTECODE="1", IDP_MIRROR_WAIT="0")
        r = subprocess.run([sys.executable, str(Path(m.__file__)), "--list", str(lst), "resolve", "--via", *answers], env=env,
                           capture_output=True, text=True)
        return r, lst.read_text()

    def test_two_that_agree_fill_the_digest_and_mark_the_entry(self):
        r, text = self.run_resolve({"m1.example": D1, "m2.example": D1})
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn(f"source: docker.io/prom/prometheus@{D1}", text)
        self.assertIn("    resolvedVia: mirrors\n", text)
        self.assertEqual(m.validate(m.parse_list(text)), [])

    def test_disagreement_or_a_single_answer_leaves_the_todo(self):
        for answers in ({"m1.example": D1, "m2.example": D2}, {"m1.example": D1, "m2.example": ""}):
            r, text = self.run_resolve(answers)
            self.assertEqual(r.returncode, 1, answers)
            self.assertIn("sha256:TODO", text)
            self.assertNotIn("resolvedVia", text)


if __name__ == "__main__":
    unittest.main()
