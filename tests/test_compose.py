"""Tests for the `docker compose` subset.

Two layers:

* Pure-unit tests for the YAML parser, interpolation, and translation
  helpers — no subprocess, no fake binary.
* End-to-end tests that drive `bin/docker compose ...` against a fake
  `container` binary which records state in a JSON file, so we can assert the
  stateless label-based orchestration without starting real VMs.
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKER = ROOT / "bin" / "docker"

sys.path.insert(0, str(ROOT / "src"))

from container_docker_shim import compose  # noqa: E402
from container_docker_shim import compose_capabilities  # noqa: E402
from container_docker_shim import compose_normalizer  # noqa: E402
from container_docker_shim import compose_resources  # noqa: E402

TIER = "fake"


# A fake `container` that models just enough of Apple's CLI for compose:
# run (records labels, entrypoint/cmd + a synthetic IP), list --format json,
# inspect (with a status.networks IPv4), image inspect (a fixed
# ENTRYPOINT/CMD), stop/rm, network/volume create/inspect/list/rm, exec.
FAKE_CONTAINER = r"""#!/usr/bin/env python3
import json, os, sys
from pathlib import Path

STATE = Path(os.environ["FAKE_CONTAINER_STATE"])


def load():
    try:
        d = json.loads(STATE.read_text())
    except FileNotFoundError:
        d = {"containers": {}, "networks": {}, "volumes": {}, "ip": 1, "exec_log": []}
    d.setdefault("net_index", {})
    d.setdefault("action_log", [])
    return d


def save(d):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(d))


def take(a, i):
    if a[i].startswith("--") and "=" in a[i]:
        return a[i].split("=", 1)[1], i + 1
    return a[i + 1], i + 2


def mount_entry(spec):
    # A `--mount` value as the runtime reports it under configuration.mounts.
    fields = {}
    for part in spec.split(","):
        k, _, v = part.partition("=")
        fields[k] = v
    kind = fields.get("type", "bind")
    source = fields.get("source", fields.get("src", ""))
    target = fields.get("target", fields.get("destination", fields.get("dst", "")))
    if kind == "volume":
        mount_type = {"volume": {"name": source}}
    elif kind == "tmpfs":
        mount_type = {"tmpfs": {}}
    else:
        mount_type = {"virtiofs": {}}
    return {"type": mount_type, "source": source, "destination": target, "options": []}


a = sys.argv[1:]

if os.environ.get("FAKE_CONTAINER_CALLS"):
    with open(os.environ["FAKE_CONTAINER_CALLS"], "a") as calls_log:
        calls_log.write(json.dumps(a) + "\n")

if a[:1] == ["--version"]:
    print("container CLI version 1.5.0 (fake)"); raise SystemExit(0)
if a[:2] == ["system", "status"]:
    print("apiserver is running"); raise SystemExit(0)

if a[:1] == ["run"]:
    d = load()
    name = None
    labels = {}
    mounts = []
    networks = []
    dns = []
    dns_search = []
    dns_opt = []
    image = None
    cmd = []
    entry = None
    value = {"--name","--label","--network","-e","--env","-p","--publish","-v",
             "--mount","--tmpfs","--cpus","--memory","--user","-w","--workdir",
             "--cwd","--entrypoint","--platform","--cap-add","--cap-drop",
             "--ulimit","--shm-size","--dns","--dns-search","--dns-option"}
    boolean = {"-d","-i","-t","--init","--read-only","--rm","--no-dns"}
    detached = False
    i = 1
    while i < len(a):
        arg = a[i]
        key = arg.split("=",1)[0] if arg.startswith("--") else arg
        if key in boolean:
            if key == "-d":
                detached = True
            i += 1; continue
        if key in value:
            v, i = take(a, i)
            if key == "--name": name = v
            elif key == "--label":
                k, _, val = v.partition("="); labels[k] = val
            elif key == "--mount": mounts.append(mount_entry(v))
            elif key == "--network": networks.append(v)
            elif key == "--dns": dns.append(v)
            elif key == "--dns-search": dns_search.append(v)
            elif key == "--dns-option": dns_opt.append(v)
            elif key == "--entrypoint": entry = v
            continue
        image = arg; cmd = a[i+1:]; break
    if not name:
        name = "fake-%d" % len(d["containers"])
    if name in d.get("fail_run", []) or (
        os.environ.get("FAKE_SEED_FAIL") and name.endswith("-seed")
    ):
        # Scripted launch failure: nothing is created.
        d["action_log"].append(["run-failed", name])
        save(d)
        print("failed to start " + name + " (scripted)", file=sys.stderr)
        raise SystemExit(1)
    if not networks:
        networks = ["default"]
    status_networks = []
    for net in networks:
        if net not in d["net_index"]:
            d["net_index"][net] = 65 + len(d["net_index"])
        octet = d["net_index"][net]
        ip = "192.168.%d.%d" % (octet, d["ip"]); d["ip"] += 1
        status_networks.append({"network": net,
                                "ipv4Address": ip + "/24",
                                "ipv4Gateway": "192.168.%d.1" % octet})
    d["containers"][name] = {
        "id": name, "name": name, "image": image or "",
        "labels": labels, "networks": networks, "dns": dns,
        "dns_search": dns_search, "dns_opt": dns_opt,
        "entrypoint": entry, "cmd": cmd, "run_argv": a,
        "configuration": {"id": name, "labels": labels, "mounts": mounts,
                          "image": {"reference": image or ""}},
        "status": {"state": "running", "networks": status_networks},
    }
    if name in d.get("exit_after_run", []):
        d["containers"][name]["status"] = {"state": "stopped", "networks": []}
    d["action_log"].append(["run", name])
    if not detached:
        # Foreground run: the container runs to completion; its exit code is
        # the scripted one (exit_codes: {container: code}), default 0.
        d["containers"][name]["status"] = {"state": "stopped", "networks": []}
        if "--rm" in a:
            d["containers"].pop(name)  # `--rm` removes the container once it exits
        save(d); raise SystemExit(d.get("exit_codes", {}).get(name, 0))
    save(d); print(name); raise SystemExit(0)

if a[:2] == ["image", "inspect"]:
    if a[-1] in os.environ.get("FAKE_CONTAINER_MISSING_IMAGES", "").split(","):
        print("image not found", file=sys.stderr); raise SystemExit(1)
    print(json.dumps([{"id": "fakeid-" + a[-1], "configuration": {"name": a[-1]}, "variants": [
        {"platform": {"os": "linux", "architecture": "arm64"},
         "config": {"config": {"Entrypoint": ["/entry"], "Cmd": ["serve"]}}}]}]))
    raise SystemExit(0)

if a[:2] == ["image", "pull"]:
    d = load(); d["action_log"].append(["pull", a[-1]])
    save(d); print("PULLED " + a[-1]); raise SystemExit(0)

if a[:1] == ["list"]:
    d = load(); allc = "--all" in a
    rows = [c for c in d["containers"].values()
            if allc or c["status"]["state"] == "running"]
    print(json.dumps([{k: c[k] for k in ("id", "configuration", "status")} for c in rows])); raise SystemExit(0)

if a[:1] == ["inspect"]:
    d = load(); ident = a[-1]; c = d["containers"].get(ident)
    if not c:
        print("No such container", file=sys.stderr); raise SystemExit(1)
    print(json.dumps([{k: c[k] for k in ("id", "configuration", "status")}])); raise SystemExit(0)

if a[:1] == ["stop"]:
    d = load(); ident = a[-1]
    if ident in d["containers"]:
        d["containers"][ident]["status"]["state"] = "stopped"
    d["action_log"].append(["stop", ident])
    save(d); print(ident); raise SystemExit(0)

if a[:1] == ["start"]:
    d = load(); ident = a[-1]
    if ident in d["containers"]:
        d["containers"][ident]["status"]["state"] = "running"
    d["action_log"].append(["start", ident])
    save(d); print(ident); raise SystemExit(0)

if a[:1] == ["rm"]:
    d = load(); ident = a[-1]; d["containers"].pop(ident, None)
    d["action_log"].append(["rm", ident])
    save(d); print(ident); raise SystemExit(0)

if a[:1] == ["exec"]:
    d = load()
    # skip exec opts, find container id then command
    i = 1
    while i < len(a) and a[i].startswith("-"):
        i += 2 if a[i] in ("-e","-w","--user","--env-file") else 1
    ident = a[i]; cmd = a[i+1:]
    if ident in os.environ.get("FAKE_NO_SHELL", "").split(","):
        print("no such executable: sh", file=sys.stderr); raise SystemExit(1)
    d["exec_log"].append({"container": ident, "cmd": cmd})
    d["action_log"].append(["exec", ident, *cmd])
    # exec_script: {container: [return codes]}; consumed in order, the last
    # one repeats. Containers without a script always succeed.
    script = d.get("exec_script", {}).get(ident)
    rc = 0
    if script:
        rc = script.pop(0) if len(script) > 1 else script[0]
    save(d)
    if rc:
        print("check failed (scripted)", file=sys.stderr)
    raise SystemExit(rc)

if a[:1] in (["cp"], ["copy"]):
    d = load(); src, dst = a[1], a[2]
    if ":" in src:  # container -> local
        ident, _, path = src.partition(":")
        files = d["containers"].get(ident, {}).get("files", {})
        Path(dst).write_text(files.get(path, "127.0.0.1 localhost\n"))
    else:           # local -> container
        ident, _, path = dst.partition(":")
        d["containers"].setdefault(ident, {}).setdefault("files", {})[path] = Path(src).read_text()
        save(d)
    raise SystemExit(0)

if a[:1] == ["logs"]:
    print("LOGS " + " ".join(x for x in a[1:] if not x.startswith("-")))
    raise SystemExit(0)

if a[:2] == ["network", "create"]:
    d = load(); labels = {}; name = None; i = 2
    while i < len(a):
        if a[i] == "--label":
            v, i = take(a, i); k,_,val = v.partition("="); labels[k]=val; continue
        if a[i] == "--internal": i += 1; continue
        name = a[i]; i += 1
    d["networks"][name] = {"configuration": {"name": name, "labels": labels}, "id": name}
    save(d); print(name); raise SystemExit(0)

if a[:2] == ["network", "inspect"]:
    d = load(); name = a[-1]
    if name not in d["networks"]:
        print("no such network", file=sys.stderr); raise SystemExit(1)
    print(json.dumps([d["networks"][name]])); raise SystemExit(0)

if a[:2] == ["network", "list"]:
    d = load(); print(json.dumps(list(d["networks"].values()))); raise SystemExit(0)

if a[:2] == ["network", "rm"]:
    d = load(); name = a[-1]; d["networks"].pop(name, None)
    save(d); print(name); raise SystemExit(0)

if a[:2] == ["volume", "create"]:
    d = load(); labels = {}; name = None; i = 2
    while i < len(a):
        if a[i] == "--label":
            v, i = take(a, i); k,_,val = v.partition("="); labels[k]=val; continue
        name = a[i]; i += 1
    d["volumes"][name] = {"configuration": {"name": name, "labels": labels}, "id": name}
    save(d); print(name); raise SystemExit(0)

if a[:2] == ["volume", "inspect"]:
    d = load(); name = a[-1]
    if name not in d["volumes"]:
        print("no such volume", file=sys.stderr); raise SystemExit(1)
    print(json.dumps([d["volumes"][name]])); raise SystemExit(0)

if a[:2] == ["volume", "list"]:
    d = load(); print(json.dumps(list(d["volumes"].values()))); raise SystemExit(0)

if a[:2] == ["volume", "rm"]:
    if a[-1] in os.environ.get("FAKE_VOLUME_RM_FAIL", "").split(","):
        print("volume " + a[-1] + " is in use", file=sys.stderr); raise SystemExit(1)
    d = load(); name = a[-1]; d["volumes"].pop(name, None)
    save(d); print(name); raise SystemExit(0)

print("unsupported fake container args: " + " ".join(a), file=sys.stderr)
raise SystemExit(2)
"""


# --------------------------------------------------------------------------- #
# Pure-unit tests (no subprocess)
# --------------------------------------------------------------------------- #


class ParserTests(unittest.TestCase):
    def test_nested_map_and_seq(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  web:
                    image: nginx:latest
                    ports:
                      - "8080:80"
                    environment:
                      FOO: bar
                """
            )
        )
        self.assertEqual(doc["services"]["web"]["image"], "nginx:latest")
        self.assertEqual(doc["services"]["web"]["ports"], ["8080:80"])
        self.assertEqual(doc["services"]["web"]["environment"], {"FOO": "bar"})

    def test_flow_collections(self) -> None:
        doc = compose.parse_yaml("services:\n  a:\n    depends_on: [x, y]\n    networks: {n: null}\n")
        self.assertEqual(doc["services"]["a"]["depends_on"], ["x", "y"])
        self.assertEqual(doc["services"]["a"]["networks"], {"n": None})

    def test_scalar_typing(self) -> None:
        doc = compose.parse_yaml("a: true\nb: 42\nc: 1.5\nd: ~\ne: 'quoted'\n")
        self.assertEqual(doc, {"a": True, "b": 42, "c": 1.5, "d": None, "e": "quoted"})

    def test_comments_and_blank_lines(self) -> None:
        doc = compose.parse_yaml("# top\nservices:\n  a:  # inline\n    image: x  # trailing\n")
        self.assertEqual(doc["services"]["a"]["image"], "x")

    def test_list_of_maps(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  a:
                    image: x
                    ulimits:
                      - nofile=1024
                """
            )
        )
        self.assertEqual(doc["services"]["a"]["ulimits"], ["nofile=1024"])


class InterpolationTests(unittest.TestCase):
    def test_default_value(self) -> None:
        self.assertEqual(compose._interpolate_str("${X:-fallback}", {}), "fallback")
        self.assertEqual(compose._interpolate_str("${X:-fallback}", {"X": "set"}), "set")

    def test_required_missing_raises(self) -> None:
        with self.assertRaises(compose.ShimError):
            compose._interpolate_str("${X:?must set}", {})

    def test_literal_dollar(self) -> None:
        self.assertEqual(compose._interpolate_str("price is $$5", {}), "price is $5")

    def test_bare_var(self) -> None:
        self.assertEqual(compose._interpolate_str("$HOME/x", {"HOME": "/root"}), "/root/x")


class TopoSortTests(unittest.TestCase):
    def test_dependency_order(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  web:
                    image: w
                    depends_on: [db, cache]
                  db:
                    image: d
                  cache:
                    image: c
                """
            )
        )
        proj = compose.Project("p", doc, "/tmp/p")
        order = [s.name for s in proj.topo_sorted()]
        self.assertLess(order.index("db"), order.index("web"))
        self.assertLess(order.index("cache"), order.index("web"))

    def test_cycle_detected(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  a:
                    image: a
                    depends_on: [b]
                  b:
                    image: b
                    depends_on: [a]
                """
            )
        )
        proj = compose.Project("p", doc, "/tmp/p")
        with self.assertRaises(compose.ShimError):
            proj.topo_sorted()


class TranslationTests(unittest.TestCase):
    def test_port_forms(self) -> None:
        self.assertEqual(compose._compose_port_to_publish("6379"), "0.0.0.0:6379:6379")
        self.assertEqual(compose._compose_port_to_publish("8080:80"), "0.0.0.0:8080:80")
        self.assertEqual(compose._compose_port_to_publish("443:443/tcp"), "0.0.0.0:443:443/tcp")
        self.assertEqual(
            compose._compose_port_to_publish("127.0.0.1:8080:80"), "127.0.0.1:8080:80"
        )

    def test_project_name_sanitize(self) -> None:
        self.assertEqual(compose._sanitize_project_name("My.App"), "my_app")
        self.assertEqual(compose._sanitize_project_name("---"), "default")

    def test_command_string_is_shell_split(self) -> None:
        # Compose splits string commands with shell lexical rules; it does NOT
        # wrap them in `sh -c` — that would smuggle `/bin/sh -c ...` into the
        # image ENTRYPOINT's argv (Dockerfile shell form is a different layer).
        self.assertEqual(
            compose._command_list("nginx -g 'daemon off;'"),
            ["nginx", "-g", "daemon off;"],
        )

    def test_command_list_passthrough(self) -> None:
        self.assertEqual(compose._command_list(["redis-server", "--port", "6379"]),
                         ["redis-server", "--port", "6379"])


class RunArgsSecurityTests(unittest.TestCase):
    """Security-related `container run` flags without the fake binary."""

    def pairs(self, args: list[str]) -> list[list[str]]:
        """Consecutive [flag, value] pairs for assertions."""
        return [
            [args[i], args[i + 1]]
            for i, tok in enumerate(args)
            if tok.startswith("--") and i + 1 < len(args)
        ]

    def build(self, spec: dict) -> list[str]:
        svc = compose.Service("web", spec)
        project = compose.Project("demo", {"services": {"web": spec}}, "/tmp")
        args, _ = compose._build_run_args(
            svc, project, image="img:1", ensured_volumes=set(), warned=set()
        )
        return args

    def test_ulimit_list_form(self) -> None:
        args = self.build({"image": "img:1", "ulimits": ["nofile=1024"]})
        self.assertIn(["--ulimit", "nofile=1024"], self.pairs(args))

    def test_ulimit_map_form(self) -> None:
        args = self.build(
            {
                "image": "img:1",
                "ulimits": {"nproc": 65535, "nofile": {"soft": 20000, "hard": 40000}},
            }
        )
        self.assertIn(["--ulimit", "nproc=65535"], self.pairs(args))
        self.assertIn(["--ulimit", "nofile=20000:40000"], self.pairs(args))

    def test_read_only_flag(self) -> None:
        args = self.build({"image": "img:1", "read_only": True})
        self.assertIn("--read-only", args)
        args = self.build({"image": "img:1", "read_only": False})
        self.assertNotIn("--read-only", args)

    def test_init_flag(self) -> None:
        args = self.build({"image": "img:1", "init": True})
        self.assertIn("--init", args)

    def test_capabilities_not_normalized(self) -> None:
        args = self.build(
            {
                "image": "img:1",
                "cap_add": ["NET_ADMIN", "CAP_SYS_TIME"],
                "cap_drop": ["ALL"],
            }
        )
        self.assertIn(["--cap-add", "NET_ADMIN"], self.pairs(args))
        self.assertIn(["--cap-add", "CAP_SYS_TIME"], self.pairs(args))
        self.assertIn(["--cap-drop", "ALL"], self.pairs(args))

    def test_user_pair(self) -> None:
        args = self.build({"image": "img:1", "user": "1000:1000"})
        self.assertIn(["--user", "1000:1000"], self.pairs(args))

    def test_tmpfs_forms(self) -> None:
        args = self.build({"image": "img:1", "tmpfs": ["/tmp:rw,size=64m"]})
        self.assertIn(["--tmpfs", "/tmp:rw,size=64m"], self.pairs(args))
        args = self.build({"image": "img:1", "tmpfs": "/run"})
        self.assertIn(["--tmpfs", "/run"], self.pairs(args))


# --------------------------------------------------------------------------- #
# End-to-end tests against the fake container binary
# --------------------------------------------------------------------------- #


# Runtime verbs a dry run may use: reads only (design D7).
READ_ONLY_CALLS = (
    ["--version"],
    ["system", "status"],
    ["inspect"],
    ["list"],
    ["image", "inspect"],
    ["volume", "inspect"],
    ["network", "inspect"],
    ["volume", "list"],
    ["network", "list"],
)


def mutating_calls(calls: list) -> list:
    """The runtime calls that are not on the read-only allow-list."""
    return [
        call for call in calls
        if not any(call[: len(allowed)] == allowed for allowed in READ_ONLY_CALLS)
    ]


class ComposeFakeBase(unittest.TestCase):
    """Fake-`container` infrastructure shared by the compose end-to-end tests:
    the throwaway fake binary, its JSON state file and the call log of every
    argv it receives, plus the helpers that read them."""

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.fake = self.root / "container"
        self.fake.write_text(FAKE_CONTAINER)
        self.fake.chmod(0o755)
        self.state = self.root / "fake-state.json"
        self.project = self.root / "proj"
        self.project.mkdir()
        self.env = os.environ.copy()
        self.env.update(
            {
                "CONTAINER_DOCKER_SHIM_CONTAINER": str(self.fake),
                "FAKE_CONTAINER_STATE": str(self.state),
                "FAKE_CONTAINER_CALLS": str(self.root / "calls.log"),
            }
        )
        # Keep project name deterministic regardless of the temp dir name.
        self.env["COMPOSE_PROJECT_NAME"] = "demo"
        # These tests exercise the built-in parser, so hide any installed
        # `docker-compose` from PATH and drop inherited compose variables.
        for variable in ("COMPOSE_FILE", "COMPOSE_PROFILES", "COMPOSE_PATH_SEPARATOR"):
            self.env.pop(variable, None)
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = ""
        self.env["PATH"] = os.pathsep.join(
            entry
            for entry in self.env.get("PATH", "").split(os.pathsep)
            if not os.path.exists(os.path.join(entry, "docker-compose"))
        )

    def write_compose(self, body: str) -> None:
        (self.project / "docker-compose.yml").write_text(textwrap.dedent(body))

    def docker(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(DOCKER), *args],
            text=True,
            capture_output=True,
            env=self.env,
            cwd=str(self.project),
            check=False,
        )

    def load_state(self) -> dict:
        return json.loads(self.state.read_text())

    def runtime_calls(self) -> list:
        """Every argv the fake `container` received, in order."""
        log = self.root / "calls.log"
        if not log.exists():
            return []
        return [json.loads(line) for line in log.read_text().splitlines()]

    def assert_nothing_was_created(self) -> None:
        """No network, volume, image build or container call reached the runtime."""
        mutating = [
            call for call in self.runtime_calls()
            if call[:2] in (["network", "create"], ["volume", "create"])
            or call[:1] in (["build"], ["run"], ["create"])
        ]
        self.assertEqual(mutating, [])

    def created_networks(self) -> list:
        """Names of every network the runtime was asked to create, in order."""
        return [
            call[-1]
            for call in self.runtime_calls()
            if call[:2] == ["network", "create"]
        ]

    def clear_action_log(self) -> None:
        state = self.load_state()
        state["action_log"] = []
        self.state.write_text(json.dumps(state))

    def _seed_state(self) -> None:
        # A refused `up` never reaches the fake runtime, so give the state
        # file the same defaults the fake binary would create on first run.
        self.state.write_text(
            json.dumps(
                {
                    "containers": {},
                    "networks": {},
                    "volumes": {},
                    "ip": 1,
                    "exec_log": [],
                    "action_log": [],
                }
            )
        )

    def _seed_resource(self, family: str, name: str, labels: dict) -> None:
        self._seed_state() if not self.state.exists() else None
        state = self.load_state()
        state[family][name] = {"configuration": {"name": name, "labels": labels}, "id": name}
        self.state.write_text(json.dumps(state))


class ComposeE2ETests(ComposeFakeBase):
    def script_run_failure(self, container: str, fail: bool = True) -> None:
        """Make `container run` for `container` fail (or stop failing)."""
        if not self.state.exists():
            self._seed_state()
        state = self.load_state()
        names = [n for n in state.get("fail_run", []) if n != container]
        if fail:
            names.append(container)
        state["fail_run"] = names
        self.state.write_text(json.dumps(state))

    def script_exit(self, container: str, code: int) -> None:
        """Make a foreground `run` of `container` exit with `code`."""
        if not self.state.exists():
            self._seed_state()
        state = self.load_state()
        state.setdefault("exit_codes", {})[container] = code
        self.state.write_text(json.dumps(state))

    def script_exec(self, container: str, codes: list[int]) -> None:
        """Make the fake `exec` return `codes` for `container` (last one repeats)."""
        if not self.state.exists():
            self._seed_state()
        state = self.load_state()
        state.setdefault("exec_script", {})[container] = list(codes)
        self.state.write_text(json.dumps(state))

    def script_early_exit(self, container: str) -> None:
        """Make a detached `run` of `container` be stored already stopped."""
        if not self.state.exists():
            self._seed_state()
        state = self.load_state()
        state.setdefault("exit_after_run", []).append(container)
        self.state.write_text(json.dumps(state))

    def assert_sha256(self, value: str) -> None:
        """A config-hash label must be a 64-char lowercase hex digest."""
        self.assertEqual(len(value), 64)
        self.assertTrue(all(c in "0123456789abcdef" for c in value))

    def test_up_creates_labeled_containers_and_network(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                ports: ["8080:80"]
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        names = set(state["containers"])
        self.assertEqual(names, {"demo-web-1", "demo-db-1"})
        for name in names:
            labels = state["containers"][name]["labels"]
            self.assertEqual(labels["com.docker.compose.project"], "demo")
        self.assertIn("demo_default", state["networks"])
        self.assertEqual(
            state["networks"]["demo_default"]["configuration"]["labels"][
                "com.docker.compose.project"
            ],
            "demo",
        )

    def test_up_injects_peer_hostnames(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        state = self.load_state()
        # Every container should have received a peer /etc/hosts append.
        hosts_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])]
        self.assertEqual(len(hosts_writes), 2)
        joined = " ".join(" ".join(e["cmd"]) for e in hosts_writes)
        self.assertIn("web", joined)
        self.assertIn("db", joined)

    def test_external_network_is_attached_by_its_own_name_and_peers_resolve(self) -> None:
        self._seed_state()
        state = self.load_state()
        state["networks"]["edge"] = {"configuration": {"name": "edge", "labels": {}}, "id": "edge"}
        self.state.write_text(json.dumps(state))
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
              db:
                image: postgres:16
                networks: [edge]
            networks:
              edge:
                external: true
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        state = self.load_state()
        for name in ("demo-web-1", "demo-db-1"):
            argv = state["containers"][name]["run_argv"]
            self.assertEqual(argv[argv.index("--network") + 1], "edge")
        self.assertNotIn("demo_edge", state["networks"])
        self.assertEqual(self.created_networks(), [])
        self.assertNotIn("demo_default", state["networks"])
        hosts_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])]
        joined = " ".join(" ".join(e["cmd"]) for e in hosts_writes)
        self.assertIn("web", joined)
        self.assertIn("db", joined)

    def test_created_networks_reports_only_the_networks_up_created(self) -> None:
        # The helper reads the runtime call log, not the container-only action
        # log: with the project network already present, `up` creates only
        # `demo_back`, and the name it reports is the key the fake stored it
        # under.
        self._seed_state()
        state = self.load_state()
        state["networks"]["demo_default"] = {
            "configuration": {
                "name": "demo_default",
                "labels": {"com.docker.compose.project": "demo"},
            },
            "id": "demo_default",
        }
        self.state.write_text(json.dumps(state))
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        self.assertEqual(self.created_networks(), ["demo_back"])
        self.assertIn("demo_back", state["networks"])

    def test_up_injects_host_docker_internal(self) -> None:
        # Multi-service /etc/hosts writes must also publish the host-gateway
        # aliases (Docker Desktop parity), pointing at the network gateway,
        # via rewrite (strip managed names, append fresh) so re-runs and
        # partial ups never duplicate or shadow entries.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        state = self.load_state()
        hosts_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])]
        for entry in hosts_writes:
            script = " ".join(entry["cmd"])
            self.assertIn("192.168.65.1 host.docker.internal gateway.docker.internal", script)
            self.assertIn("grep -vE", script)  # replace semantics: strip managed names first

    def test_up_injects_db_address_into_api_hosts_and_replaces_it_on_second_run(self) -> None:
        # api depends_on db: db's *current* address must land in api's
        # /etc/hosts on every run, exactly once — a re-run that changes the
        # address replaces the stale line instead of duplicating it.
        self.write_compose(
            """
            services:
              api:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)

        def db_address(state: dict) -> str:
            return (
                state["containers"]["demo-db-1"]["status"]["networks"][0]["ipv4Address"]
                .split("/")[0]
            )

        def last_api_hosts_script(state: dict) -> str:
            entries = [e for e in state["exec_log"] if e["container"] == "demo-api-1"]
            self.assertTrue(entries)
            cmd = entries[-1]["cmd"]
            self.assertEqual(cmd[:2], ["sh", "-c"])
            return cmd[2]

        state = self.load_state()
        first_ip = db_address(state)
        script = last_api_hosts_script(state)
        self.assertIn("/etc/hosts", script)
        self.assertIn(f"{first_ip} db", script)

        # The runtime hands db a new address, then run `up` again: the shim
        # may or may not recreate the containers, but the /etc/hosts rewrite
        # api receives must carry db's current address exactly once, and must
        # not keep the old address as ` db`.
        self._move_container_address("demo-db-1", "192.168.65.210")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        current_ip = db_address(state)
        script = last_api_hosts_script(state)
        self.assertIn("/etc/hosts", script)
        self.assertIn(f"{current_ip} db", script)
        self.assertEqual(script.count(f"{current_ip} db"), 1)
        self.assertNotEqual(current_ip, first_ip)
        self.assertNotIn(f"{first_ip} db", script)

    def test_single_service_still_gets_host_gateway(self) -> None:
        # A lone service has no peers to resolve, but must still get
        # host.docker.internal — the old peers-only path skipped it entirely.
        self.write_compose(
            """
            services:
              solo:
                image: nginx:latest
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        state = self.load_state()
        hosts_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])]
        self.assertEqual(len(hosts_writes), 1)
        script = " ".join(hosts_writes[0]["cmd"])
        self.assertIn("host.docker.internal", script)
        # No peer line for itself (single service → no <ip> solo entry).
        self.assertNotIn(" solo", script)

    def test_dependent_service_boots_with_dependency_hosts(self) -> None:
        # web depends_on db: db starts first (topological order), so its IP is
        # known when web launches. The shim must wrap web's entrypoint so
        # /etc/hosts is written BEFORE the app runs — a fail-fast app dials its
        # database in its first millisecond, long before post-start `exec`
        # injection can land (and Apple container has no restart policies to
        # give it a second chance).
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                command: --flag on
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        web = state["containers"]["demo-web-1"]
        db = state["containers"]["demo-db-1"]
        self.assertIsNone(db["entrypoint"])  # no dependencies -> not wrapped
        self.assertEqual(web["entrypoint"], "/bin/sh")
        self.assertEqual(web["cmd"][0], "-c")
        script = web["cmd"][1]
        self.assertIn("192.168.65.1 db", script)  # db started first -> first IP
        self.assertIn("host.docker.internal", script)
        self.assertIn('exec "$@"', script)
        # The real argv rides behind the script: image ENTRYPOINT (from
        # `image inspect`) + the compose command — string form shell-split,
        # never sh -c wrapped, replacing the image CMD.
        self.assertEqual(web["cmd"][2:], ["sh", "/entry", "--flag", "on"])

    def test_compose_dns_options_are_forwarded_to_container_run(self) -> None:
        self.write_compose(
            """
            services:
              resolver:
                image: nginx:latest
                dns: [1.1.1.1, 8.8.8.8]
                dns_search: [svc.local]
                dns_opt: [use-vc]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        resolver = self.load_state()["containers"]["demo-resolver-1"]
        self.assertEqual(resolver["dns"], ["1.1.1.1", "8.8.8.8"])
        self.assertEqual(resolver["dns_search"], ["svc.local"])
        self.assertEqual(resolver["dns_opt"], ["use-vc"])

    def test_multi_network_injection_uses_shared_peer_addresses(self) -> None:
        # A service can put its Internet-facing network first for DNS/egress,
        # while private-only peers must still resolve it to the shared private
        # address. This mirrors Docker DNS behavior per receiver network.
        self.write_compose(
            """
            services:
              api:
                image: nginx:latest
                networks: [private]
              proxy:
                image: nginx:latest
                networks: [egress, private]
            networks:
              private:
                internal: true
              egress: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        proxy_nets = state["containers"]["demo-proxy-1"]["status"]["networks"]
        proxy_egress_ip = next(
            n["ipv4Address"].split("/")[0]
            for n in proxy_nets
            if n["network"] == "demo_egress"
        )
        proxy_private_ip = next(
            n["ipv4Address"].split("/")[0]
            for n in proxy_nets
            if n["network"] == "demo_private"
        )
        hosts_writes = [
            entry
            for entry in state["exec_log"]
            if "/etc/hosts" in " ".join(entry["cmd"])
        ]
        by_target = {e["container"]: " ".join(e["cmd"]) for e in hosts_writes}
        self.assertIn(f"{proxy_private_ip} proxy", by_target["demo-api-1"])
        self.assertNotIn(f"{proxy_egress_ip} proxy", by_target["demo-api-1"])

    def test_boot_hosts_use_shared_dependency_network(self) -> None:
        # Boot-time dependency injection follows the dependent service's
        # declared networks, not the dependency's first network.
        self.write_compose(
            """
            services:
              api:
                image: nginx:latest
                depends_on: [proxy]
                networks: [private]
              proxy:
                image: nginx:latest
                networks: [egress, private]
            networks:
              private:
                internal: true
              egress: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        proxy_nets = state["containers"]["demo-proxy-1"]["status"]["networks"]
        proxy_egress_ip = next(
            n["ipv4Address"].split("/")[0]
            for n in proxy_nets
            if n["network"] == "demo_egress"
        )
        proxy_private_ip = next(
            n["ipv4Address"].split("/")[0]
            for n in proxy_nets
            if n["network"] == "demo_private"
        )
        script = state["containers"]["demo-api-1"]["cmd"][1]
        self.assertIn(f"{proxy_private_ip} proxy", script)
        self.assertNotIn(f"{proxy_egress_ip} proxy", script)

    def test_partial_up_sees_whole_project_and_refreshes_peers(self) -> None:
        # `up -d web` after a full up: the recreated web (new IP) must still
        # learn db's name (peers come from the whole project, not the started
        # subset), and db must be re-injected with web's NEW address — the
        # resolver takes the first match, so stale lines must be replaced,
        # not shadowed. This is the exact failure that broke a live tunnel
        # sidecar restart (fresh container knew no peers at all).
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        result = self.docker("compose", "up", "-d", "web")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        web_ip = state["containers"]["demo-web-1"]["status"]["networks"][0]["ipv4Address"].split("/")[0]
        partial_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])][-2:]
        by_target = {e["container"]: " ".join(e["cmd"]) for e in partial_writes}
        self.assertIn("demo-web-1", by_target)
        self.assertIn("demo-db-1", by_target)
        self.assertIn(" db", by_target["demo-web-1"])       # fresh web knows db
        self.assertIn(f"{web_ip} web", by_target["demo-db-1"])  # db gets web's NEW ip

    def test_shell_less_image_gets_hosts_via_cp_fallback(self) -> None:
        # A distroless image (no /bin/sh — e.g. cloudflare/cloudflared) can't
        # take exec injection or the boot wrapper. The shim must fall back to
        # `container cp`: read /etc/hosts out, merge, write it back.
        self.write_compose(
            """
            services:
              edge:
                image: distroless/edge:latest
                x-shim-boot-hosts: false
                depends_on: [app]
              app:
                image: nginx:latest
            """
        )
        self.env["FAKE_NO_SHELL"] = "demo-edge-1"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("could not write /etc/hosts", result.stderr)
        state = self.load_state()
        hosts = state["containers"]["demo-edge-1"]["files"]["/etc/hosts"]
        self.assertIn(" app", hosts)
        self.assertIn("host.docker.internal", hosts)
        # And the shelled peer still got plain exec injection.
        self.assertTrue(any(e["container"] == "demo-app-1" for e in state["exec_log"]))

    def _move_container_address(self, container: str, new_ip: str) -> None:
        """Pretend the runtime gave `container` a new address after a restart."""
        state = self.load_state()
        net = state["containers"][container]["status"]["networks"][0]
        net["ipv4Address"] = f"{new_ip}/24"
        self.state.write_text(json.dumps(state))

    def test_restart_refreshes_peer_hosts_with_the_new_database_address(self) -> None:
        self.write_compose(
            """
            services:
              api:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        old_ip = self.load_state()["containers"]["demo-db-1"]["status"]["networks"][0]["ipv4Address"].split("/")[0]
        new_ip = "192.168.65.200"
        self.assertNotEqual(old_ip, new_ip)
        self._move_container_address("demo-db-1", new_ip)
        for command in (("compose", "restart", "db"), ("restart", "demo-db-1")):
            with self.subTest(command=command):
                self._move_container_address("demo-db-1", new_ip)
                state = self.load_state()
                state["exec_log"] = []
                self.state.write_text(json.dumps(state))
                result = self.docker(*command)
                self.assertEqual(result.returncode, 0, result.stderr)
                writes = [
                    " ".join(e["cmd"])
                    for e in self.load_state()["exec_log"]
                    if e["container"] == "demo-api-1" and "/etc/hosts" in " ".join(e["cmd"])
                ]
                self.assertTrue(writes, "api hosts were not refreshed after the restart")
                self.assertIn(f"{new_ip} db", writes[-1])
                self.assertNotIn(f"{old_ip} db", writes[-1])

    def test_restart_refreshes_a_shell_less_peer_through_cp(self) -> None:
        self.write_compose(
            """
            services:
              edge:
                image: distroless/edge:latest
                x-shim-boot-hosts: false
                depends_on: [app]
              app:
                image: nginx:latest
            """
        )
        self.env["FAKE_NO_SHELL"] = "demo-edge-1"
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        new_ip = "192.168.65.201"
        self._move_container_address("demo-app-1", new_ip)
        result = self.docker("compose", "restart", "app")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("could not write /etc/hosts", result.stderr)
        hosts = self.load_state()["containers"]["demo-edge-1"]["files"]["/etc/hosts"]
        self.assertIn(f"{new_ip} app", hosts)
        self.assertEqual(hosts.count(" app"), 1)

    def test_boot_hosts_wrapper_can_be_opted_out(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
                x-shim-boot-hosts: false
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        web = self.load_state()["containers"]["demo-web-1"]
        self.assertIsNone(web["entrypoint"])
        # Post-start injection still covers it.
        state = self.load_state()
        hosts_writes = [e for e in state["exec_log"] if "/etc/hosts" in " ".join(e["cmd"])]
        self.assertTrue(any(e["container"] == "demo-web-1" for e in hosts_writes))

    def test_ps_reconstructs_from_labels(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.docker("compose", "up", "-d")
        ps = self.docker("compose", "ps")
        self.assertEqual(ps.returncode, 0, ps.stderr)
        self.assertIn("demo-web-1", ps.stdout)
        self.assertIn("web", ps.stdout)

    def test_ps_json_and_quiet_have_stable_output(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.docker("compose", "up", "-d")
        ps = self.docker("compose", "ps", "--format", "json")
        self.assertEqual(ps.returncode, 0, ps.stderr)
        record = json.loads(ps.stdout)
        self.assertEqual(record["Service"], "web")
        self.assertEqual(record["State"], "running")
        self.assertIsInstance(record["ExitCode"], int)
        quiet = self.docker("compose", "ps", "-q")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(len(quiet.stdout.splitlines()), 1)
        self.assertNotIn("NAME", quiet.stdout)

    def test_ps_services_status_and_service_filters(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
              worker:
                image: busybox:latest
                command: ["sh", "-c", "sleep 600"]
            """
        )
        self.docker("compose", "up", "-d")
        services = self.docker("compose", "ps", "--services")
        self.assertEqual(services.returncode, 0, services.stderr)
        self.assertEqual(services.stdout.splitlines(), ["web", "worker"])
        filtered = self.docker("compose", "ps", "worker", "--status", "running", "--format=json")
        self.assertEqual(filtered.returncode, 0, filtered.stderr)
        self.assertEqual(json.loads(filtered.stdout)["Service"], "worker")

    def test_ps_refuses_templates_without_stdout(self) -> None:
        ps = self.docker("compose", "ps", "--format", "{{.Name}}")
        self.assertEqual(ps.returncode, 64)
        self.assertEqual(ps.stdout, "")

    def test_down_removes_only_owned_resources(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            volumes:
              data: {}
            """
        )
        # Pre-existing foreign container must survive a down.
        subprocess.run(
            [str(self.fake), "run", "-d", "--name", "outsider", "busybox"],
            env=self.env, check=True, capture_output=True,
        )
        self.docker("compose", "up", "-d")
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        state = self.load_state()
        self.assertIn("outsider", state["containers"])
        self.assertNotIn("demo-web-1", state["containers"])
        self.assertNotIn("demo_default", state["networks"])

    def test_down_v_removes_named_volumes(self) -> None:
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                volumes:
                  - data:/var/lib/postgresql/data
            volumes:
              data: {}
            """
        )
        self.docker("compose", "up", "-d")
        state = self.load_state()
        self.assertIn("demo_data", state["volumes"])
        self.docker("compose", "down", "-v")
        state = self.load_state()
        self.assertNotIn("demo_data", state["volumes"])

    def test_up_recreate_keeps_named_volumes_and_never_removes(self) -> None:
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                environment:
                  MODE: one
                volumes:
                  - data:/data
            volumes:
              data: {}
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        state = self.load_state()
        before = set(state["volumes"])
        self.assertIn("demo_data", before)
        # Change the environment so the shim must recreate the service.
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                environment:
                  MODE: two
                volumes:
                  - data:/data
            volumes:
              data: {}
            """
        )
        self.clear_action_log()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        actions = self.load_state()["action_log"]
        self.assertIn(["rm", "demo-db-1"], actions)
        self.assertLess(
            actions.index(["rm", "demo-db-1"]),
            actions.index(["run", "demo-db-1"]),
        )
        # The named volume created by the first `up` must survive the
        # recreation exactly: nothing removed, nothing duplicated.
        state = self.load_state()
        self.assertEqual(set(state["volumes"]), before)
        self.assertIn("demo_data", state["volumes"])
        for entry in actions:
            self.assertNotEqual(entry[0], "volume", entry)

    # -- Volume safety on `down` (external / foreign volumes survive) ------ #

    def _write_demo_compose(self) -> None:
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                volumes:
                  - data:/var/lib/postgresql/data
                  - shared:/shared
            volumes:
              data: {}
              shared:
                external: true
            """
        )

    def _seed_demo_volumes(self) -> None:
        # `shared` is external: it pre-exists with no project label and is
        # mounted by that exact name. `other` owns `other_data`. A `demo` down
        # must never touch either, and no `demo_shared` copy may ever appear.
        self._seed_state()
        state = self.load_state()
        state["volumes"]["shared"] = {
            "configuration": {"name": "shared", "labels": {}},
            "id": "shared",
        }
        state["volumes"]["other_data"] = {
            "configuration": {
                "name": "other_data",
                "labels": {"com.docker.compose.project": "other"},
            },
            "id": "other_data",
        }
        self.state.write_text(json.dumps(state))

    def test_down_without_v_keeps_all_volumes(self) -> None:
        self._write_demo_compose()
        self._seed_demo_volumes()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        state = self.load_state()
        self.assertIn("demo-db-1", state["containers"])
        before = set(state["volumes"])
        self.assertEqual(before, {"demo_data", "shared", "other_data"})
        self.assertNotIn("demo_shared", before)
        run_argv = state["containers"]["demo-db-1"]["run_argv"]
        self.assertIn("type=volume,source=shared,target=/shared", run_argv)
        self.assertIn("type=volume,source=demo_data,target=/var/lib/postgresql/data", run_argv)
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        state = self.load_state()
        self.assertNotIn("demo-db-1", state["containers"])
        self.assertIn("demo_data", state["volumes"])
        self.assertIn("shared", state["volumes"])
        self.assertIn("other_data", state["volumes"])
        self.assertEqual(set(state["volumes"]), before)  # no volume removed

    def test_down_v_removes_only_project_labeled_volumes(self) -> None:
        self._write_demo_compose()
        self._seed_demo_volumes()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        state = self.load_state()
        labeled = {
            name
            for name, vol in state["volumes"].items()
            if (vol["configuration"].get("labels") or {}).get(
                "com.docker.compose.project"
            )
            == "demo"
        }
        self.assertEqual(labeled, {"demo_data"})
        down = self.docker("compose", "down", "-v")
        self.assertEqual(down.returncode, 0, down.stderr)
        state = self.load_state()
        self.assertNotIn("demo_data", state["volumes"])
        self.assertNotIn("demo_shared", state["volumes"])
        self.assertIn("shared", state["volumes"])  # external: survives -v
        self.assertIn("other_data", state["volumes"])  # another project
        for name, vol in state["volumes"].items():
            self.assertNotEqual(
                (vol["configuration"].get("labels") or {}).get(
                    "com.docker.compose.project"
                ),
                "demo",
                f"volume {name} is still labelled for project demo",
            )

    def test_down_v_without_up_keeps_foreign_volumes(self) -> None:
        self._write_demo_compose()
        self._seed_demo_volumes()
        down = self.docker("compose", "down", "-v")
        self.assertEqual(down.returncode, 0, down.stderr)
        state = self.load_state()
        self.assertNotIn("demo-db-1", state["containers"])
        # No `up` ran, so no project volume exists; the set must be untouched.
        self.assertEqual(set(state["volumes"]), {"shared", "other_data"})

    # -- Existence pass before creation (external / foreign / unlabelled) --- #

    def _write_external_volume_compose(self) -> None:
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                volumes:
                  - shared:/shared
            volumes:
              shared:
                external: true
            """
        )

    def _assert_nothing_created(self) -> None:
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertNotIn("demo_default", state["networks"])
        self.assertFalse([n for n in state["volumes"] if n.startswith("demo_")])

    def test_missing_external_volume_exits_1_and_creates_nothing(self) -> None:
        self._seed_state()
        self._write_external_volume_compose()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("shared", up.stderr)
        self.assertIn("'db'", up.stderr)
        self._assert_nothing_created()

    def test_missing_external_network_exits_1_and_creates_nothing(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
            networks:
              edge:
                external: true
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("edge", up.stderr)
        self._assert_nothing_created()

    def test_managed_volume_labelled_for_another_project_is_refused(self) -> None:
        self._seed_resource("volumes", "demo_data", {"com.docker.compose.project": "other"})
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                volumes:
                  - data:/data
            volumes:
              data: {}
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("belongs to project", up.stderr)
        self.assertIn("other", up.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertNotIn("demo_default", state["networks"])

    def test_unlabelled_managed_volume_is_reused_with_a_notice_and_kept_by_down(self) -> None:
        self._seed_resource("volumes", "demo_data", {})
        self.write_compose(
            """
            services:
              db:
                image: postgres:16
                volumes:
                  - data:/data
            volumes:
              data: {}
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertIn("without a Compose label", up.stderr)
        down = self.docker("compose", "down", "-v")
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertIn("demo_data", self.load_state()["volumes"])

    def test_existing_external_resource_is_used_untouched(self) -> None:
        self._seed_resource("volumes", "shared", {"keep": "me"})
        self._write_external_volume_compose()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        vol = self.load_state()["volumes"]["shared"]
        self.assertEqual(vol["configuration"]["labels"], {"keep": "me"})

    def test_legacy_copy_gets_one_notice_and_is_left_in_place(self) -> None:
        self._seed_resource("volumes", "shared", {})
        self._seed_resource("volumes", "demo_shared", {"com.docker.compose.project": "demo"})
        self._write_external_volume_compose()
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(up.stderr.count("demo_shared"), 1)
        state = self.load_state()
        self.assertIn("demo_shared", state["volumes"])
        run_argv = state["containers"]["demo-db-1"]["run_argv"]
        self.assertIn("type=volume,source=shared,target=/shared", run_argv)


    # -- Teardown ownership: model AND label ------------------------------- #

    def _managed_data_compose(self, external: bool = False) -> None:
        declaration = "\n                external: true" if external else " {}"
        self.write_compose(
            f"""
            services:
              db:
                image: postgres:16
                volumes:
                  - data:/data
            volumes:
              data:{declaration}
            """
        )

    def test_down_v_protects_a_volume_now_declared_external(self) -> None:
        self._managed_data_compose()
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.assertIn("demo_data", self.load_state()["volumes"])
        # The file now declares `data` external, so `demo_data` is not ours to remove.
        self._managed_data_compose(external=True)
        self._seed_resource("volumes", "data", {})
        down = self.docker("compose", "down", "-v")
        self.assertEqual(down.returncode, 0, down.stderr)
        volumes = self.load_state()["volumes"]
        self.assertIn("demo_data", volumes)
        self.assertIn("data", volumes)

    def test_down_v_does_not_remove_a_labelled_volume_the_file_does_not_declare(self) -> None:
        self._seed_resource("volumes", "demo_stray", {"com.docker.compose.project": "demo"})
        self._managed_data_compose()
        down = self.docker("compose", "down", "-v")
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertIn("demo_stray", self.load_state()["volumes"])

    def test_down_v_without_a_file_exits_64_and_removes_nothing(self) -> None:
        self._managed_data_compose()
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (self.project / "docker-compose.yml").unlink()
        down = self.docker("compose", "-p", "demo", "down", "-v")
        self.assertEqual(down.returncode, 64, down.stderr)
        self.assertIn("Compose file", down.stderr)
        state = self.load_state()
        self.assertIn("demo-db-1", state["containers"])
        self.assertIn("demo_data", state["volumes"])
        self.assertIn("demo_default", state["networks"])

    def test_down_v_with_a_service_and_no_file_is_refused_too(self) -> None:
        self._managed_data_compose()
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (self.project / "docker-compose.yml").unlink()
        down = self.docker("compose", "-p", "demo", "down", "-v", "db")
        self.assertEqual(down.returncode, 64, down.stderr)
        self.assertIn("demo-db-1", self.load_state()["containers"])

    def test_plain_down_without_a_file_still_works_from_labels(self) -> None:
        self._managed_data_compose()
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (self.project / "docker-compose.yml").unlink()
        down = self.docker("compose", "-p", "demo", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        state = self.load_state()
        self.assertNotIn("demo-db-1", state["containers"])
        self.assertNotIn("demo_default", state["networks"])
        self.assertIn("demo_data", state["volumes"])

    def test_down_never_removes_an_external_network(self) -> None:
        self._seed_resource("networks", "edge", {"com.docker.compose.project": "demo"})
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
            networks:
              edge:
                external: true
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertIn("edge", self.load_state()["networks"])

    def test_up_is_idempotent(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.docker("compose", "up", "-d")
        first = set(self.load_state()["containers"])
        self.docker("compose", "up", "-d")
        second = set(self.load_state()["containers"])
        self.assertEqual(first, second)  # no duplicate accumulation

    def test_ls_lists_projects(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.docker("compose", "up", "-d")
        ls = self.docker("compose", "ls")
        self.assertEqual(ls.returncode, 0, ls.stderr)
        self.assertIn("demo", ls.stdout)

    def test_pull_uses_compose_images_and_requires_the_file(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
              local:
                build: .
            """
        )
        result = self.docker("compose", "pull")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.load_state()["action_log"],
            [["pull", "postgres:16"], ["pull", "nginx:latest"]],
        )
        self.assertIn("has no image to pull", result.stderr)

        (self.project / "docker-compose.yml").unlink()
        missing = self.docker("compose", "pull")
        self.assertEqual(missing.returncode, 1)
        self.assertIn("no compose file found", missing.stderr)

    def test_lifecycle_uses_dependency_order(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()

        stopped = self.docker("compose", "stop", "--timeout=7")
        self.assertEqual(stopped.returncode, 0, stopped.stderr)
        self.assertEqual(
            self.load_state()["action_log"],
            [["stop", "demo-web-1"], ["stop", "demo-db-1"]],
        )

        self.clear_action_log()
        started = self.docker("compose", "start")
        self.assertEqual(started.returncode, 0, started.stderr)
        started_actions = self.load_state()["action_log"]
        self.assertEqual(
            [action for action in started_actions if action[0] == "start"],
            [["start", "demo-db-1"], ["start", "demo-web-1"]],
        )
        self.assertEqual(
            len([action for action in started_actions if action[0] == "exec"]),
            2,
        )

        self.clear_action_log()
        restarted = self.docker("compose", "restart")
        self.assertEqual(restarted.returncode, 0, restarted.stderr)
        restarted_actions = self.load_state()["action_log"]
        self.assertEqual(
            [
                action
                for action in restarted_actions
                if action[0] in {"stop", "start"}
            ],
            [
                ["stop", "demo-web-1"],
                ["stop", "demo-db-1"],
                ["start", "demo-db-1"],
                ["start", "demo-web-1"],
            ],
        )
        self.assertEqual(
            len([action for action in restarted_actions if action[0] == "exec"]),
            2,
        )

    def test_lifecycle_falls_back_to_stable_label_order_without_file(self) -> None:
        self.write_compose(
            """
            services:
              zeta:
                image: zeta:latest
              alpha:
                image: alpha:latest
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (self.project / "docker-compose.yml").unlink()
        self.clear_action_log()

        self.assertEqual(self.docker("compose", "stop").returncode, 0)
        self.assertEqual(
            self.load_state()["action_log"],
            [["stop", "demo-zeta-1"], ["stop", "demo-alpha-1"]],
        )

        self.clear_action_log()
        self.assertEqual(self.docker("compose", "start").returncode, 0)
        started_actions = self.load_state()["action_log"]
        self.assertEqual(
            [action for action in started_actions if action[0] == "start"],
            [["start", "demo-alpha-1"], ["start", "demo-zeta-1"]],
        )
        self.assertEqual(
            len([action for action in started_actions if action[0] == "exec"]),
            2,
        )

    def test_exec_and_rm_reconstruct_membership_from_labels(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (self.project / "docker-compose.yml").unlink()
        self.clear_action_log()

        executed = self.docker(
            "compose", "exec", "-T", "--user", "1000", "web", "echo", "ok"
        )
        self.assertEqual(executed.returncode, 0, executed.stderr)
        self.assertEqual(
            self.load_state()["action_log"],
            [["exec", "demo-web-1", "echo", "ok"]],
        )

        self.clear_action_log()
        removed = self.docker("compose", "rm", "-fs")
        self.assertEqual(removed.returncode, 0, removed.stderr)
        self.assertEqual(
            self.load_state()["action_log"],
            [
                ["stop", "demo-web-1"],
                ["stop", "demo-db-1"],
                ["rm", "demo-web-1"],
                ["rm", "demo-db-1"],
            ],
        )
        self.assertEqual(self.load_state()["containers"], {})

    def test_scaled_service_state_is_rejected(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        state = self.load_state()
        duplicate = json.loads(json.dumps(state["containers"]["demo-web-1"]))
        duplicate["id"] = "demo-web-2"
        duplicate["name"] = "demo-web-2"
        duplicate["labels"]["com.docker.compose.container-number"] = "2"
        duplicate["configuration"]["id"] = "demo-web-2"
        duplicate["configuration"]["labels"][
            "com.docker.compose.container-number"
        ] = "2"
        state["containers"]["demo-web-2"] = duplicate
        self.state.write_text(json.dumps(state))

        result = self.docker("compose", "stop")
        self.assertEqual(result.returncode, 64)
        self.assertIn("scaling is not supported", result.stderr)

    def test_deferred_compose_features_warn_and_keep_one_service_container(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
                deploy:
                  replicas: 3
                networks:
                  default:
                    aliases: [frontend]
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "true"]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("deploy.replicas", result.stderr)
        self.assertIn("networks.aliases", result.stderr)
        self.assertIn("NOT APPLIED:", result.stderr)
        # conditions are enforced, not ignored: `web` waits for `db` to be
        # healthy (the fake exec passes, so the wait resolves at once), and
        # the gated healthcheck of `db` triggers no ungated-check warning.
        self.assertNotIn("depends_on conditions ignored", result.stderr)
        self.assertNotIn("healthcheck", result.stderr)
        self.assertEqual(
            set(self.load_state()["containers"]),
            {"demo-web-1", "demo-db-1"},
        )

    # -- Service selection (`up <service>` pulls in its dependencies) ------- #

    def _write_selective_compose(self) -> None:
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "true"]
              worker:
                image: alpine:3.20
            """
        )

    def _runs(self) -> list:
        return [a[1] for a in self.load_state()["action_log"] if a[0] == "run"]

    def test_up_service_creates_its_dependencies_in_order_and_skips_others(self) -> None:
        self._write_selective_compose()
        up = self.docker("compose", "up", "-d", "api")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(self._runs(), ["demo-db-1", "demo-api-1"])
        self.assertIn("Waiting for demo-db-1 to become healthy", up.stdout)
        self.assertNotIn("demo-worker-1", self.load_state()["containers"])

    def test_up_no_deps_creates_only_the_named_service_with_a_notice(self) -> None:
        self._write_selective_compose()
        up = self.docker("compose", "up", "-d", "--no-deps", "api")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(self._runs(), ["demo-api-1"])
        self.assertIn("--no-deps", up.stderr)
        self.assertIn("db", up.stderr)

    def test_up_unknown_service_exits_1_and_creates_nothing(self) -> None:
        self._seed_state()
        self._write_selective_compose()
        up = self.docker("compose", "up", "-d", "apii")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("apii", up.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertEqual(state["networks"], {})

    def test_up_service_starts_a_stopped_dependency(self) -> None:
        self._write_selective_compose()
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.assertEqual(self.docker("compose", "stop", "db").returncode, 0)
        up = self.docker("compose", "up", "-d", "api")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertIn("Starting demo-db-1", up.stdout)

    def test_down_without_compose_file(self) -> None:
        # down must work from labels alone, even with no compose file present.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.docker("compose", "up", "-d")
        (self.project / "docker-compose.yml").unlink()
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    # -- Security enforcement (security.decide, all-or-nothing) ------------- #

    def test_refused_project_creates_nothing(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                volumes: ["data:/d"]
              worker:
                image: alpine:3.20
                network_mode: none
            volumes:
              data: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertIn("worker", result.stderr)
        self.assertIn("network_mode: none is not supported", result.stderr)
        self.assertNotIn("network_mode: network_mode", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertEqual(state["networks"], {})
        self.assertEqual(state["volumes"], {})
        self.assertEqual(state["action_log"], [])

    def test_refused_build_creates_nothing(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              worker:
                build: .
                network_mode: none
            """
        )
        result = self.docker("compose", "build")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertEqual(self.load_state()["action_log"], [])

    def test_refused_edit_leaves_running_project_untouched(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        before = self.load_state()
        self.clear_action_log()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                network_mode: none
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        after = self.load_state()
        self.assertEqual(after["containers"], before["containers"])
        self.assertEqual(after["networks"], before["networks"])
        self.assertEqual(after["action_log"], [])

    def test_all_problems_are_reported_in_one_run(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              a:
                image: alpine:3.20
                network_mode: none
              b:
                image: alpine:3.20
                secrets: [pw]
            secrets:
              pw:
                file: ./pw.txt
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        refused = [line for line in result.stderr.splitlines() if line.startswith("REFUSED:")]
        self.assertEqual(len(refused), 2, result.stderr)
        self.assertTrue(any("'a'" in line and "network_mode" in line for line in refused))
        self.assertTrue(any("'b'" in line and "secrets" in line for line in refused))

    def test_up_refuses_secrets_without_opening_the_secret_file(self) -> None:
        self._seed_state()
        before = self.load_state()
        secret = self.project / "pw.txt"
        secret.write_text("hunter2\n")
        self.write_compose(
            """
            services:
              db:
                image: alpine:3.20
                secrets: [pw]
            secrets:
              pw:
                file: ./pw.txt
            """
        )
        log = self.root / "opened.log"
        harness = (
            "import builtins, sys\n"
            "real = builtins.open\n"
            "def guard(file, *a, **k):\n"
            "    with real(%r, 'a') as out:\n"
            "        out.write(str(file) + '\\n')\n"
            "    return real(file, *a, **k)\n"
            "builtins.open = guard\n"
            "sys.path.insert(0, %r)\n"
            "from container_docker_shim.cli import main\n"
            "raise SystemExit(main())\n"
        ) % (str(log), str(ROOT / "src"))
        result = subprocess.run(
            [sys.executable, "-c", harness, "compose", "up", "-d"],
            text=True,
            capture_output=True,
            env=self.env,
            cwd=str(self.project),
            check=False,
        )
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertNotIn("hunter2", result.stdout + result.stderr)
        opened = log.read_text().splitlines() if log.exists() else []
        self.assertNotIn(str(secret), opened)
        self.assertNotIn("pw.txt", " ".join(opened))
        after = self.load_state()
        self.assertEqual(after["action_log"], [])
        self.assertEqual(after["containers"], before["containers"])

    def write_local_bind_compose(self, options: str, services: tuple = ("db",)) -> Path:
        """A project whose `data` volume is the local-driver bind idiom."""
        device = self.root / "host-data"
        device.mkdir(exist_ok=True)
        body = "services:\n"
        for name in services:
            body += (
                f"  {name}:\n    image: alpine:3.20\n    volumes:\n      - data:/data\n"
            )
        body += (
            "volumes:\n  data:\n    driver: local\n    driver_opts:\n"
            f"      type: none\n      o: {options}\n      device: {device}\n"
        )
        (self.project / "docker-compose.yml").write_text(body)
        return device

    def run_mounts(self) -> list:
        """The `--mount` values of every `container run` the fake received."""
        mounts = []
        for call in self.runtime_calls():
            if call[:1] == ["run"] and "--mount" in call:
                mounts.extend(
                    call[i + 1] for i, arg in enumerate(call) if arg == "--mount"
                )
        return mounts

    def test_local_bind_volume_is_a_bind_mount_and_creates_no_volume(self) -> None:
        device = self.write_local_bind_compose("bind")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"type=bind,source={device},target=/data", self.run_mounts())
        self.assertEqual(self.load_state()["volumes"], {})
        volume_calls = [c for c in self.runtime_calls() if c[:1] == ["volume"]]
        self.assertEqual([c for c in volume_calls if c[1] in ("create", "inspect")], [])

    def test_local_bind_volume_with_ro_is_read_only(self) -> None:
        device = self.write_local_bind_compose("bind,ro")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(f"type=bind,source={device},target=/data,readonly", self.run_mounts())

    def test_two_services_share_the_local_bind_volume(self) -> None:
        device = self.write_local_bind_compose("bind", services=("a", "b"))
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.run_mounts().count(f"type=bind,source={device},target=/data"), 2)

    def test_down_v_never_removes_a_local_bind_volume(self) -> None:
        device = self.write_local_bind_compose("bind")
        (device / "keep.txt").write_text("data")
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        result = self.docker("compose", "down", "-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual([c for c in self.runtime_calls() if c[:2] == ["volume", "rm"]], [])
        self.assertTrue((device / "keep.txt").exists())

    def test_refused_local_bind_variants_exit_64_and_create_nothing(self) -> None:
        self._seed_state()
        self.write_local_bind_compose("bind,uid=1")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("uid=1", result.stderr)
        self.assert_nothing_was_created()

    def test_rbind_local_bind_volume_exits_64_and_creates_nothing(self) -> None:
        self._seed_state()
        self.write_local_bind_compose("rbind")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("rbind", result.stderr)
        self.assertIn("bind", result.stderr)
        self.assert_nothing_was_created()

    def test_rbind_local_bind_volume_is_refused_in_dry_run_too(self) -> None:
        self.write_local_bind_compose("rbind")
        result = self.dry_up()
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("rbind", result.stderr)
        self.assertIn("bind", result.stderr)
        self.assert_nothing_was_created()

    ANON_COMPOSE = """
        services:
          web:
            image: node:20
            volumes:
              - ./src:/app
              - /app/node_modules
        """

    ANON_LABEL = "com.docker.compose.anonymous"

    def anonymous_volumes(self, target: str | None = None) -> dict:
        """Name -> labels of the anonymous volumes in the fake runtime."""
        found = {}
        for name, volume in self.load_state()["volumes"].items():
            labels = volume["configuration"]["labels"]
            if self.ANON_LABEL in labels and (target is None or labels[self.ANON_LABEL] == target):
                found[name] = labels
        return found

    def container_volumes(self, container: str = "demo-web-1") -> dict:
        """Destination -> volume name of the container, read from its mounts."""
        mounts = self.load_state()["containers"][container]["configuration"]["mounts"]
        return {
            mount["destination"]: mount["type"]["volume"]["name"]
            for mount in mounts
            if "volume" in mount["type"]
        }

    def volume_removals(self) -> list:
        return [c for c in self.runtime_calls() if c[:2] == ["volume", "rm"]]

    def volume_creations(self) -> list:
        return [
            c for c in self.runtime_calls()
            if c[:2] == ["volume", "create"] and any(a.startswith(self.ANON_LABEL + "=") for a in c)
        ]

    def seed_runs(self) -> list:
        """The copy-up helper runs the fake received."""
        return [
            c for c in self.runtime_calls()
            if c[:1] == ["run"] and "--entrypoint" in c and c[c.index("--entrypoint") + 1] == "/bin/sh"
        ]

    def up_with_anonymous_volume(self) -> str:
        """`up -d` the project and return the anonymous volume this call created."""
        before = set(self.anonymous_volumes()) if self.state.exists() else set()
        self.write_compose(self.ANON_COMPOSE)
        (self.project / "src").mkdir(exist_ok=True)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        (created,) = set(self.anonymous_volumes()) - before
        return created

    def test_anonymous_volume_is_a_labelled_volume_filled_from_the_image(self) -> None:
        name = self.up_with_anonymous_volume()
        self.assertEqual(len(self.volume_creations()), 1)
        self.assertRegex(name, r"^demo_web_anon-[0-9a-f]{10}$")
        labels = self.load_state()["volumes"][name]["configuration"]["labels"]
        self.assertEqual(labels["com.docker.compose.project"], "demo")
        self.assertEqual(labels["com.docker.compose.service"], "web")
        self.assertEqual(labels[self.ANON_LABEL], "/app/node_modules")
        self.assertIn(f"type=volume,source={name},target=/app/node_modules", self.run_mounts())
        (seed,) = self.seed_runs()
        self.assertIn("--rm", seed)
        self.assertIn("node:20", seed)
        self.assertEqual(seed[-3:], [compose._SEED_SCRIPT, "sh", "/app/node_modules"])
        # The helper is gone, the service mounts the volume and no `-v /path` reached it.
        self.assertEqual(sorted(self.load_state()["containers"]), ["demo-web-1"])
        self.assertEqual(self.container_volumes(), {"/app/node_modules": name})
        service_run = [c for c in self.runtime_calls() if c[:1] == ["run"] and "--name" in c
                       and c[c.index("--name") + 1] == "demo-web-1"][0]
        self.assertNotIn("-v", service_run)
        self.assertEqual(self.volume_removals(), [])

    def test_recreate_mounts_the_volume_of_the_existing_container(self) -> None:
        name = self.up_with_anonymous_volume()
        result = self.docker("compose", "up", "-d", "--force-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.container_volumes(), {"/app/node_modules": name})
        self.assertEqual(list(self.anonymous_volumes()), [name])
        self.assertEqual(len(self.volume_creations()), 1)
        self.assertEqual(len(self.seed_runs()), 1)
        self.assertEqual(self.volume_removals(), [])

    def test_recreate_finds_the_volume_from_the_mounts_not_from_its_name(self) -> None:
        name = self.up_with_anonymous_volume()
        legacy = "demo_web_anon-0123456789"  # an earlier naming scheme, no labels
        state = self.load_state()
        volume = state["volumes"].pop(name)
        volume["configuration"] = {"name": legacy, "labels": {}}
        volume["id"] = legacy
        state["volumes"][legacy] = volume
        for mount in state["containers"]["demo-web-1"]["configuration"]["mounts"]:
            if mount["type"].get("volume", {}).get("name") == name:
                mount["type"]["volume"]["name"] = legacy
        self.state.write_text(json.dumps(state))
        result = self.docker("compose", "up", "-d", "--force-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.container_volumes(), {"/app/node_modules": legacy})
        self.assertEqual(sorted(self.load_state()["volumes"]), [legacy])
        self.assertEqual(len(self.volume_creations()), 1)
        self.assertEqual(self.volume_removals(), [])

    def test_recreate_creates_a_new_volume_when_the_old_one_is_gone(self) -> None:
        name = self.up_with_anonymous_volume()
        state = self.load_state()
        state["volumes"].pop(name)
        self.state.write_text(json.dumps(state))
        result = self.docker("compose", "up", "-d", "--force-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        (current,) = self.container_volumes().values()
        self.assertNotEqual(current, name)
        self.assertIn(current, self.load_state()["volumes"])
        self.assertEqual(len(self.seed_runs()), 2)
        self.assertEqual(self.volume_removals(), [])

    def test_recreate_with_a_new_target_creates_only_that_volume(self) -> None:
        name = self.up_with_anonymous_volume()
        self.write_compose(
            """
            services:
              web:
                image: node:20
                volumes:
                  - ./src:/app
                  - /app/node_modules
                  - /var/cache
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        mounted = self.container_volumes()
        self.assertEqual(mounted["/app/node_modules"], name)
        self.assertNotEqual(mounted["/var/cache"], name)
        self.assertEqual(len(self.anonymous_volumes()), 2)
        self.assertEqual(len(self.seed_runs()), 2)
        self.assertEqual(self.volume_removals(), [])

    def test_down_then_up_creates_a_new_volume_and_keeps_the_old_one(self) -> None:
        first = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.assertIn(first, self.load_state()["volumes"])
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        volumes = self.anonymous_volumes()
        self.assertEqual(len(volumes), 2)
        self.assertIn(first, volumes)
        self.assertEqual(volumes[first][self.ANON_LABEL], "/app/node_modules")
        (current,) = self.container_volumes().values()
        self.assertNotEqual(current, first)
        self.assertEqual(len(self.volume_creations()), 2)
        self.assertEqual(len(self.seed_runs()), 2)
        self.assertEqual(self.volume_removals(), [])

    def test_container_removed_outside_compose_gets_a_fresh_volume(self) -> None:
        first = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("rm", "-f", "demo-web-1").returncode, 0)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        (current,) = self.container_volumes().values()
        self.assertNotEqual(current, first)
        self.assertIn(first, self.load_state()["volumes"])
        self.assertEqual(len(self.seed_runs()), 2)
        self.assertEqual(self.volume_removals(), [])

    def test_a_volume_in_use_elsewhere_does_not_stop_up(self) -> None:
        first = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.env["FAKE_VOLUME_RM_FAIL"] = first  # any removal of it would fail
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(first, self.load_state()["volumes"])
        self.assertEqual(self.volume_removals(), [])

    def test_up_without_a_recreate_does_not_touch_the_volume(self) -> None:
        name = self.up_with_anonymous_volume()
        for flags in (["-d"], ["-d", "-V"]):
            result = self.docker("compose", "up", *flags)
            self.assertEqual(result.returncode, 0, result.stderr)
        # A stopped container is started, not recreated, even with -V.
        self.assertEqual(self.docker("stop", "demo-web-1").returncode, 0)
        result = self.docker("compose", "up", "-d", "-V")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.anonymous_volumes()), [name])
        self.assertEqual(self.container_volumes(), {"/app/node_modules": name})
        self.assertEqual(len(self.volume_creations()), 1)
        self.assertEqual(len(self.seed_runs()), 1)
        self.assertEqual(self.volume_removals(), [])

    def test_renew_anon_volumes_mounts_new_volumes_and_removes_nothing(self) -> None:
        first = self.up_with_anonymous_volume()
        seen = [first]
        for round_, flag in enumerate(("-V", "--renew-anon-volumes"), start=1):
            with self.subTest(flag=flag):
                result = self.docker("compose", "up", "-d", "--force-recreate", flag)
                self.assertEqual(result.returncode, 0, result.stderr)
                (current,) = self.container_volumes().values()
                self.assertNotIn(current, seen)
                seen.append(current)
                self.assertEqual(sorted(self.anonymous_volumes()), sorted(seen))
                self.assertEqual(len(self.seed_runs()), round_ + 1)
                self.assertEqual(self.volume_removals(), [])

    def test_renew_anon_volumes_keeps_named_volumes(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: node:20
                volumes:
                  - data:/data
                  - /app/node_modules
            volumes:
              data: {}
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        result = self.docker("compose", "up", "-d", "--force-recreate", "-V")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.container_volumes()["/data"], "demo_data")
        self.assertIn("demo_data", self.load_state()["volumes"])
        self.assertEqual(len(self.anonymous_volumes()), 2)
        self.assertEqual(self.volume_removals(), [])

    def test_renew_anon_volumes_in_dry_run_changes_nothing(self) -> None:
        self.up_with_anonymous_volume()
        before = len(self.runtime_calls())
        result = self.dry_up("-V", "--force-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        new = [c for c in self.runtime_calls()[before:] if c[:1] in (["run"], ["rm"], ["stop"])
               or c[:2] in (["volume", "create"], ["volume", "rm"])]
        self.assertEqual(new, [])

    def test_two_anonymous_targets_get_two_volumes(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: node:20
                volumes:
                  - /app/node_modules
                  - /var/cache
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        mounted = self.container_volumes()
        self.assertEqual(sorted(mounted), ["/app/node_modules", "/var/cache"])
        self.assertEqual(len(set(mounted.values())), 2)
        for target, name in mounted.items():
            self.assertEqual(list(self.anonymous_volumes(target)), [name])
        self.assertEqual(len(self.seed_runs()), 2)

    def test_failed_copy_up_removes_only_the_new_volume(self) -> None:
        old = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.env["FAKE_SEED_FAIL"] = "1"
        result = self.docker("compose", "up", "-d")
        self.assertNotEqual(result.returncode, 0)
        for text in ("web", "node:20", "/app/node_modules"):
            self.assertIn(text, result.stderr)
        state = self.load_state()
        self.assertEqual(list(self.anonymous_volumes()), [old])
        removed = self.volume_removals()
        self.assertEqual(len(removed), 1)
        self.assertNotEqual(removed[0][-1], old)
        self.assertEqual(state["containers"], {})

    def test_down_v_removes_every_labelled_anonymous_volume_of_the_project(self) -> None:
        orphans = []
        for _ in range(2):
            orphans.append(self.up_with_anonymous_volume())
            self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        (current,) = self.container_volumes().values()
        state = self.load_state()
        foreign = {
            "gone_service": {
                "com.docker.compose.project": "demo",
                "com.docker.compose.service": "gone",
                self.ANON_LABEL: "/data",
            },
            "other_anon": {"com.docker.compose.project": "other", self.ANON_LABEL: "/x"},
            "unlabelled": {},
            "named_in_demo": {"com.docker.compose.project": "demo"},
        }
        for name, labels in foreign.items():
            state["volumes"][name] = {"configuration": {"name": name, "labels": labels}, "id": name}
        self.state.write_text(json.dumps(state))
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.assertEqual(self.volume_removals(), [])
        result = self.docker("compose", "down", "-v")
        self.assertEqual(result.returncode, 0, result.stderr)
        remaining = sorted(self.load_state()["volumes"])
        self.assertEqual(remaining, ["named_in_demo", "other_anon", "unlabelled"])
        for name in (*orphans, current, "gone_service"):
            self.assertIn(f"Removed volume {name}", result.stdout)

    def test_down_v_keeps_removing_after_a_refusal_and_exits_1(self) -> None:
        first = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        second = self.up_with_anonymous_volume()
        self.assertEqual(self.docker("compose", "down").returncode, 0)
        self.env["FAKE_VOLUME_RM_FAIL"] = first
        result = self.docker("compose", "down", "-v")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn(first, result.stderr)
        self.assertEqual(list(self.anonymous_volumes()), [first])
        self.assertIn(f"Removed volume {second}", result.stdout)

    def test_anonymous_volume_with_subpath_is_refused_before_anything_is_created(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: node:20
                volumes:
                  - type: volume
                    target: /app/node_modules
                    volume:
                      subpath: sub
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("subpath", result.stderr)
        self.assert_nothing_was_created()

    def test_long_syntax_anonymous_volume_equals_the_short_one(self) -> None:
        proj = compose.Project("p", {"services": {"web": {"image": "x"}}}, "/tmp/p")
        short = compose._entry_mount("/cache", proj, "web")
        long = compose._entry_mount({"type": "volume", "target": "/cache"}, proj, "web")
        self.assertEqual(short, long)
        # Planning is pure: the volume is only named when the container is created.
        self.assertEqual(short.anonymous, compose._AnonymousVolume("web", "/cache"))
        self.assertEqual(short.mount.source, "")

    def test_volume_driver_opts_exit_64_without_creating_a_volume(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              db:
                image: alpine:3.20
                volumes:
                  - data:/data
            volumes:
              data:
                driver_opts:
                  type: nfs
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertEqual(self.load_state()["action_log"], [])

    def test_notices_print_before_anything_is_created(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                restart: always
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("NOT APPLIED:"), 1, result.stderr)
        self.assertNotIn("NOT APPLIED:", result.stdout)

    def test_network_mode_values_are_refused(self) -> None:
        for value in ("none", "host", "service:db", "container:x"):
            with self.subTest(value=value):
                self._seed_state()
                db = (
                    "\n                    db:\n                      image: alpine:3.20\n"
                    if value == "service:db"
                    else ""
                )
                self.write_compose(
                    """
                    services:
                      worker:
                        image: alpine:3.20
                        network_mode: %s
                    """
                    % value
                    + db
                )
                result = self.docker("compose", "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                refused = [
                    line
                    for line in result.stderr.splitlines()
                    if line.startswith("REFUSED:")
                ]
                self.assertTrue(
                    any(
                        "'worker'" in line and "network_mode" in line
                        for line in refused
                    ),
                    result.stderr,
                )
                state = self.load_state()
                self.assertEqual(state["containers"], {})
                self.assertEqual(state["networks"], {})
                self.assertEqual(state["action_log"], [])

    def test_network_mode_waiver_has_no_effect(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = (
            "network_mode,network-none"
        )
        self.write_compose(
            """
            services:
              worker:
                image: alpine:3.20
                network_mode: none
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("cannot be waived", result.stderr)
        self.assertIn("names no waivable option", result.stderr)
        self.assertEqual(self.load_state()["containers"], {})

    def test_unselected_service_with_network_mode_is_not_refused(self) -> None:
        self.write_compose(
            """
            services:
              api:
                image: nginx:latest
              legacy:
                image: alpine:3.20
                network_mode: host
            """
        )
        result = self.docker("compose", "up", "-d", "api")
        self.assertEqual(result.returncode, 0, result.stderr)
        containers = self.load_state()["containers"]
        self.assertIn("demo-api-1", containers)
        self.assertNotIn("demo-legacy-1", containers)

    def test_up_refuses_privileged_service(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                privileged: true
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("privileged", result.stderr)
        self.assertIn("web", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_refuses_security_opt_service(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                security_opt: ["no-new-privileges:true"]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertTrue(
            "security_opt" in result.stderr or "--security-opt" in result.stderr,
            result.stderr,
        )
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_refuses_pids_limit_service(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                pids_limit: 100
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("pids_limit", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_refusal_is_all_or_nothing(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
        self.write_compose(
            """
            services:
              bad:
                image: nginx:latest
                privileged: true
              good:
                image: nginx:latest
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertNotIn("demo_default", state["networks"])

    def test_up_opt_in_turns_refusal_into_warning(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = (
            "privileged,security-opt,pids-limit,sysctls,devices"
        )
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                privileged: true
                security_opt: ["no-new-privileges:true"]
                pids_limit: 100
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("warning:", result.stderr)
        for name in ("privileged", "security-opt", "pids-limit"):
            self.assertIn(name, result.stderr)
        self.assertIn("demo-web-1", self.load_state()["containers"])

    def test_up_opt_in_covers_only_named_option(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = "security-opt"
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                security_opt: ["no-new-privileges:true"]
                pids_limit: 100
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("pids_limit", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_refuses_sysctls_and_devices(self) -> None:
        cases = {
            "sysctls": 'sysctls: {net.ipv4.ip_forward: "1"}',
            "devices": 'devices: ["/dev/fuse:/dev/fuse"]',
        }
        for key, yaml_key in cases.items():
            with self.subTest(key=key):
                self._seed_state()
                self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
                self.write_compose(
                    f"""
                    services:
                      web:
                        image: nginx:latest
                        {yaml_key}
                    """
                )
                result = self.docker("compose", "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(key, result.stderr)
                self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_ignores_disabled_security_keys(self) -> None:
        self._seed_state()
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = ""
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                privileged: false
                security_opt: []
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo-web-1", self.load_state()["containers"])

    # -- depends_on service_healthy validation (all-or-nothing) -------------- #

    def test_up_refuses_service_healthy_without_healthcheck(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("web", result.stderr)
        self.assertIn("db", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        actions = [entry[0] for entry in state["action_log"]]
        self.assertNotIn("run", actions)
        self.assertNotIn("create", actions)

    def test_up_refuses_service_healthy_with_disabled_healthcheck(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  disable: true
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("web", result.stderr)
        self.assertIn("db", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        actions = [entry[0] for entry in state["action_log"]]
        self.assertNotIn("run", actions)
        self.assertNotIn("create", actions)

    def test_up_refuses_service_healthy_with_none_healthcheck(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["NONE"]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("web", result.stderr)
        self.assertIn("db", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        actions = [entry[0] for entry in state["action_log"]]
        self.assertNotIn("run", actions)
        self.assertNotIn("create", actions)

    def test_up_refuses_invalid_healthcheck_before_creating_containers(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                healthcheck:
                  test: ["CMD", "true"]
                  interval: abc
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("invalid healthcheck", result.stderr)
        self.assertIn("web", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        actions = [entry[0] for entry in state["action_log"]]
        self.assertNotIn("run", actions)
        self.assertNotIn("create", actions)

    def test_service_healthy_with_enabled_healthcheck_passes_validation(self) -> None:
        # The waiting logic is not implemented yet, so `up` is not exercised
        # here — only the validation guard, which must accept a dependency
        # that has an enabled healthcheck.
        project = compose.Project(
            "demo",
            compose.parse_yaml(
                textwrap.dedent(
                    """
                    services:
                      web:
                        image: nginx:latest
                        depends_on:
                          db:
                            condition: service_healthy
                      db:
                        image: postgres:16
                        healthcheck:
                          test: ["CMD", "true"]
                    """
                )
            ),
            str(self.project),
        )
        compose._validate_dependencies(project.topo_sorted())  # must not raise

    # -- depends_on service_healthy waiting (end-to-end) ------------------------ #

    def test_up_waits_for_service_healthy_then_starts_dependent(self) -> None:
        # retries must tolerate two consecutive failures so that the third,
        # passing check is reached before `up` gives up on `db`.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 0.05
                  timeout: 5
                  retries: 3
            """
        )
        self.script_exec("demo-db-1", [1, 1, 0])
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo-db-1 is healthy", result.stdout)
        state = self.load_state()
        self.assertIn("demo-db-1", state["containers"])
        self.assertIn("demo-web-1", state["containers"])
        pg_execs = [
            entry
            for entry in state["action_log"]
            if entry == ["exec", "demo-db-1", "pg_isready"]
        ]
        self.assertEqual(len(pg_execs), 3)
        log = state["action_log"]
        self.assertGreater(
            log.index(["run", "demo-web-1"]),
            max(i for i, entry in enumerate(log) if entry == ["exec", "demo-db-1", "pg_isready"]),
        )

    def test_up_fails_when_dependency_never_becomes_healthy(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 0.05
                  timeout: 5
                  retries: 2
            """
        )
        self.script_exec("demo-db-1", [1])  # the last code repeats: never passes
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("demo-db-1", result.stderr)
        self.assertIn("became unhealthy", result.stderr)
        self.assertIn("last log lines", result.stderr)
        state = self.load_state()
        self.assertIn("demo-db-1", state["containers"])
        self.assertNotIn("demo-web-1", state["containers"])

    def _write_gate(self, required: str, healthcheck: bool = True) -> None:
        check = (
            """
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 0.05
                  timeout: 5
                  retries: 2"""
            if healthcheck
            else ""
        )
        self.write_compose(
            f"""
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy{required}
              db:
                image: postgres:16{check}
            """
        )

    def test_optional_unhealthy_dependency_is_a_notice_and_the_dependent_starts(self) -> None:
        self._write_gate("\n                    required: false")
        self.script_exec("demo-db-1", [1])
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("optional dependency 'db'", result.stderr)
        self.assertIn("demo-web-1", self.load_state()["containers"])

    def test_optional_unhealthy_dependency_is_not_waited_on_again_by_up_wait(self) -> None:
        self._write_gate("\n                    required: false")
        self.script_exec("demo-db-1", [1])
        result = self.docker("compose", "up", "-d", "--wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count("became unhealthy"), 1)

    def test_required_unhealthy_dependency_still_fails_up(self) -> None:
        self._write_gate("\n                    required: true")
        self.script_exec("demo-db-1", [1])
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_optional_gate_without_healthcheck_is_a_notice_not_a_refusal(self) -> None:
        self._write_gate("\n                    required: false", healthcheck=False)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("dependency is optional", result.stderr)
        self.assertEqual(
            set(self.load_state()["containers"]), {"demo-web-1", "demo-db-1"}
        )

    def test_required_gate_without_healthcheck_is_still_refused(self) -> None:
        self._seed_state()
        self._write_gate("", healthcheck=False)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(self.load_state()["containers"], {})

    def test_up_wait_timeout_bounds_service_healthy_wait(self) -> None:
        # A slow healthcheck (30s interval, 50 retries) must still be cut
        # off by --wait-timeout: the wait sleeps at most to the deadline.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 30
                  timeout: 5
                  retries: 50
            """
        )
        self.script_exec("demo-db-1", [1])  # never passes
        result = self.docker("compose", "up", "-d", "--wait-timeout", "1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not become healthy", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    # -- depends_on service_completed_successfully (end-to-end) ---------------- #

    def test_up_runs_completed_successfully_dependency_in_foreground(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  migrate:
                    condition: service_completed_successfully
              migrate:
                image: busybox
                command: ["true"]
            """
        )
        self.script_exit("demo-migrate-1", 0)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo-migrate-1 exited with code 0", result.stdout)
        state = self.load_state()
        self.assertIn("demo-migrate-1", state["containers"])
        self.assertIn("demo-web-1", state["containers"])
        self.assertEqual(
            state["containers"]["demo-migrate-1"]["status"]["state"], "stopped"
        )
        self.assertEqual(
            state["containers"]["demo-web-1"]["status"]["state"], "running"
        )
        log = state["action_log"]
        self.assertGreater(
            log.index(["run", "demo-web-1"]), log.index(["run", "demo-migrate-1"])
        )

    def test_up_fails_when_completed_successfully_dependency_exits_nonzero(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  migrate:
                    condition: service_completed_successfully
              migrate:
                image: busybox
                command: ["true"]
            """
        )
        self.script_exit("demo-migrate-1", 3)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("demo-migrate-1", result.stderr)
        self.assertIn("exited with code 3", result.stderr)
        self.assertIn("last log lines", result.stderr)
        state = self.load_state()
        self.assertIn("demo-migrate-1", state["containers"])
        self.assertNotIn("demo-web-1", state["containers"])

    # -- up --wait (end-to-end) ------------------------------------------------- #

    def test_up_wait_returns_when_services_healthy(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 0.05
                  timeout: 5
                  retries: 3
            """
        )
        self.script_exec("demo-db-1", [1, 0])  # fails once, then passes
        result = self.docker("compose", "up", "--wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo-db-1 is healthy", result.stdout)
        self.assertNotIn("LOGS", result.stdout)  # --wait implies detach: no log streaming
        state = self.load_state()
        for name in ("demo-db-1", "demo-web-1"):
            self.assertEqual(
                state["containers"][name]["status"]["state"], "running", name
            )

    def test_up_wait_fails_when_dependency_never_becomes_healthy(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 0.05
                  timeout: 5
                  retries: 2
            """
        )
        self.script_exec("demo-db-1", [1])  # the last code repeats: never passes
        result = self.docker("compose", "up", "--wait")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("demo-db-1", result.stderr)
        self.assertIn("became unhealthy", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_wait_timeout_cuts_off_slow_healthy_wait(self) -> None:
        # A slow healthcheck (30s interval, 50 retries) must still be cut
        # off by --wait-timeout: `up --wait` gives up at the deadline.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
              db:
                image: postgres:16
                healthcheck:
                  test: ["CMD", "pg_isready"]
                  interval: 30
                  timeout: 5
                  retries: 50
            """
        )
        self.script_exec("demo-db-1", [1])  # never passes
        result = self.docker("compose", "up", "--wait", "--wait-timeout", "1")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("did not become healthy", result.stderr)
        self.assertNotIn("demo-web-1", self.load_state()["containers"])

    def test_up_wait_fails_when_service_exited_early(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.script_early_exit("demo-web-1")
        result = self.docker("compose", "up", "--wait")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("demo-web-1", result.stderr)
        self.assertIn("is not running", result.stderr)
        state = self.load_state()
        self.assertEqual(
            state["containers"]["demo-web-1"]["status"]["state"], "stopped"
        )

    def test_up_detach_does_not_wait_for_early_exit(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.script_early_exit("demo-web-1")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        self.assertEqual(
            state["containers"]["demo-web-1"]["status"]["state"], "stopped"
        )

    # -- Ungated healthcheck warnings ----------------------------------------- #

    _UNGATED_WARNING = (
        "only evaluated to gate depends_on service_healthy or 'up --wait'"
    )

    def test_up_detach_warns_ungated_healthcheck_once(self) -> None:
        # A declared healthcheck that nothing here gates on is never
        # evaluated by `up -d`, and the shim must say so exactly once.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                healthcheck:
                  test: ["CMD", "true"]
                  interval: 0.05
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stderr.count(self._UNGATED_WARNING), 1, result.stderr)

    def test_up_wait_suppresses_ungated_healthcheck_warning(self) -> None:
        # `up --wait` evaluates the same healthcheck itself, so the warning
        # must not fire for it.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                healthcheck:
                  test: ["CMD", "true"]
                  interval: 0.05
            """
        )
        result = self.docker("compose", "up", "--wait")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self._UNGATED_WARNING, result.stderr)

    def test_disabled_healthcheck_does_not_warn(self) -> None:
        # Both disabled spellings are never evaluated at all, so neither
        # triggers the ungated-check warning.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                healthcheck:
                  disable: true
                  test: ["CMD", "true"]
              cache:
                image: redis:7
                healthcheck:
                  test: ["NONE"]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn(self._UNGATED_WARNING, result.stderr)
        self.assertNotIn("healthcheck", result.stderr)

    def test_up_writes_config_hash_and_image_labels(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        web = state["containers"]["demo-web-1"]["labels"]
        self.assertEqual(web["com.docker.compose.image"], "fakeid-nginx:latest")
        self.assert_sha256(web["com.docker.compose.config-hash"])
        db = state["containers"]["demo-db-1"]["labels"]
        self.assertEqual(db["com.docker.compose.image"], "fakeid-postgres:16")
        self.assert_sha256(db["com.docker.compose.config-hash"])
        self.assertNotEqual(
            web["com.docker.compose.config-hash"],
            db["com.docker.compose.config-hash"],
        )

    def test_up_config_hash_tracks_spec_changes(self) -> None:
        original = (
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.write_compose(original)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        first = self.load_state()["containers"]["demo-web-1"]["labels"][
            "com.docker.compose.config-hash"
        ]
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                environment:
                  FOO: bar
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        changed = self.load_state()["containers"]["demo-web-1"]["labels"][
            "com.docker.compose.config-hash"
        ]
        self.assertNotEqual(first, changed)
        # Rewriting the file back to the original reproduces the first hash.
        self.write_compose(original)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        restored = self.load_state()["containers"]["demo-web-1"]["labels"][
            "com.docker.compose.config-hash"
        ]
        self.assertEqual(first, restored)

    def test_up_config_hash_ignores_depends_on(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        first = self.load_state()["containers"]["demo-web-1"]["labels"][
            "com.docker.compose.config-hash"
        ]
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [cache]
              cache:
                image: redis:7
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        state = self.load_state()
        self.assertEqual(
            state["containers"]["demo-web-1"]["labels"][
                "com.docker.compose.config-hash"
            ],
            first,
        )

    # -- up -d per-service reconciliation ----------------------------------- #

    _RECONCILE_COMPOSE = """
    services:
      web:
        image: nginx:latest
        environment:
          FOO: one
        depends_on: [db]
      db:
        image: postgres:16
    """

    def test_up_leaves_unchanged_running_containers_alone(self) -> None:
        self.write_compose(self._RECONCILE_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        verbs = {entry[0] for entry in actions}
        self.assertFalse(verbs & {"run", "rm", "stop", "start"}, actions)
        self.assertIn("demo-web-1 is up to date", result.stdout)
        self.assertIn("demo-db-1 is up to date", result.stdout)

    def test_up_starts_stopped_container_without_recreating(self) -> None:
        self.write_compose(self._RECONCILE_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.assertEqual(self.docker("stop", "demo-db-1").returncode, 0)
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertIn(["start", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)
        self.assertNotIn(["rm", "demo-db-1"], actions)
        # web depends on db, but db was only started, not recreated.
        self.assertNotIn(["rm", "demo-web-1"], actions)
        state = self.load_state()
        self.assertEqual(state["containers"]["demo-db-1"]["status"]["state"], "running")

    def test_up_recreates_only_the_changed_service(self) -> None:
        self.write_compose(self._RECONCILE_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                environment:
                  FOO: two
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertLess(
            actions.index(["rm", "demo-web-1"]),
            actions.index(["run", "demo-web-1"]),
        )
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)
        self.assertIn("Recreating demo-web-1 (configuration changed)", result.stdout)

    def test_up_recreates_dependent_services_when_a_dependency_changes(self) -> None:
        self.write_compose(self._RECONCILE_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                environment:
                  FOO: one
                depends_on: [db]
              db:
                image: postgres:17
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertLess(
            actions.index(["rm", "demo-db-1"]),
            actions.index(["rm", "demo-web-1"]),
        )
        self.assertLess(
            actions.index(["run", "demo-db-1"]),
            actions.index(["run", "demo-web-1"]),
        )
        self.assertIn("Recreating demo-db-1", result.stdout)
        self.assertIn("Recreating demo-web-1 (a dependency was recreated)", result.stdout)

    def test_up_recreates_container_missing_config_hash_label(self) -> None:
        self.write_compose(self._RECONCILE_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()
        state = self.load_state()
        for labels in (
            state["containers"]["demo-web-1"]["labels"],
            state["containers"]["demo-web-1"]["configuration"]["labels"],
        ):
            labels.pop("com.docker.compose.config-hash", None)
        self.state.write_text(json.dumps(state))
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertLess(
            actions.index(["rm", "demo-web-1"]),
            actions.index(["run", "demo-web-1"]),
        )
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)
        self.assertIn(
            "Recreating demo-web-1 (created without a configuration label)",
            result.stdout,
        )

    # -- up -d flags: --force-recreate, --no-recreate, --remove-orphans ----- #

    _UP_FLAG_COMPOSE = """
    services:
      web:
        image: nginx:latest
      db:
        image: postgres:16
    """

    def test_up_force_recreate_recreates_unchanged_containers(self) -> None:
        self.write_compose(self._UP_FLAG_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.clear_action_log()
        result = self.docker("compose", "up", "-d", "--force-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertLess(
            actions.index(["rm", "demo-web-1"]),
            actions.index(["run", "demo-web-1"]),
        )
        self.assertLess(
            actions.index(["rm", "demo-db-1"]),
            actions.index(["run", "demo-db-1"]),
        )
        self.assertIn("Recreating demo-web-1 (forced)", result.stdout)
        self.assertIn("Recreating demo-db-1 (forced)", result.stdout)

    def test_up_no_recreate_leaves_changed_containers_alone(self) -> None:
        self.write_compose(self._UP_FLAG_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                environment:
                  FOO: x
              db:
                image: postgres:16
            """
        )
        self.clear_action_log()
        result = self.docker("compose", "up", "-d", "--no-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        for entry in actions:
            self.assertNotIn(entry[0], {"rm", "run"}, actions)
        self.assertNotIn("Recreating", result.stdout)
        # Control: a plain `up -d` recreates the changed service.
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertLess(
            actions.index(["rm", "demo-web-1"]),
            actions.index(["run", "demo-web-1"]),
        )
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)

    def test_up_no_recreate_creates_missing_containers(self) -> None:
        self.write_compose(self._UP_FLAG_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.assertEqual(self.docker("rm", "-f", "demo-web-1").returncode, 0)
        self.clear_action_log()
        result = self.docker("compose", "up", "-d", "--no-recreate")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertIn(["run", "demo-web-1"], actions)
        self.assertNotIn(["rm", "demo-web-1"], actions)
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)

    def test_up_rejects_force_recreate_with_no_recreate(self) -> None:
        self._seed_state()
        self.write_compose(self._UP_FLAG_COMPOSE)
        result = self.docker(
            "compose", "up", "-d", "--force-recreate", "--no-recreate"
        )
        self.assertEqual(result.returncode, 64)
        self.assertIn("incompatible", result.stderr)
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertEqual(state["action_log"], [])

    def test_up_warns_about_orphan_containers(self) -> None:
        self.write_compose(self._UP_FLAG_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("found orphan containers", result.stderr)
        self.assertIn("demo-db-1", result.stderr)
        self.assertIn("--remove-orphans", result.stderr)
        actions = self.load_state()["action_log"]
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertIn("demo-db-1", self.load_state()["containers"])

    def test_up_remove_orphans_removes_orphan_containers(self) -> None:
        self.write_compose(self._UP_FLAG_COMPOSE)
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        self.clear_action_log()
        result = self.docker("compose", "up", "-d", "--remove-orphans")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["rm", "demo-web-1"], actions)
        self.assertNotIn(["run", "demo-web-1"], actions)
        state = self.load_state()
        self.assertNotIn("demo-db-1", state["containers"])
        self.assertIn("demo-web-1", state["containers"])
        self.assertIn("Removing orphan containers: demo-db-1", result.stdout)

    def test_up_partial_failure_keeps_started_services_and_recovers(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.script_run_failure("demo-web-1")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("service 'web' failed to start", result.stderr)
        state = self.load_state()
        self.assertIn("demo-db-1", state["containers"])
        self.assertEqual(state["containers"]["demo-db-1"]["status"]["state"], "running")
        self.assertNotIn("demo-web-1", state["containers"])
        self.assertNotIn(["rm", "demo-db-1"], state["action_log"])
        # Recovery: the next up only starts the missing service.
        self.script_run_failure("demo-web-1", fail=False)
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        actions = self.load_state()["action_log"]
        self.assertIn(["run", "demo-web-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertIn("demo-db-1 is up to date", result.stdout)
        state = self.load_state()
        self.assertEqual(state["containers"]["demo-db-1"]["status"]["state"], "running")
        self.assertEqual(state["containers"]["demo-web-1"]["status"]["state"], "running")

    def test_up_failed_recreate_leaves_other_services_alone(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.assertEqual(self.docker("compose", "up", "-d").returncode, 0)
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                environment:
                  FOO: x
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        self.script_run_failure("demo-web-1")
        self.clear_action_log()
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertIn("service 'web' failed to start", result.stderr)
        actions = self.load_state()["action_log"]
        self.assertNotIn(["rm", "demo-db-1"], actions)
        self.assertNotIn(["run", "demo-db-1"], actions)
        self.assertNotIn(["stop", "demo-db-1"], actions)
        state = self.load_state()
        self.assertEqual(state["containers"]["demo-db-1"]["status"]["state"], "running")

    # -- Global flags: repeatable -f, --profile, COMPOSE_FILE/PROFILES ------ #

    def _write_named_compose(self, name: str, image: str = "nginx:latest") -> None:
        (self.project / name).write_text(
            f"services:\n  web:\n    image: {image}\n"
        )

    def test_file_flag_in_both_spellings_selects_the_file(self) -> None:
        self._write_named_compose("custom.yml", "custom:1")
        for flags in (["-f", "custom.yml"], ["--file=custom.yml"]):
            result = self.docker("compose", *flags, "config")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("custom:1", result.stdout)

    def test_several_file_flags_exit_64_without_merging(self) -> None:
        self._write_named_compose("one.yml")
        self._write_named_compose("two.yml")
        result = self.docker("compose", "-f", "one.yml", "-f", "two.yml", "config")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("-f", result.stderr)

    def test_profile_flag_exits_64_in_both_spellings(self) -> None:
        self._write_named_compose("one.yml")
        for flags in (["--profile", "debug"], ["--profile=debug"]):
            result = self.docker("compose", "-f", "one.yml", *flags, "config")
            self.assertEqual(result.returncode, 64, result.stderr)
            self.assertIn("--profile", result.stderr)

    def test_compose_profiles_environment_reaches_the_loader(self) -> None:
        self._write_named_compose("one.yml")
        self.env["COMPOSE_PROFILES"] = "debug"
        result = self.docker("compose", "-f", "one.yml", "config")
        self.assertEqual(result.returncode, 64, result.stderr)

    def test_compose_file_environment_selects_the_file(self) -> None:
        self._write_named_compose("custom.yml", "fromenv:1")
        self.env["COMPOSE_FILE"] = "custom.yml"
        result = self.docker("compose", "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fromenv:1", result.stdout)

    def test_compose_file_environment_with_two_files_exits_64(self) -> None:
        self._write_named_compose("one.yml")
        self._write_named_compose("two.yml")
        self.env["COMPOSE_FILE"] = os.pathsep.join(["one.yml", "two.yml"])
        result = self.docker("compose", "config")
        self.assertEqual(result.returncode, 64, result.stderr)

    def test_file_flag_wins_over_compose_file_environment(self) -> None:
        self._write_named_compose("flag.yml", "fromflag:1")
        self._write_named_compose("env.yml", "fromenv:1")
        self.env["COMPOSE_FILE"] = "env.yml"
        result = self.docker("compose", "-f", "flag.yml", "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("fromflag:1", result.stdout)
        self.assertNotIn("fromenv:1", result.stdout)

    # -- Official normalizer source (fake `docker-compose`) ---------------- #

    FAKE_COMPOSE = textwrap.dedent(
        """\
        #!/usr/bin/env python3
        import json, os, sys
        with open(os.environ["FAKE_COMPOSE_LOG"], "a") as handle:
            handle.write(json.dumps({
                "argv": sys.argv[1:],
                "marker": os.environ.get("CONTAINER_DOCKER_SHIM_NORMALIZING"),
            }) + "\\n")
        if sys.argv[1:] == ["version", "--short"]:
            print("5.6.0")
            sys.exit(0)
        sys.stderr.write(os.environ.get("FAKE_COMPOSE_STDERR", ""))
        if os.environ.get("FAKE_COMPOSE_FAIL"):
            sys.exit(int(os.environ["FAKE_COMPOSE_FAIL"]))
        print(open(os.environ["FAKE_COMPOSE_MODEL"]).read())
        """
    )

    def install_fake_normalizer(self, model: dict, *, explicit: bool = True) -> Path:
        """Install a fake `docker-compose` printing `model`; returns its argv log.

        With ``explicit`` the executable is named by CONTAINER_DOCKER_SHIM_COMPOSE,
        otherwise it is found on PATH.
        """
        bin_dir = self.root / "bin"
        bin_dir.mkdir(exist_ok=True)
        executable = bin_dir / "docker-compose"
        executable.write_text(self.FAKE_COMPOSE)
        executable.chmod(0o755)
        model_file = self.root / "model.json"
        model_file.write_text(json.dumps(model))
        log = self.root / "compose-argv.log"
        self.env["FAKE_COMPOSE_MODEL"] = str(model_file)
        self.env["FAKE_COMPOSE_LOG"] = str(log)
        if explicit:
            self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = str(executable)
        else:
            self.env["PATH"] = str(bin_dir) + os.pathsep + self.env["PATH"]
        return log

    def normalizer_calls(self, log: Path) -> list[dict]:
        return [json.loads(line) for line in log.read_text().splitlines()]

    def web_model(self, **service: object) -> dict:
        spec: dict = {"image": "nginx:latest"}
        spec.update(service)
        return {"name": "demo", "services": {"web": spec}}

    def test_official_normalizer_supplies_the_model(self) -> None:
        self.write_compose("services:\n  ignored:\n    image: busybox\n")
        log = self.install_fake_normalizer(
            self.web_model(
                ports=[{"mode": "ingress", "target": 80, "published": "8080", "protocol": "tcp"}],
                environment={"A": "1"},
                command=None,
            )
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        containers = self.load_state()["containers"]
        self.assertEqual(list(containers), ["demo-web-1"])  # the YAML file was not read
        argv = containers["demo-web-1"]["run_argv"]
        self.assertIn("0.0.0.0:8080:80", argv)
        self.assertIn("A=1", argv)
        calls = self.normalizer_calls(log)
        self.assertEqual(calls[0]["argv"], ["config", "--format", "json"])
        self.assertEqual(calls[0]["marker"], "1")

    def test_official_normalizer_is_found_on_path(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        log = self.install_fake_normalizer(self.web_model(), explicit=False)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.normalizer_calls(log)[0]["argv"], ["config", "--format", "json"])

    def test_official_normalizer_receives_the_forwarded_flags(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        log = self.install_fake_normalizer(self.web_model())
        result = self.docker(
            "compose",
            "--env-file", "e.env",
            "-p", "custom",
            "--project-directory", str(self.project),
            "-f", "docker-compose.yml",
            "--profile", "debug",
            "up", "-d",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.normalizer_calls(log)[0]["argv"],
            [
                "-f", os.path.join(os.path.realpath(self.project), "docker-compose.yml"),
                "--env-file", "e.env",
                "--profile", "debug",
                "-p", "custom",
                "--project-directory", str(self.project),
                "config", "--format", "json",
            ],
        )

    def test_official_normalizer_accepts_several_files_and_profiles(self) -> None:
        self._write_named_compose("one.yml")
        self._write_named_compose("two.yml")
        log = self.install_fake_normalizer(self.web_model())
        result = self.docker(
            "compose", "-f", "one.yml", "-f", "two.yml", "--profile", "a", "--profile", "b", "up", "-d"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.normalizer_calls(log)[0]["argv"],
            [
                "-f", os.path.join(os.path.realpath(self.project), "one.yml"),
                "-f", os.path.join(os.path.realpath(self.project), "two.yml"),
                "--profile", "a", "--profile", "b",
                "config", "--format", "json",
            ],
        )

    def test_official_normalizer_receives_every_env_file_in_order(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        log = self.install_fake_normalizer(self.web_model())
        result = self.docker(
            "compose",
            "--env-file", "one.env",
            "--env-file", "two.env",
            "--env-file", "three.env",
            "up", "-d",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            self.normalizer_calls(log)[0]["argv"],
            [
                "--env-file", "one.env",
                "--env-file", "two.env",
                "--env-file", "three.env",
                "config", "--format", "json",
            ],
        )

    def test_equals_spellings_behave_like_the_separate_form(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        log = self.install_fake_normalizer(self.web_model())
        separate = self.docker(
            "compose", "--env-file", "e.env", "--project-directory", str(self.project),
            "--profile", "debug", "-p", "custom", "-f", "docker-compose.yml", "up", "-d",
        )
        equals = self.docker(
            "compose", "--env-file=e.env", f"--project-directory={self.project}",
            "--profile=debug", "--project-name=custom", "--file=docker-compose.yml", "up", "-d",
        )
        self.assertEqual(separate.returncode, 0, separate.stderr)
        self.assertEqual(equals.returncode, 0, equals.stderr)
        configs = [
            call["argv"] for call in self.normalizer_calls(log) if "config" in call["argv"]
        ]
        self.assertEqual(len(configs), 2)
        self.assertEqual(configs[0], configs[1])

    def test_empty_or_missing_global_option_values_exit_64(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        for option in ("--env-file", "--project-directory", "--file", "--profile", "--project-name"):
            for args in ((option, ""), (f"{option}=",)):
                with self.subTest(args=args):
                    result = self.docker("compose", *args, "up", "-d")
                    self.assertEqual(result.returncode, 64, result.stderr)
                    self.assertIn(option, result.stderr)
            with self.subTest(option=option, missing=True):
                result = self.docker("compose", option)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("requires a value", result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_official_normalizer_failure_is_reported(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.install_fake_normalizer(self.web_model())
        self.env["FAKE_COMPOSE_FAIL"] = "1"
        self.env["FAKE_COMPOSE_STDERR"] = "validating x.yml: services.web.nope is invalid\n"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 1)
        self.assertIn("services.web.nope is invalid", result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_official_normalizer_warnings_pass_through(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.install_fake_normalizer(self.web_model())
        self.env["FAKE_COMPOSE_STDERR"] = 'level=warning msg="The NOPE variable is not set"\n'
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("The NOPE variable is not set", result.stderr)

    def test_explicit_normalizer_that_does_not_exist_fails(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = str(self.root / "missing-compose")
        result = self.docker("compose", "up", "-d")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTAINER_DOCKER_SHIM_COMPOSE", result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_normalizer_that_resolves_to_the_shim_is_refused_as_a_loop(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = str(DOCKER)
        result = self.docker("compose", "up", "-d")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("loop", result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_malformed_normalized_model_names_the_compose_version(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.install_fake_normalizer({"services": {"web": {"image": "nginx:latest"}}})
        result = self.docker("compose", "up", "-d")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Docker Compose 5.6.0", result.stderr)

    def test_explicit_file_is_relative_to_the_working_directory(self) -> None:
        # Compose resolves -f against the cwd; --project-directory only moves
        # the project root, so the file below must not be looked up inside it.
        elsewhere = self.root / "elsewhere"
        elsewhere.mkdir()
        (elsewhere / "docker-compose.yml").write_text("services:\n  web:\n    image: other:1\n")
        self._write_named_compose("custom.yml", "cwdfile:1")
        (elsewhere / "custom.yml").write_text("services:\n  web:\n    image: wrong:1\n")
        result = self.docker(
            "compose", "--project-directory", str(elsewhere), "-f", "custom.yml", "config"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("cwdfile:1", result.stdout)

    def test_official_model_name_is_sanitized(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        model = self.web_model()
        model["name"] = "My App!"
        self.install_fake_normalizer(model)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(list(self.load_state()["containers"]), ["my_app_-web-1"])

    # -- config / config -q / version through each source ------------------ #

    def test_official_config_prints_the_normalized_json(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        model = self.web_model(
            ports=[{"mode": "ingress", "target": 80, "published": "8080", "protocol": "tcp"}],
            command=None,
        )
        self.install_fake_normalizer(model)
        result = self.docker("compose", "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(json.loads(result.stdout), model)

    def test_official_config_quiet_validates_without_output(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.install_fake_normalizer(self.web_model())
        for flag in ("-q", "--quiet"):
            result = self.docker("compose", "config", flag)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout, "")

    def test_official_config_failure_is_reported_and_prints_nothing(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        self.install_fake_normalizer(self.web_model())
        self.env["FAKE_COMPOSE_FAIL"] = "1"
        self.env["FAKE_COMPOSE_STDERR"] = "services.web.nope is invalid\n"
        for args in (["config"], ["config", "-q"]):
            result = self.docker("compose", *args)
            self.assertEqual(result.returncode, 1)
            self.assertEqual(result.stdout, "")
            self.assertIn("services.web.nope is invalid", result.stderr)

    def test_builtin_config_prints_the_model_and_quiet_is_silent(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx:latest\n")
        result = self.docker("compose", "config")
        self.assertEqual(result.returncode, 0, result.stderr)
        rendered = json.loads(result.stdout)
        self.assertEqual(rendered["name"], "demo")
        self.assertEqual(rendered["services"]["web"]["image"], "nginx:latest")
        quiet = self.docker("compose", "config", "-q")
        self.assertEqual(quiet.returncode, 0, quiet.stderr)
        self.assertEqual(quiet.stdout, "")

    def test_builtin_config_quiet_still_validates(self) -> None:
        self.write_compose(
            "services:\n  a:\n    image: x\n    depends_on: [b]\n"
            "  b:\n    image: y\n    depends_on: [a]\n"
        )
        result = self.docker("compose", "config", "-q")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertEqual(result.stdout, "")

    def test_version_reports_the_builtin_subset(self) -> None:
        result = self.docker("compose", "version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "normalizer: built-in subset\n")

    def test_version_reports_the_official_release(self) -> None:
        self.install_fake_normalizer(self.web_model())
        result = self.docker("compose", "version")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout, "normalizer: official 5.6.0\n")

    def test_version_with_a_missing_explicit_normalizer_fails(self) -> None:
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = str(self.root / "missing-compose")
        result = self.docker("compose", "version")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("CONTAINER_DOCKER_SHIM_COMPOSE", result.stderr)

    # -- Built-in source refuses what it cannot read (exit 64) ------------- #

    REFUSED_CONSTRUCTS = {
        "anchor": "x-base: &base\n  restart: always\nservices:\n  web:\n    image: nginx\n",
        "alias": "services:\n  web:\n    image: nginx\n    command: *cmd\n",
        "merge key": "services:\n  web:\n    <<: {image: nginx}\n",
        "block scalar": "services:\n  web:\n    image: nginx\n    command: |\n      echo hi\n",
        "multi-document stream": "services:\n  web:\n    image: nginx\n---\nservices: {}\n",
        "include": "include:\n  - other.yml\nservices:\n  web:\n    image: nginx\n",
        "extends": "services:\n  web:\n    image: nginx\n    extends:\n      service: base\n",
        "profiles": "services:\n  web:\n    image: nginx\n    profiles: [debug]\n",
    }

    def test_builtin_source_refuses_each_unsupported_construct(self) -> None:
        for construct, body in self.REFUSED_CONSTRUCTS.items():
            with self.subTest(construct=construct):
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn(construct, result.stderr)
                self.assertIn("brew install docker-compose", result.stderr)
                self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_builtin_source_error_names_file_and_line(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n    command: *cmd\n")
        result = self.docker("compose", "config")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("docker-compose.yml", result.stderr)
        self.assertIn("line 4", result.stderr)

    def test_builtin_source_refuses_several_files_with_the_install_hint(self) -> None:
        self._write_named_compose("one.yml")
        self._write_named_compose("two.yml")
        result = self.docker("compose", "-f", "one.yml", "-f", "two.yml", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("brew install docker-compose", result.stderr)

    def test_builtin_source_refuses_profiles_with_the_install_hint(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        for flags, env in ((["--profile", "debug"], {}), ([], {"COMPOSE_PROFILES": "debug"})):
            with self.subTest(flags=flags, env=env):
                self.env.pop("COMPOSE_PROFILES", None)
                self.env.update(env)
                result = self.docker("compose", *flags, "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("brew install docker-compose", result.stderr)
                self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_builtin_source_refuses_several_env_files_with_the_install_hint(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        (self.project / "e1.env").write_text("A=1\n")
        (self.project / "e2.env").write_text("B=2\n")
        result = self.docker("compose", "--env-file", "e1.env", "--env-file", "e2.env", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("brew install docker-compose", result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_builtin_source_refuses_compose_env_files_with_the_install_hint(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        (self.project / "e1.env").write_text("A=1\n")
        self.env["COMPOSE_ENV_FILES"] = "e1.env"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("COMPOSE_ENV_FILES", result.stderr)
        self.assertIn("brew install docker-compose", result.stderr)
        # The command line wins over the variable, so one --env-file still works.
        explicit = self.docker("compose", "--env-file", "e1.env", "up", "-d")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)

    def test_builtin_source_fails_for_an_explicit_missing_env_file(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        result = self.docker("compose", "--env-file", "nope.env", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stderr)
        self.assertIn("couldn't find env file", result.stderr)
        self.assertIn(os.path.join(os.path.realpath(self.project), "nope.env"), result.stderr)
        self.assertFalse(self.state.exists() and self.load_state()["containers"])

    def test_builtin_source_ignores_a_missing_default_dotenv(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_invalid_mount_in_a_later_service_stops_before_any_side_effect(self) -> None:
        self.write_compose(
            """
            services:
              a:
                image: nginx
                volumes: ["data:/d"]
              b:
                image: nginx
                volumes: ["./data:/data:Z"]
            volumes:
              data: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertIn("'b'", result.stderr)
        self.assertIn("SELinux", result.stderr)
        self.assertNotIn("Creating", result.stdout + result.stderr)
        self.assert_nothing_was_created()

    def test_untranslatable_mounts_are_refused_together_without_a_traceback(self) -> None:
        self.write_compose(
            """
            services:
              a:
                image: nginx
                volumes: ["/tmp/a,b:/d"]
              b:
                image: nginx
                volumes: ["data:relative"]
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertNotIn("Traceback", result.stderr)
        self.assertEqual(result.stderr.count("REFUSED:"), 2, result.stderr)
        self.assertIn("'a'", result.stderr)
        self.assertIn("'b'", result.stderr)
        self.assert_nothing_was_created()

    def test_build_also_stops_before_any_build_on_an_invalid_mount(self) -> None:
        self.write_compose(
            """
            services:
              a:
                build: .
                volumes: ["data:/d:Z"]
            """
        )
        result = self.docker("compose", "build")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assert_nothing_was_created()

    def test_selinux_refusal_is_not_waivable_and_leaves_the_file_alone(self) -> None:
        body = "services:\n  web:\n    image: nginx\n    volumes: ['./d:/d:z']\n"
        self.write_compose(body)
        self.env["CONTAINER_DOCKER_SHIM_ALLOW_UNENFORCED"] = "volumes.z,volumes.Z,volumes.selinux"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("remove it", result.stderr)
        self.assertEqual((self.project / "docker-compose.yml").read_text(), body)
        self.assert_nothing_was_created()

    SECRET_PROJECT = """
        services:
          api:
            image: api:local
            build:
              context: .
              secrets:
                - %s
        secrets:
          %s
        """

    def write_secret_project(self, source: str, definition: str) -> None:
        self.write_compose(self.SECRET_PROJECT % (source, definition))
        self.env["FAKE_CONTAINER_MISSING_IMAGES"] = "api:local"

    def test_missing_secret_file_is_refused_before_any_side_effect(self) -> None:
        self.write_secret_project("tok", "tok:\n            file: ./missing-token.txt")
        for command in (("up", "-d"), ("build",)):
            with self.subTest(command=command):
                result = self.docker("compose", *command)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("REFUSED:", result.stderr)
                self.assertIn("'api'", result.stderr)
                self.assertIn("'tok'", result.stderr)
                self.assertIn("missing-token.txt", result.stderr)
                self.assert_nothing_was_created()

    def test_unset_secret_variable_is_refused_and_never_echoed(self) -> None:
        self.write_secret_project("tok", "tok:\n            environment: UNSET_SECRET_VAR")
        self.env.pop("UNSET_SECRET_VAR", None)
        self.env["OTHER_SECRET_VALUE"] = "sentinel-do-not-print"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("UNSET_SECRET_VAR", result.stderr)
        self.assertIn("export", result.stderr)
        self.assertNotIn("sentinel-do-not-print", result.stdout + result.stderr)
        self.assert_nothing_was_created()

    def test_variable_only_in_dotenv_is_refused(self) -> None:
        self.write_secret_project("tok", "tok:\n            environment: DOTENV_ONLY_SECRET")
        (self.project / ".env").write_text("DOTENV_ONLY_SECRET=value-from-dotenv\n")
        self.env.pop("DOTENV_ONLY_SECRET", None)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("DOTENV_ONLY_SECRET", result.stderr)
        self.assertNotIn("value-from-dotenv", result.stdout + result.stderr)
        self.assert_nothing_was_created()

    def test_structurally_invalid_secret_definitions_are_refused(self) -> None:
        cases = {
            "undefined": ("ghost", "other:\n            file: ./x.txt"),
            "external": ("tok", "tok:\n            external: true"),
            "both": ("tok", "tok:\n            file: ./x.txt\n            environment: VAR"),
            "neither": ("tok", "tok: {}"),
        }
        for label, (source, definition) in cases.items():
            with self.subTest(label):
                self.write_secret_project(source, definition)
                result = self.docker("compose", "up", "-d")
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("REFUSED:", result.stderr)
                self.assertIn(source, result.stderr)
                self.assert_nothing_was_created()

    def test_present_image_without_build_does_not_need_the_secret_file(self) -> None:
        self.write_secret_project("tok", "tok:\n            file: ./missing-token.txt")
        self.env.pop("FAKE_CONTAINER_MISSING_IMAGES")  # the image exists locally
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        # ... but `--build` makes the build (and its secret) real again.
        rebuilt = self.docker("compose", "up", "-d", "--build")
        self.assertEqual(rebuilt.returncode, 64, rebuilt.stderr)
        self.assertIn("missing-token.txt", rebuilt.stderr)

    def test_present_image_still_refuses_a_structurally_invalid_definition(self) -> None:
        self.write_secret_project("tok", "tok:\n            external: true")
        self.env.pop("FAKE_CONTAINER_MISSING_IMAGES")
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("tok", result.stderr)
        self.assert_nothing_was_created()

    def test_runtime_secrets_stay_refused_next_to_a_build_secret(self) -> None:
        self.write_compose(
            """
            services:
              api:
                image: api:local
                build:
                  context: .
                  secrets: [tok]
                secrets: [tok]
            secrets:
              tok:
                file: ./token.txt
            """
        )
        (self.project / "token.txt").write_text("dummy\n")
        self.env["FAKE_CONTAINER_MISSING_IMAGES"] = "api:local"
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertTrue(
            any("REFUSED:" in line and "secrets" in line for line in result.stderr.splitlines()),
            result.stderr,
        )
        self.assert_nothing_was_created()

    def test_secret_value_reaches_no_output_and_no_argument(self) -> None:
        self.write_secret_project("tok", "tok:\n            environment: BUILD_SECRET_VAR")
        self.env["BUILD_SECRET_VAR"] = "sentinel-secret-value-123"
        result = self.docker("compose", "build")  # the fake runtime then fails the build
        everything = result.stdout + result.stderr + json.dumps(self.runtime_calls())
        self.assertNotIn("sentinel-secret-value-123", everything)
        secret_args = [
            call[i + 1] for call in self.runtime_calls() if call[:1] == ["build"]
            for i, arg in enumerate(call) if arg == "--secret"
        ]
        self.assertEqual(secret_args, ["id=tok,env=BUILD_SECRET_VAR"])

    def test_builtin_source_refuses_an_automatic_override_file(self) -> None:
        self.write_compose("services:\n  web:\n    image: nginx\n")
        (self.project / "docker-compose.override.yml").write_text(
            "services:\n  web:\n    image: other\n"
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("docker-compose.override.yml", result.stderr)
        self.assertIn("brew install docker-compose", result.stderr)
        # An explicit -f opts out of the automatic merge, so it still works.
        explicit = self.docker("compose", "-f", "docker-compose.yml", "up", "-d")
        self.assertEqual(explicit.returncode, 0, explicit.stderr)

    def test_builtin_source_still_reads_the_supported_subset(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                command: ["sh", "-c", "a && b | c"]
                environment:
                  URL: http://x?a=1&b=2
                  NAME: ${NAME:-fallback}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("demo-web-1", self.load_state()["containers"])

    # -- Both sources drive the runtime identically ------------------------ #

    EQUIVALENCE_YAML = """
        services:
          db:
            image: postgres:16
            environment:
              POSTGRES_PASSWORD: secret
            volumes:
              - data:/var/lib/postgresql/data
          web:
            image: nginx:latest
            ports:
              - "8080:80"
              - "127.0.0.1:9090:90/udp"
            environment:
              MODE: prod
            depends_on:
              - db
        volumes:
          data: {}
    """

    def equivalence_model(self) -> dict:
        """What `docker-compose config --format json` prints for EQUIVALENCE_YAML."""
        return {
            "name": "demo",
            "networks": {"default": {"name": "demo_default", "ipam": {}}},
            "services": {
                "db": {
                    "command": None,
                    "entrypoint": None,
                    "environment": {"POSTGRES_PASSWORD": "secret"},
                    "image": "postgres:16",
                    "networks": {"default": None},
                    "volumes": [
                        {
                            "type": "volume",
                            "source": "data",
                            "target": "/var/lib/postgresql/data",
                            "volume": {},
                        }
                    ],
                },
                "web": {
                    "command": None,
                    "depends_on": {"db": {"condition": "service_started", "required": True}},
                    "entrypoint": None,
                    "environment": {"MODE": "prod"},
                    "image": "nginx:latest",
                    "networks": {"default": None},
                    "ports": [
                        {"mode": "ingress", "target": 80, "published": "8080", "protocol": "tcp"},
                        {
                            "mode": "ingress",
                            "host_ip": "127.0.0.1",
                            "target": 90,
                            "published": "9090",
                            "protocol": "udp",
                        },
                    ],
                },
            },
            "volumes": {"data": {"name": "demo_data"}},
        }

    def runtime_trace(self) -> tuple[list, dict]:
        """`up -d`, then the runtime commands and `run` argv it produced.

        The config-hash label is masked: it fingerprints the model dictionary,
        which legitimately differs in shape between the two sources.
        """
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()
        runs = {}
        for name, container in state["containers"].items():
            argv = []
            for item in container["run_argv"]:
                if item.startswith("com.docker.compose.config-hash="):
                    item = "com.docker.compose.config-hash=<hash>"
                argv.append(item)
            runs[name] = argv
        return state["action_log"], runs

    def test_builtin_and_official_sources_produce_the_same_runtime_commands(self) -> None:
        self.write_compose(self.EQUIVALENCE_YAML)
        builtin = self.runtime_trace()
        self.state.unlink()
        self.install_fake_normalizer(self.equivalence_model())
        official = self.runtime_trace()
        self.assertEqual(official[0], builtin[0])
        self.assertEqual(official[1], builtin[1])
        self.assertIn("0.0.0.0:8080:80", builtin[1]["demo-web-1"])
        self.assertIn("127.0.0.1:9090:90/udp", builtin[1]["demo-web-1"])

    def test_real_official_binary_matches_the_builtin_source(self) -> None:
        real = shutil.which("docker-compose")
        if real is None or subprocess.run(
            [real, "version", "--short"], capture_output=True, text=True
        ).stdout.strip() != "5.6.0":
            self.skipTest("docker-compose 5.6.0 is not on PATH")
        self.write_compose(self.EQUIVALENCE_YAML)
        builtin = self.runtime_trace()
        self.state.unlink()
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = real
        self.env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        official = self.runtime_trace()
        self.assertEqual(official[0], builtin[0])
        self.assertEqual(official[1], builtin[1])


    DRY_COMPOSE = """
        services:
          db:
            image: postgres:16
            volumes:
              - data:/var/lib/postgresql/data
          web:
            image: nginx:latest
            depends_on: [db]
            volumes:
              - /cache
          other:
            image: alpine:3.20
        volumes:
          data: {}
        """

    def dry_up(self, *args: str, flag_first: bool = True) -> subprocess.CompletedProcess[str]:
        if flag_first:
            return self.docker("compose", "--dry-run", "up", *args)
        return self.docker("compose", "up", "--dry-run", *args)

    def test_dry_run_up_prints_the_plan_and_calls_only_read_only_verbs(self) -> None:
        self.write_compose(self.DRY_COMPOSE)
        result = self.dry_up("-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = result.stdout.splitlines()
        self.assertIn("Network demo_default", lines)
        self.assertIn("Volume demo_data", lines)
        self.assertIn("Volume (anonymous, service web) at /cache", lines)
        containers = [line for line in lines if line.startswith("Container ")]
        self.assertEqual(
            [line.split()[1] for line in containers],
            ["demo-db-1", "demo-web-1", "demo-other-1"],
        )
        self.assertLess(lines.index("Container demo-db-1 (service db)"),
                        lines.index("Container demo-web-1 (service web)"))
        self.assertIn("only the configuration was checked", lines[-1])
        self.assertEqual(mutating_calls(self.runtime_calls()), [])

    def test_the_read_only_check_fails_for_a_real_up(self) -> None:
        self.write_compose(self.DRY_COMPOSE)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertTrue(mutating_calls(self.runtime_calls()))

    def test_the_read_only_check_catches_a_mutating_stub(self) -> None:
        stub = [["list", "--all"], ["volume", "create", "demo_data"], ["run", "-d", "nginx"]]
        self.assertEqual(
            mutating_calls(stub), [["volume", "create", "demo_data"], ["run", "-d", "nginx"]]
        )

    def test_dry_run_flag_after_up_is_not_a_global_flag(self) -> None:
        self.write_compose(self.DRY_COMPOSE)
        result = self.dry_up(flag_first=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.runtime_calls() and mutating_calls(self.runtime_calls()), [])

    def test_dry_run_up_with_a_service_honours_the_selection(self) -> None:
        self.write_compose(self.DRY_COMPOSE)
        result = self.dry_up("web")
        self.assertEqual(result.returncode, 0, result.stderr)
        containers = [l for l in result.stdout.splitlines() if l.startswith("Container ")]
        self.assertEqual(
            containers,
            ["Container demo-db-1 (service db)", "Container demo-web-1 (service web)"],
        )
        self.assertNotIn("demo-other-1", result.stdout)
        alone = self.dry_up("other")
        self.assertEqual(alone.returncode, 0, alone.stderr)
        self.assertNotIn("demo_data", alone.stdout)
        self.assertNotIn("demo-web-1", alone.stdout)

    def test_dry_run_reports_a_refused_key_like_up(self) -> None:
        self.write_compose(
            """
            services:
              worker:
                image: alpine:3.20
                network_mode: none
            """
        )
        dry = self.dry_up()
        real = self.docker("compose", "up", "-d")
        self.assertEqual(dry.returncode, 64, dry.stderr)
        self.assertEqual(real.returncode, 64, real.stderr)
        refused = [l for l in dry.stderr.splitlines() if l.startswith("REFUSED:")]
        self.assertTrue(refused)
        self.assertEqual(refused, [l for l in real.stderr.splitlines() if l.startswith("REFUSED:")])
        self.assertEqual(mutating_calls(self.runtime_calls()[:1]), [])

    def test_dry_run_reports_a_managed_name_conflict_like_up(self) -> None:
        self._seed_resource("volumes", "demo_data", {"com.docker.compose.project": "other"})
        self.write_compose(self.DRY_COMPOSE)
        dry = self.dry_up()
        self.assertEqual(dry.returncode, 1, dry.stderr)
        self.assertIn("belongs to project", dry.stderr)
        self.assertEqual(mutating_calls(self.runtime_calls()), [])

    def test_dry_run_reports_a_missing_build_secret_file(self) -> None:
        self.write_secret_project("tok", "tok:\n            file: ./missing-token.txt")
        result = self.dry_up()
        self.assertEqual(result.returncode, 64, result.stderr)
        self.assertIn("REFUSED:", result.stderr)
        self.assertIn("missing-token.txt", result.stderr)
        self.assertEqual(mutating_calls(self.runtime_calls()), [])

    def test_dry_run_with_other_commands_exits_64_and_calls_nothing(self) -> None:
        self.write_compose(self.DRY_COMPOSE)
        for command in ("down", "stop", "rm", "build", "pull", "ps"):
            with self.subTest(command=command):
                result = self.docker("compose", "--dry-run", command)
                self.assertEqual(result.returncode, 64, result.stderr)
                self.assertIn("only supported with 'up'", result.stderr)
        self.assertEqual(self.runtime_calls(), [])


class ImplicitDefaultNetworkTests(ComposeFakeBase):
    """The implicit `default` network follows the plan of the `up` it serves.

    Every test asserts `created_networks()` (the `network create` calls the
    runtime received) and the final `state["networks"]`, including the labels
    of every network that was created.
    """

    def _assert_labels(self, state: dict, name: str, key: str) -> None:
        labels = state["networks"][name]["configuration"]["labels"]
        self.assertEqual(labels["com.docker.compose.project"], "demo")
        self.assertEqual(labels["com.docker.compose.network"], key)

    def _seed_container(self, name: str, service: str, network: str) -> None:
        self._seed_state() if not self.state.exists() else None
        state = self.load_state()
        labels = {
            "com.docker.compose.project": "demo",
            "com.docker.compose.service": service,
        }
        state["containers"][name] = {
            "id": name,
            "name": name,
            "image": "nginx:latest",
            "labels": labels,
            "networks": [network],
            "dns": [],
            "dns_search": [],
            "dns_opt": [],
            "entrypoint": None,
            "cmd": [],
            "run_argv": [],
            "configuration": {
                "id": name,
                "labels": labels,
                "mounts": [],
                "image": {"reference": "nginx:latest"},
            },
            "status": {"state": "running", "networks": []},
        }
        self.state.write_text(json.dumps(state))

    # -- `up` creates exactly the networks the planned services use --------- #

    def test_service_without_networks_creates_default(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), ["demo_default"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_default"})
        self._assert_labels(state, "demo_default", "default")

    def test_single_external_network_creates_nothing(self) -> None:
        self._seed_state()
        state = self.load_state()
        state["networks"]["edge"] = {"configuration": {"name": "edge", "labels": {}}, "id": "edge"}
        self.state.write_text(json.dumps(state))
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
            networks:
              edge:
                external: true
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), [])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"edge"})
        argv = state["containers"]["demo-web-1"]["run_argv"]
        self.assertEqual(argv[argv.index("--network") + 1], "edge")

    def test_single_managed_network_creates_only_its_own(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        created = self.created_networks()
        # The single network is created, before the fix too; the fix is that
        # nothing else is.
        self.assertIn("demo_back", created)
        self.assertEqual(created, ["demo_back"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back"})
        self._assert_labels(state, "demo_back", "back")

    def test_mixed_services_create_each_network_once_in_plan_order(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
              db:
                image: postgres:16
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        # Both networks are created exactly once, in the plan's order: `web`
        # (back) is planned before `db` (default), so default no longer comes
        # first.
        self.assertEqual(self.created_networks(), ["demo_back", "demo_default"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back", "demo_default"})
        self._assert_labels(state, "demo_back", "back")
        self._assert_labels(state, "demo_default", "default")

    def test_named_default_and_default_plus_back(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [default]
              db:
                image: postgres:16
                networks: [default, back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), ["demo_default", "demo_back"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_default", "demo_back"})
        self._assert_labels(state, "demo_default", "default")
        self._assert_labels(state, "demo_back", "back")

    def test_declared_default_no_service_uses_creates_nothing(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              default: {}
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), ["demo_back"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back"})
        self._assert_labels(state, "demo_back", "back")

    # -- No side effect when a needed network is missing or conflicts ------- #

    def test_missing_external_network_blocks_up_without_side_effects(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
              db:
                image: postgres:16
            networks:
              edge:
                external: true
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("edge", up.stderr)
        self.assertIn("external", up.stderr)
        self.assertEqual(self.created_networks(), [])
        self.assert_nothing_was_created()
        self.assertEqual(self.load_state()["networks"], {})

    def test_used_default_labelled_for_another_project_is_refused(self) -> None:
        self._seed_resource("networks", "demo_default", {"com.docker.compose.project": "other"})
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 1, up.stderr)
        self.assertIn("belongs to project", up.stderr)
        self.assertIn("other", up.stderr)
        self.assertEqual(self.created_networks(), [])
        self.assert_nothing_was_created()
        state = self.load_state()
        self.assertEqual(
            state["networks"]["demo_default"]["configuration"]["labels"],
            {"com.docker.compose.project": "other"},
        )

    def test_unused_default_labelled_for_another_project_does_not_block_up(self) -> None:
        self._seed_resource("networks", "demo_default", {"com.docker.compose.project": "other"})
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              back: {}
            """
        )
        up = self.docker("compose", "up", "-d")
        self.assertEqual(up.returncode, 0, up.stderr)
        self.assertEqual(self.created_networks(), ["demo_back"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back", "demo_default"})
        back_labels = state["networks"]["demo_back"]["configuration"]["labels"]
        default_labels = state["networks"]["demo_default"]["configuration"]["labels"]
        self.assertEqual(back_labels["com.docker.compose.project"], "demo")
        self.assertEqual(default_labels["com.docker.compose.project"], "other")

    # -- The dry run reports the plan, in the plan's order ------------------ #

    def test_dry_run_prints_only_used_networks_in_plan_order(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
              db:
                image: postgres:16
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "--dry-run", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("Network ")]
        # The plan's order: `web` (back) first, `db` (default) second.
        self.assertEqual(lines, ["Network demo_back", "Network demo_default"])
        self.assert_nothing_was_created()
        # The common single-network case keeps its output: one `default`.
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        result = self.docker("compose", "--dry-run", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("Network ")]
        self.assertEqual(lines, ["Network demo_default"])
        self.assert_nothing_was_created()

    def test_dry_run_with_only_explicit_networks_lists_them_and_no_default(self) -> None:
        self._seed_state()
        state = self.load_state()
        state["networks"]["edge"] = {"configuration": {"name": "edge", "labels": {}}, "id": "edge"}
        self.state.write_text(json.dumps(state))
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
              db:
                image: postgres:16
                networks: [back]
            networks:
              edge:
                external: true
              back: {}
            """
        )
        result = self.docker("compose", "--dry-run", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        lines = [line for line in result.stdout.splitlines() if line.startswith("Network ")]
        self.assertEqual(lines, ["Network edge (external, must exist)", "Network demo_back"])
        self.assertNotIn("demo_default", result.stdout)
        self.assertEqual(self.created_networks(), [])
        self.assert_nothing_was_created()
        self.assertEqual(set(self.load_state()["networks"]), {"edge"})

    def test_dry_run_missing_external_network_exits_1_without_printing_the_plan(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [edge]
              db:
                image: postgres:16
            networks:
              edge:
                external: true
            """
        )
        result = self.docker("compose", "--dry-run", "up", "-d")
        self.assertEqual(result.returncode, 1, result.stderr)
        # The same message a real `up` prints for the missing external.
        self.assertIn(
            "network edge declared as external, but could not be found",
            result.stderr,
        )
        self.assertNotIn("Dry run of project", result.stdout)
        self.assertEqual(self.created_networks(), [])
        self.assert_nothing_was_created()
        self.assertEqual(self.load_state()["networks"], {})

    def test_dry_run_unused_default_labelled_for_another_project_reports_no_conflict(self) -> None:
        self._seed_resource("networks", "demo_default", {"com.docker.compose.project": "other"})
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "--dry-run", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("belongs to project", result.stderr)
        self.assertNotIn("demo_default", result.stdout)
        self.assertEqual(self.created_networks(), [])
        self.assert_nothing_was_created()
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_default"})
        self.assertEqual(
            state["networks"]["demo_default"]["configuration"]["labels"],
            {"com.docker.compose.project": "other"},
        )

    # -- Selection and dependencies restrict the set ------------------------ #

    def test_up_api_leaves_default_uncreated_when_only_web_uses_it(self) -> None:
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                networks: [back]
              web:
                image: nginx:latest
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d", "api")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), ["demo_back"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back"})
        self._assert_labels(state, "demo_back", "back")
        self.assertEqual(set(state["containers"]), {"demo-api-1"})

    def test_up_web_with_dependency_on_back_creates_both(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
                networks: [back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d", "web")
        self.assertEqual(result.returncode, 0, result.stderr)
        # The dependency is planned first, so its network is created first.
        self.assertEqual(self.created_networks(), ["demo_back", "demo_default"])
        state = self.load_state()
        self.assertEqual(set(state["networks"]), {"demo_back", "demo_default"})
        self._assert_labels(state, "demo_back", "back")
        self._assert_labels(state, "demo_default", "default")

    def test_up_no_deps_web_does_not_create_back(self) -> None:
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
                networks: [back]
            networks:
              back: {}
            """
        )
        result = self.docker("compose", "up", "-d", "--no-deps", "web")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(self.created_networks(), ["demo_default"])
        state = self.load_state()
        self.assertNotIn("demo_back", state["networks"])
        self.assertNotIn("demo-db-1", state["containers"])

    # -- `down` keeps its ownership rules ----------------------------------- #

    def test_down_never_removes_a_default_the_project_never_created(self) -> None:
        self._seed_resource(
            "networks",
            "demo_back",
            {"com.docker.compose.project": "demo", "com.docker.compose.network": "back"},
        )
        self._seed_container("demo-web-1", "web", "demo_back")
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
                networks: [back]
            networks:
              back: {}
            """
        )
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        rms = [call[-1] for call in self.runtime_calls() if call[:2] == ["network", "rm"]]
        self.assertEqual(rms, ["demo_back"])
        self.assertEqual(self.load_state()["networks"], {})

    def test_down_removes_a_default_labelled_for_the_project(self) -> None:
        self._seed_resource(
            "networks",
            "demo_default",
            {"com.docker.compose.project": "demo", "com.docker.compose.network": "default"},
        )
        self._seed_container("demo-web-1", "web", "demo_default")
        self.write_compose(
            """
            services:
              web:
                image: nginx:latest
            """
        )
        down = self.docker("compose", "down")
        self.assertEqual(down.returncode, 0, down.stderr)
        rms = [call[-1] for call in self.runtime_calls() if call[:2] == ["network", "rm"]]
        self.assertEqual(rms, ["demo_default"])
        self.assertEqual(self.load_state()["networks"], {})

    # -- The official normalizer -------------------------------------------- #

    def test_official_normalizer_yields_default_for_empty_network_forms(self) -> None:
        # Characterization (design D2): what the official normalizer yields
        # for `networks: []` and `networks: {}` — the same implicit `default`
        # as a service that declares none, so such a service uses `default`.
        executable = shutil.which("docker-compose")
        if executable is None or subprocess.run(
            [executable, "version", "--short"], capture_output=True, text=True
        ).stdout.strip() != "5.6.0":
            self.skipTest("docker-compose 5.6.0 is not on PATH")
        self.write_compose(
            """
            services:
              empty_list:
                image: alpine:3.20
                networks: []
              empty_map:
                image: alpine:3.20
                networks: {}
            """
        )
        env = dict(os.environ)
        env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        for name in [name for name in env if name.startswith("COMPOSE_")]:
            del env[name]
        result = subprocess.run(
            [executable, "-p", "demo", "config", "--format", "json"],
            env=env,
            capture_output=True,
            text=True,
            cwd=str(self.project),
            timeout=60,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        model = json.loads(result.stdout)
        for service in ("empty_list", "empty_map"):
            with self.subTest(service=service):
                self.assertEqual(
                    model["services"][service]["networks"],
                    {"default": None},
                )


class ReservedComposeLabelTests(ComposeFakeBase):
    """The `com.docker.compose` label namespace belongs to the shim.

    A user label inside it — map or list form, on a service, a network or a
    volume — must be refused before any runtime mutation: a non-zero exit
    with a `REFUSED:` line naming the offending key and the resource, and an
    unchanged fake-runtime state (no containers, networks or volumes).

    The service-level check (task 2.1) has landed, so the service, in-use
    network and in-use volume tests pass. The unused- and external-resource
    tests (task 1.4) are expected to fail until task 2.2 widens the check to
    every declared network and volume (Q3 resolved): today the shim only
    validates the resources the planned services use, so a reserved label on
    an unused or `external: true` resource is accepted and `up` succeeds. The
    exit code is asserted as non-zero rather than a fixed 64 because design
    Q2 leaves the exact code open; tighten to 64 once Q2 is resolved.

    The official-normalizer tests (design D5) exercise the same refusals when
    the model arrives normalized by the real `docker-compose` binary instead
    of the built-in parser; they skip cleanly when `docker-compose` 5.6.0 is
    not on PATH, like the other real-binary characterization tests.
    """

    def _assert_state_untouched(self) -> None:
        state = self.load_state()
        self.assertEqual(state["containers"], {})
        self.assertEqual(state["networks"], {})
        self.assertEqual(state["volumes"], {})

    def _assert_refused(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertNotEqual(result.returncode, 0, result.stderr)
        self.assertIn("REFUSED:", result.stderr)

    def _assert_only_read_only_runtime_calls(self) -> None:
        """Every recorded runtime call is read-only (list/inspect-style).

        Asserts on the actual call log: any call that creates, runs, starts,
        stops, modifies or removes anything fails the test, whatever else the
        log contains.
        """
        for call in self.runtime_calls():
            verb = call[0]
            if verb in ("image", "network", "volume", "system"):
                sub = call[1] if len(call) > 1 else ""
                read_only = sub in ("inspect", "list", "status")
            else:
                read_only = verb in ("list", "inspect", "logs", "--version")
            self.assertTrue(read_only, f"mutating runtime call reached the runtime: {call!r}")

    def test_service_reserved_label_map_is_refused(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.compose.project: another-project
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.project", result.stderr)
        self.assertIn("api", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_service_reserved_label_list_is_refused(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  - com.docker.compose.service=other
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.service", result.stderr)
        self.assertIn("api", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_network_reserved_label_is_refused(self) -> None:
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    networks: [back]
                networks:
                  back:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    networks: [back]
                networks:
                  back:
                    labels:
                      - com.docker.compose.network=back
                """,
                "com.docker.compose.network",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("back", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_volume_reserved_label_is_refused(self) -> None:
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    volumes:
                      - data:/data
                volumes:
                  data:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    volumes:
                      - data:/data
                volumes:
                  data:
                    labels:
                      - com.docker.compose.volume=data
                """,
                "com.docker.compose.volume",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("data", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_several_offending_labels_are_reported_together(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.compose.project: other
                networks: [back]
            networks:
              back:
                labels:
                  com.docker.compose.network: other
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        refused = [line for line in result.stderr.splitlines() if line.startswith("REFUSED:")]
        self.assertEqual(len(refused), 2, result.stderr)
        self.assertIn("com.docker.compose.project", result.stderr)
        self.assertIn("com.docker.compose.network", result.stderr)
        self.assertIn("api", result.stderr)
        self.assertIn("back", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_error_text_names_the_offending_key_and_resource(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                networks: [back]
            networks:
              back:
                labels:
                  com.docker.compose.project: other-project
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.project", result.stderr)
        self.assertIn("back", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_dry_run_reports_the_same_refusal_as_up(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.compose.project: another-project
            """
        )
        dry = self.docker("compose", "--dry-run", "up", "-d")
        real = self.docker("compose", "up", "-d")
        self.assertEqual(dry.returncode, real.returncode)
        self.assertNotEqual(real.returncode, 0, real.stderr)
        refused_dry = [line for line in dry.stderr.splitlines() if line.startswith("REFUSED:")]
        refused_real = [line for line in real.stderr.splitlines() if line.startswith("REFUSED:")]
        self.assertTrue(refused_dry, dry.stderr)
        self.assertEqual(refused_dry, refused_real)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    # -- Review findings (task 1.4) ----------------------------------------- #
    #
    # The independent review found that the task 2.1 check only validates the
    # networks and volumes the planned services use, and skips external ones.
    # Q3 is resolved: every declared network and volume is validated, used or
    # not, managed or external. The unused- and external-resource tests below
    # are expected to fail until task 2.2 widens the check; the exact-key,
    # bare-list-entry and pre-existing-state tests pin behaviour the current
    # implementation already has.

    def test_unused_network_reserved_label_is_refused(self) -> None:
        # Q3: a declared network is validated even when no service uses it.
        # Until task 2.2 the shim never looks at it, so `up` succeeds and the
        # label is accepted.
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                networks:
                  back:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                networks:
                  back:
                    labels:
                      - com.docker.compose.network=back
                """,
                "com.docker.compose.network",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("back", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_unused_volume_reserved_label_is_refused(self) -> None:
        # Q3: a declared volume is validated even when no service mounts it.
        # Until task 2.2 the shim never looks at it, so `up` succeeds and the
        # label is accepted.
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                volumes:
                  data:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                volumes:
                  data:
                    labels:
                      - com.docker.compose.volume=data
                """,
                "com.docker.compose.volume",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("data", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_external_network_and_volume_reserved_labels_are_refused(self) -> None:
        # Q3: `external: true` resources are validated too. They are left
        # unused by the service, so the fake state needs no live counterpart;
        # until task 2.2 the shim skips external resources and `up` succeeds.
        for body, key, resource in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                networks:
                  back:
                    external: true
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
                "back",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                networks:
                  back:
                    external: true
                    labels:
                      - com.docker.compose.network=back
                """,
                "com.docker.compose.network",
                "back",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                volumes:
                  data:
                    external: true
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
                "data",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                volumes:
                  data:
                    external: true
                    labels:
                      - com.docker.compose.volume=data
                """,
                "com.docker.compose.volume",
                "data",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn(resource, result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_exact_namespace_key_and_bare_list_entry_are_refused(self) -> None:
        # The namespace boundary is the exact key `com.docker.compose` (no
        # suffix) and a list entry without `=` (a bare key, no value).
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    labels:
                      com.docker.compose: other
                """,
                "com.docker.compose",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    labels:
                      - com.docker.compose.project
                """,
                "com.docker.compose.project",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("api", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_refusal_leaves_preexisting_resources_byte_for_byte_unchanged(self) -> None:
        # A live project: a network, a volume and the container for the very
        # service that carries the reserved label already exist in the fake
        # runtime. The refusal must happen before any runtime call that
        # creates, runs, starts, stops, modifies or removes anything, and the
        # pre-existing resources must survive byte for byte.
        self._seed_state()
        network = {
            "configuration": {
                "name": "demo_back",
                "labels": {
                    "com.docker.compose.project": "demo",
                    "com.docker.compose.network": "back",
                },
            },
            "id": "demo_back",
        }
        volume = {
            "configuration": {
                "name": "demo_data",
                "labels": {
                    "com.docker.compose.project": "demo",
                    "com.docker.compose.volume": "data",
                },
            },
            "id": "demo_data",
        }
        labels = {
            "com.docker.compose.project": "demo",
            "com.docker.compose.service": "api",
            "com.docker.compose.container-number": "1",
            "com.docker.compose.oneoff": "False",
            "com.docker.compose.config-hash": "0" * 64,
            "com.docker.compose.image": "fakeid-alpine:3.20",
        }
        container = {
            "id": "demo-api-1",
            "name": "demo-api-1",
            "image": "alpine:3.20",
            "labels": labels,
            "networks": ["demo_back"],
            "dns": [],
            "dns_search": [],
            "dns_opt": [],
            "entrypoint": None,
            "cmd": [],
            "run_argv": [],
            "configuration": {
                "id": "demo-api-1",
                "labels": labels,
                "mounts": [],
                "image": {"reference": "alpine:3.20"},
            },
            "status": {"state": "running", "networks": []},
        }
        state = self.load_state()
        state["networks"]["demo_back"] = network
        state["volumes"]["demo_data"] = volume
        state["containers"]["demo-api-1"] = container
        self.state.write_text(json.dumps(state))
        before = self.state.read_text()

        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.compose.project: other
                networks: [back]
                volumes:
                  - data:/data
            networks:
              back: {}
            volumes:
              data: {}
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.project", result.stderr)
        self.assertIn("api", result.stderr)

        after = self.state.read_text()
        self.assertEqual(after, before)
        state = self.load_state()
        self.assertEqual(state["networks"]["demo_back"], network)
        self.assertEqual(state["volumes"]["demo_data"], volume)
        self.assertEqual(state["containers"]["demo-api-1"], container)
        self._assert_only_read_only_runtime_calls()
        self.assert_nothing_was_created()

    # -- Official-normalizer path (design D5) ------------------------------ #
    #
    # The same refusals must hold when the model arrives normalized by the
    # official `docker-compose` binary instead of the built-in parser. The
    # normalizer converts list-form labels to a map, so both forms arrive as
    # maps; the validation must refuse either way.

    def _use_official_normalizer(self) -> None:
        """Point the shim at the real `docker-compose` 5.6.0, or skip.

        Same gate as the other real-binary tests: the official-normalizer
        path is only exercised when `docker-compose` 5.6.0 is on PATH. The
        `version` check guards against the env var silently not taking
        effect, so a skip or a failure is never mistaken for the built-in
        parser's behaviour.
        """
        real = shutil.which("docker-compose")
        if real is None or subprocess.run(
            [real, "version", "--short"], capture_output=True, text=True
        ).stdout.strip() != "5.6.0":
            self.skipTest("docker-compose 5.6.0 is not on PATH")
        self.env["CONTAINER_DOCKER_SHIM_COMPOSE"] = real
        self.env["DOCKER_HOST"] = "unix:///nonexistent.sock"
        version = self.docker("compose", "version")
        self.assertEqual(version.returncode, 0, version.stderr)
        self.assertEqual(version.stdout, "normalizer: official 5.6.0\n")

    def test_official_normalizer_reserved_service_label_map_is_refused(self) -> None:
        self._use_official_normalizer()
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.compose.project: another-project
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.project", result.stderr)
        self.assertIn("api", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_official_normalizer_reserved_service_label_list_is_refused(self) -> None:
        # The official normalizer converts the list to a map before the model
        # reaches the shim; the refusal must hold on the normalized model.
        self._use_official_normalizer()
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  - com.docker.compose.service=other
            """
        )
        result = self.docker("compose", "up", "-d")
        self._assert_refused(result)
        self.assertIn("com.docker.compose.service", result.stderr)
        self.assertIn("api", result.stderr)
        self.assert_nothing_was_created()
        self._assert_state_untouched()

    def test_official_normalizer_reserved_network_label_is_refused(self) -> None:
        self._use_official_normalizer()
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    networks: [back]
                networks:
                  back:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    networks: [back]
                networks:
                  back:
                    labels:
                      - com.docker.compose.network=back
                """,
                "com.docker.compose.network",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("back", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()

    def test_official_normalizer_reserved_volume_label_is_refused(self) -> None:
        self._use_official_normalizer()
        for body, key in (
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    volumes:
                      - data:/data
                volumes:
                  data:
                    labels:
                      com.docker.compose.project: other
                """,
                "com.docker.compose.project",
            ),
            (
                """
                services:
                  api:
                    image: alpine:3.20
                    volumes:
                      - data:/data
                volumes:
                  data:
                    labels:
                      - com.docker.compose.volume=data
                """,
                "com.docker.compose.volume",
            ),
        ):
            with self.subTest(key=key):
                self._seed_state()
                self.write_compose(body)
                result = self.docker("compose", "up", "-d")
                self._assert_refused(result)
                self.assertIn(key, result.stderr)
                self.assertIn("data", result.stderr)
                self.assert_nothing_was_created()
                self._assert_state_untouched()


class UserLabelsStillWorkTests(ComposeFakeBase):
    """Non-regression: user labels outside the reserved namespace keep working.

    These tests pin the behaviour change `reject-reserved-compose-labels`
    must not alter (spec `compose-resource-identity`, scenarios "Non-reserved
    labels keep working" and "Internal labels are not refused"): `up -d` exits
    0, every user label reaches the container, the managed network and the
    managed volume exactly once, next to the shim's ownership labels, and a
    service without any `labels:` keeps the internal labels the shim writes.
    They must pass both before and after task 2.1.
    """

    USER_LABELS = {
        "team": "platform",
        "com.example.owner": "ops",
    }

    MAP_BODY = """
    services:
      api:
        image: alpine:3.20
        labels:
          team: platform
          com.example.owner: ops
        networks: [back]
        volumes:
          - data:/data
    networks:
      back:
        labels:
          team: platform
          com.example.owner: ops
    volumes:
      data:
        labels:
          team: platform
          com.example.owner: ops
    """

    LIST_BODY = """
    services:
      api:
        image: alpine:3.20
        labels:
          - team=platform
          - com.example.owner=ops
        networks: [back]
        volumes:
          - data:/data
    networks:
      back:
        labels:
          - team=platform
          - com.example.owner=ops
    volumes:
      data:
        labels:
          - team=platform
          - com.example.owner=ops
    """

    @staticmethod
    def _label_occurrences(argv: list[str], label: str) -> int:
        """How many times `--label <label>` (or `--label=<label>`) appears in one argv."""
        count = 0
        i = 0
        while i < len(argv):
            if argv[i] == "--label" and i + 1 < len(argv) and argv[i + 1] == label:
                count += 1
                i += 2
                continue
            if argv[i] == f"--label={label}":
                count += 1
            i += 1
        return count

    def _created_argv(self, family: str, name: str) -> list[str]:
        """The argv of the `network`/`volume` create call for `name` (empty when none)."""
        for call in self.runtime_calls():
            if call[:2] == [family, "create"] and call[-1] == name:
                return call
        return []

    def _assert_user_labels(self, labels: dict, argv: list[str]) -> None:
        """Each user label is recorded exactly once, next to the shim's ownership label."""
        for key, value in self.USER_LABELS.items():
            self.assertEqual(labels.get(key), value)
            self.assertEqual(self._label_occurrences(argv, f"{key}={value}"), 1)
        self.assertEqual(labels.get("com.docker.compose.project"), "demo")

    def _assert_up_records_user_labels(self, body: str) -> None:
        self._seed_state()
        self.write_compose(body)
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        state = self.load_state()

        container = state["containers"]["demo-api-1"]
        self._assert_user_labels(container["labels"], container["run_argv"])
        self.assertEqual(container["labels"]["com.docker.compose.service"], "api")

        network = state["networks"]["demo_back"]["configuration"]
        self._assert_user_labels(network["labels"], self._created_argv("network", "demo_back"))
        self.assertEqual(network["labels"]["com.docker.compose.network"], "back")

        volume = state["volumes"]["demo_data"]["configuration"]
        self._assert_user_labels(volume["labels"], self._created_argv("volume", "demo_data"))
        self.assertEqual(volume["labels"]["com.docker.compose.volume"], "data")

    def test_non_reserved_labels_keep_working_on_service_network_and_volume(self) -> None:
        self._assert_up_records_user_labels(self.MAP_BODY)

    def test_non_reserved_labels_in_list_form_keep_working(self) -> None:
        self._assert_up_records_user_labels(self.LIST_BODY)

    def test_service_without_labels_keeps_the_internal_labels(self) -> None:
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        labels = self.load_state()["containers"]["demo-api-1"]["labels"]
        self.assertEqual(labels["com.docker.compose.project"], "demo")
        self.assertEqual(labels["com.docker.compose.service"], "api")
        self.assertEqual(labels["com.docker.compose.container-number"], "1")
        self.assertEqual(labels["com.docker.compose.oneoff"], "False")
        self.assertEqual(len(labels["com.docker.compose.config-hash"]), 64)
        self.assertEqual(labels["com.docker.compose.image"], "fakeid-alpine:3.20")

    def test_lookalike_key_outside_the_reserved_namespace_is_not_refused(self) -> None:
        # `com.docker.composer` shares the `com.docker.compose` prefix but is
        # neither that key itself nor a `com.docker.compose.*` key, so design
        # D1 (the exact prefix boundary of open question Q1) leaves it alone.
        self._seed_state()
        self.write_compose(
            """
            services:
              api:
                image: alpine:3.20
                labels:
                  com.docker.composer: x
            """
        )
        result = self.docker("compose", "up", "-d")
        self.assertEqual(result.returncode, 0, result.stderr)
        container = self.load_state()["containers"]["demo-api-1"]
        self.assertEqual(container["labels"].get("com.docker.composer"), "x")
        self.assertEqual(self._label_occurrences(container["run_argv"], "com.docker.composer=x"), 1)


class DependsConditionTests(unittest.TestCase):
    def project(self, body: str) -> compose.Project:
        return compose.Project("p", compose.parse_yaml(textwrap.dedent(body)), "/tmp/p")

    def test_list_form_defaults_to_service_started(self) -> None:
        proj = self.project(
            """
            services:
              web:
                image: nginx:latest
                depends_on: [db]
              db:
                image: postgres:16
            """
        )
        web = proj.services["web"]
        self.assertEqual(web.depends_conditions, {"db": "service_started"})
        self.assertEqual(web.depends_on, ["db"])

    def test_map_form_records_each_condition_and_keeps_order(self) -> None:
        proj = self.project(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
                    condition: service_healthy
                  cache:
                    condition: service_started
                  job:
                    condition: service_completed_successfully
              db:
                image: postgres:16
              cache:
                image: redis:7
              job:
                image: busybox
            """
        )
        web = proj.services["web"]
        self.assertEqual(
            web.depends_conditions,
            {
                "db": "service_healthy",
                "cache": "service_started",
                "job": "service_completed_successfully",
            },
        )
        self.assertEqual(web.depends_on, ["db", "cache", "job"])

    def test_map_form_empty_value_defaults_to_service_started(self) -> None:
        proj = self.project(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db: {}
              db:
                image: postgres:16
            """
        )
        self.assertEqual(
            proj.services["web"].depends_conditions, {"db": "service_started"}
        )

        proj = self.project(
            """
            services:
              web:
                image: nginx:latest
                depends_on:
                  db:
              db:
                image: postgres:16
            """
        )
        self.assertEqual(
            proj.services["web"].depends_conditions, {"db": "service_started"}
        )

    def test_unknown_condition_raises(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  web:
                    image: nginx:latest
                    depends_on:
                      db:
                        condition: bogus
                  db:
                    image: postgres:16
                """
            )
        )
        with self.assertRaises(compose.ShimError) as ctx:
            compose.Project("p", doc, "/tmp/p")
        self.assertEqual(ctx.exception.code, 64)
        message = str(ctx.exception)
        self.assertIn("web", message)
        self.assertIn("db", message)
        self.assertIn("bogus", message)


class ComposeEnvironmentFlagTests(unittest.TestCase):
    def apply(self, files, profiles, environ):
        return compose._apply_compose_environment(files, profiles, environ)

    def test_nothing_set_keeps_flags_untouched(self) -> None:
        self.assertEqual(self.apply(["a.yml"], ["p"], {}), (["a.yml"], ["p"]))

    def test_compose_file_is_split_on_the_path_separator(self) -> None:
        env = {"COMPOSE_FILE": os.pathsep.join(["a.yml", "b.yml", ""])}
        self.assertEqual(self.apply([], [], env), (["a.yml", "b.yml"], []))

    def test_compose_path_separator_overrides_the_default(self) -> None:
        env = {"COMPOSE_FILE": "a.yml,b.yml", "COMPOSE_PATH_SEPARATOR": ","}
        self.assertEqual(self.apply([], [], env), (["a.yml", "b.yml"], []))

    def test_explicit_files_win_over_compose_file(self) -> None:
        env = {"COMPOSE_FILE": "env.yml"}
        self.assertEqual(self.apply(["flag.yml"], [], env), (["flag.yml"], []))

    def test_compose_profiles_are_appended_after_flags(self) -> None:
        env = {"COMPOSE_PROFILES": "b, c,,"}
        self.assertEqual(self.apply([], ["a"], env), ([], ["a", "b", "c"]))


class GlobalOptionParsingTests(unittest.TestCase):
    """`main` collects every --env-file and stops at the subcommand."""

    def dispatched(self, argv):
        captured = {}

        def fake_dispatch(sub, sub_args, **kwargs):
            captured.update(kwargs, sub=sub, sub_args=sub_args)
            return 0

        original = compose._dispatch
        compose._dispatch = fake_dispatch
        try:
            self.assertEqual(compose.main(argv), 0)
        finally:
            compose._dispatch = original
        return captured

    def test_env_files_keep_their_order(self) -> None:
        got = self.dispatched(["--env-file", "a", "--env-file=b", "--env-file", "c", "ps"])
        self.assertEqual(got["env_files"], ("a", "b", "c"))
        self.assertEqual(got["sub"], "ps")

    def test_no_env_file_gives_an_empty_tuple(self) -> None:
        self.assertEqual(self.dispatched(["ps"])["env_files"], ())

    def test_exec_env_file_is_left_to_exec(self) -> None:
        got = self.dispatched(["exec", "--env-file", "x.env", "web", "true"])
        self.assertEqual(got["env_files"], ())
        self.assertEqual(got["sub_args"], ["--env-file", "x.env", "web", "true"])

    def test_equals_forms_fill_the_same_fields(self) -> None:
        got = self.dispatched(
            ["--project-directory=app", "--file=a.yml", "--profile=p", "--project-name=n", "ps"]
        )
        self.assertEqual(got["project_dir"], "app")
        self.assertEqual(got["files"], ["a.yml"])
        self.assertEqual(got["profiles"], ["p"])
        self.assertEqual(got["project_name"], "n")


class RunNormalizerTests(unittest.TestCase):
    def test_timeout_becomes_an_oserror_for_load_normalized(self) -> None:
        original = compose.NORMALIZER_TIMEOUT_SECONDS
        compose.NORMALIZER_TIMEOUT_SECONDS = 0.2
        self.addCleanup(setattr, compose, "NORMALIZER_TIMEOUT_SECONDS", original)
        with self.assertRaises(OSError) as raised:
            compose._run_normalizer([sys.executable, "-c", "import time; time.sleep(5)"], dict(os.environ))
        self.assertIn("did not finish", str(raised.exception))

    def test_timeout_is_reported_as_a_shim_error(self) -> None:
        original = compose.NORMALIZER_TIMEOUT_SECONDS
        compose.NORMALIZER_TIMEOUT_SECONDS = 0.2
        self.addCleanup(setattr, compose, "NORMALIZER_TIMEOUT_SECONDS", original)
        with tempfile.TemporaryDirectory() as tmp:
            slow = Path(tmp) / "docker-compose"
            slow.write_text("#!/bin/sh\nsleep 5\n")
            slow.chmod(0o755)
            with self.assertRaises(compose.ShimError) as raised:
                compose._load_normalized_model(str(slow), compose.compose_normalizer.Inputs())
        self.assertIn("did not finish", str(raised.exception))


class WaitTimeoutFlagTests(unittest.TestCase):
    def test_accepts_a_positive_number_in_both_forms(self) -> None:
        self.assertEqual(compose._extract_wait_timeout(["--wait-timeout", "2.5", "web"]), (2.5, ["web"]))
        self.assertEqual(compose._extract_wait_timeout(["--wait-timeout=7"]), (7.0, []))

    def test_rejects_non_positive_and_non_finite_values(self) -> None:
        for value in ("0", "-1", "abc", "nan", "inf"):
            with self.assertRaises(compose.ShimError, msg=value) as ctx:
                compose._extract_wait_timeout(["--wait-timeout", value])
            self.assertEqual(ctx.exception.code, 64)


class ContainerStateTests(unittest.TestCase):
    def _inspect(self, state):
        import json
        from types import SimpleNamespace

        payload = json.dumps([{"status": {"state": state}}])
        return SimpleNamespace(returncode=0, stdout=payload, stderr="")

    def test_maps_runtime_states(self) -> None:
        from unittest import mock

        for runtime_state, expected in (
            ("running", "running"),
            ("stopped", "exited"),
            ("exited", "exited"),
            ("stopping", "starting"),
        ):
            with mock.patch.object(
                compose, "_run_container_capture", return_value=self._inspect(runtime_state)
            ):
                self.assertEqual(compose._container_state("c"), expected, runtime_state)

    def test_missing_container_counts_as_exited(self) -> None:
        from types import SimpleNamespace
        from unittest import mock

        gone = SimpleNamespace(returncode=1, stdout="", stderr="no such container")
        with mock.patch.object(compose, "_run_container_capture", return_value=gone):
            self.assertEqual(compose._container_state("c"), "exited")

    def test_wait_started_polls_a_starting_container_until_running(self) -> None:
        from unittest import mock

        states = iter(["starting", "starting", "running"])
        service = compose.Service("web", {"image": "nginx"})
        project = compose.Project("demo", {"services": {"web": {"image": "nginx"}}}, "/tmp")
        with mock.patch.object(compose, "_container_state", side_effect=lambda name: next(states)), \
                mock.patch.object(compose.time, "sleep"):
            rc = compose._wait_started_services(project, [service], set(), set(), 5.0)
        self.assertEqual(rc, 0)


class HostsRewriteScriptTests(unittest.TestCase):
    """The generated hosts rewrite script: idempotent and self-healing.

    Each test runs the script with ``sh -c`` against a temporary file
    (explicit ``path=``) — the real ``/etc/hosts`` is never touched.
    """

    LINES = [
        "192.168.65.3 db",
        "192.168.65.1 host.docker.internal gateway.docker.internal",
    ]
    MANAGED = ["api", "db", "host.docker.internal", "gateway.docker.internal"]

    def _run(self, path: str, lines: list[str]) -> None:
        script = compose._hosts_rewrite_script(lines, self.MANAGED, path=path)
        subprocess.run(["sh", "-c", script], check=True, capture_output=True, text=True)

    def _write(self, path: str, content: str) -> None:
        with open(path, "w") as f:
            f.write(content)

    def _read(self, path: str) -> str:
        with open(path) as f:
            return f.read()

    def test_run_appends_fresh_lines_and_keeps_unmanaged_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hosts")
            self._write(path, "127.0.0.1 localhost\n")
            self._run(path, self.LINES)
            content = self._read(path)
            self.assertIn("127.0.0.1 localhost", content)
            self.assertEqual([l for l in content.splitlines() if "db" in l], self.LINES[:1])
            self.assertEqual(
                [l for l in content.splitlines() if "host.docker.internal" in l], self.LINES[1:]
            )

    def test_running_twice_leaves_file_byte_identical(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hosts")
            self._write(path, "127.0.0.1 localhost\n")
            self._run(path, self.LINES)
            first = self._read(path)
            self._run(path, self.LINES)
            self.assertEqual(self._read(path), first)

    def test_changed_address_replaces_stale_line(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hosts")
            self._write(path, "127.0.0.1 localhost\n")
            self._run(path, self.LINES)
            self._run(
                path,
                [
                    "192.168.65.7 db",
                    "192.168.65.1 host.docker.internal gateway.docker.internal",
                ],
            )
            content = self._read(path)
            self.assertNotIn("192.168.65.3", content)
            self.assertIn("127.0.0.1 localhost", content)
            self.assertEqual([l for l in content.splitlines() if "db" in l], ["192.168.65.7 db"])

    def test_stale_managed_line_removed_unmanaged_line_kept(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hosts")
            self._write(path, "127.0.0.1 localhost\n192.168.65.9 db\n10.0.0.5 other\n")
            self._run(path, self.LINES)
            content = self._read(path)
            self.assertNotIn("192.168.65.9", content)
            self.assertIn("10.0.0.5 other", content)
            self.assertEqual([l for l in content.splitlines() if "db" in l], self.LINES[:1])

    def test_all_lines_managed_still_succeeds_and_writes_fresh_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "hosts")
            self._write(
                path,
                "192.168.65.3 db\n192.168.65.1 host.docker.internal gateway.docker.internal\n"
                "10.1.1.1 api\n",
            )
            # grep -v exits 1 when everything is stripped; the script must still succeed.
            self._run(path, self.LINES)
            self.assertEqual(
                self._read(path),
                "192.168.65.3 db\n192.168.65.1 host.docker.internal gateway.docker.internal\n",
            )


class BuildPlatformTests(unittest.TestCase):
    """`_run_build` forwards the service or build platform to `container build`."""

    def build_args(self, body: str) -> list:
        proj = compose.Project("p", compose.parse_yaml(textwrap.dedent(body)), "/tmp/p")
        captured: list = []
        original = compose._run_container_passthrough
        compose._run_container_passthrough = lambda args: captured.append(args) or 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                compose._run_build(proj.services["web"], proj, "p-web:latest")
        finally:
            compose._run_container_passthrough = original
        return captured[0]

    def test_service_platform_reaches_the_build(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build: .
                platform: linux/amd64
            """
        )
        self.assertEqual(args[args.index("--platform") + 1], "linux/amd64")

    def test_build_platforms_list_is_joined_and_wins(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                platform: linux/arm64
                build:
                  context: .
                  platforms:
                    - linux/amd64
                    - linux/arm64
            """
        )
        self.assertEqual(args[args.index("--platform") + 1], "linux/amd64,linux/arm64")

    def test_no_platform_flag_without_a_platform(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build: .
            """
        )
        self.assertNotIn("--platform", args)


class BuildKeyForwardingTests(unittest.TestCase):
    """`_run_build` forwards every classified build key through the docker build translator."""

    build_args = BuildPlatformTests.build_args

    def test_target_reaches_the_build(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  target: runtime
            """
        )
        self.assertEqual(args[args.index("--target") + 1], "runtime")

    def test_labels_map_from_a_mapping_and_a_list(self) -> None:
        mapping = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  labels:
                    team: core
            """
        )
        listed = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  labels:
                    - team=core
            """
        )
        self.assertEqual(mapping[mapping.index("--label") + 1], "team=core")
        self.assertEqual(listed[listed.index("--label") + 1], "team=core")

    def test_no_cache_and_pull_map_to_flags(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  no_cache: true
                  pull: true
            """
        )
        self.assertIn("--no-cache", args)
        self.assertIn("--pull", args)

    def test_false_no_cache_and_pull_add_no_flag(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  no_cache: false
                  pull: false
            """
        )
        self.assertNotIn("--no-cache", args)
        self.assertNotIn("--pull", args)

    def test_file_secret_becomes_a_secret_argument_without_reading_the_file(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  secrets:
                    - npmrc
            secrets:
              npmrc:
                file: ./npmrc.txt
            """
        )
        self.assertEqual(args[args.index("--secret") + 1], "id=npmrc,src=/tmp/p/npmrc.txt")

    def test_environment_secret_becomes_an_env_secret(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  secrets:
                    - source: token
            secrets:
              token:
                environment: API_TOKEN
            """
        )
        self.assertEqual(args[args.index("--secret") + 1], "id=token,env=API_TOKEN")

    def test_undefined_build_secret_exits_64(self) -> None:
        with self.assertRaises(compose.ShimError) as caught:
            self.build_args(
                """
                services:
                  web:
                    build:
                      context: .
                      secrets:
                        - missing
                """
            )
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("missing", str(caught.exception))

    def test_target_renames_the_secret_id(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  secrets:
                    - source: npmrc
                      target: registry
            secrets:
              npmrc:
                file: ./npmrc.txt
            """
        )
        self.assertEqual(args[args.index("--secret") + 1], "id=registry,src=/tmp/p/npmrc.txt")

    def test_dollar_in_a_secret_path_is_kept_single(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  secrets: [npmrc]
            secrets:
              npmrc:
                file: /tmp/a$b.txt
            """
        )
        self.assertEqual(args[args.index("--secret") + 1], "id=npmrc,src=/tmp/a$b.txt")

    def test_ignored_attributes_print_a_not_applied_notice(self) -> None:
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            args = self.build_args(
                """
                services:
                  web:
                    build:
                      context: .
                      secrets:
                        - source: npmrc
                          uid: "1000"
                          mode: 0400
                secrets:
                  npmrc:
                    file: ./npmrc.txt
                """
            )
        self.assertEqual(args[args.index("--secret") + 1], "id=npmrc,src=/tmp/p/npmrc.txt")
        self.assertIn("NOT APPLIED", err.getvalue())
        self.assertIn("uid", err.getvalue())
        self.assertIn("mode", err.getvalue())

    def test_normalizer_shaped_model_gives_the_same_arguments(self) -> None:
        fixture = (
            Path(__file__).resolve().parent
            / "fixtures" / "compose" / "normalized" / "build-secrets.json"
        )
        raw = json.loads(fixture.read_text(encoding="utf-8").replace("__PROJECT_DIR__", "/project"))
        model = compose_normalizer.convert_model(raw, "5.6.0")
        proj = compose.Project("bsec", model, "/project")
        self.assertEqual(sorted(proj.secrets), ["api_key", "npm_token"])
        captured: list = []
        original = compose._run_container_passthrough
        compose._run_container_passthrough = lambda args: captured.append(args) or 0
        try:
            with contextlib.redirect_stdout(io.StringIO()):
                compose._run_build(proj.services["api"], proj, "bsec-api:latest")
        finally:
            compose._run_container_passthrough = original
        args = captured[0]
        pairs = [args[i + 1] for i, arg in enumerate(args) if arg == "--secret"]
        self.assertEqual(
            pairs, ["id=npm_token,src=/project/npm-token.txt", "id=registry,env=API_KEY"]
        )

    def test_ssh_default_is_forwarded(self) -> None:
        args = self.build_args(
            """
            services:
              web:
                build:
                  context: .
                  ssh:
                    - default
            """
        )
        self.assertEqual(args[args.index("--ssh") + 1], "default")

    def test_unclassified_cache_from_is_refused_by_the_gate(self) -> None:
        doc = compose.parse_yaml(
            textwrap.dedent(
                """
                services:
                  web:
                    build:
                      context: .
                      cache_from:
                        - img:cache
                """
            )
        )
        proj = compose.Project("p", doc, "/tmp/p")
        report = compose_capabilities.validate(proj.doc, proj.services.values(), {})
        self.assertEqual(
            [(f.service, f.key) for f in report.refusals], [("web", "build.cache_from")]
        )


class VolumeMountTranslationTests(unittest.TestCase):
    """Short and long volume syntax translate to one `--mount` form with `readonly`."""

    def translate(self, entry) -> list:
        proj = compose.Project("p", {"services": {}}, "/tmp/p")
        original = compose._ensure_named_volume
        compose._ensure_named_volume = lambda *args, **kwargs: None
        try:
            return compose._classify_and_translate_volume(entry, proj, set())
        finally:
            compose._ensure_named_volume = original

    def test_translation_table(self) -> None:
        cases = [
            ("data:/d", ["--mount", "type=volume,source=p_data,target=/d"]),
            ("data:/d:ro", ["--mount", "type=volume,source=p_data,target=/d,readonly"]),
            ("data:/d:rw", ["--mount", "type=volume,source=p_data,target=/d"]),
            ("data:/d:cached", ["--mount", "type=volume,source=p_data,target=/d"]),
            ("data:/d:delegated", ["--mount", "type=volume,source=p_data,target=/d"]),
            ("/abs:/d", ["--mount", "type=bind,source=/abs,target=/d"]),
            ("/abs:/d:ro", ["--mount", "type=bind,source=/abs,target=/d,readonly"]),
            ("./rel:/d", ["--mount", "type=bind,source=/tmp/p/rel,target=/d"]),
            (
                {"type": "volume", "source": "data", "target": "/d", "read_only": True},
                ["--mount", "type=volume,source=p_data,target=/d,readonly"],
            ),
            (
                {"type": "volume", "source": "data", "target": "/d"},
                ["--mount", "type=volume,source=p_data,target=/d"],
            ),
            (
                {"type": "bind", "source": "/abs", "target": "/d", "read_only": True},
                ["--mount", "type=bind,source=/abs,target=/d,readonly"],
            ),
        ]
        for entry, expected in cases:
            with self.subTest(entry=entry):
                self.assertEqual(self.translate(entry), expected)

    def test_unsupported_option_exits_64_naming_the_target(self) -> None:
        with self.assertRaises(compose.ShimError) as caught:
            self.translate("data:/d:nocopy")
        self.assertEqual(caught.exception.code, 64)
        self.assertIn("nocopy", str(caught.exception))
        self.assertIn("/d", str(caught.exception))

    def test_unsupported_option_on_a_bind_exits_64(self) -> None:
        with self.assertRaises(compose.ShimError) as caught:
            self.translate("/abs:/d:nocopy")
        self.assertEqual(caught.exception.code, 64)

    def test_bind_noop_modes_are_dropped(self) -> None:
        self.assertEqual(
            self.translate("/abs:/d:rw,cached"), ["--mount", "type=bind,source=/abs,target=/d"]
        )

    def test_selinux_relabeling_is_refused_in_every_shape(self) -> None:
        refused = [
            "data:/d:z",
            "data:/d:Z",
            "data:/d:ro,z",
            "/abs:/d:Z",
            "./rel:/d:Z,ro",
            {"type": "bind", "source": "/abs", "target": "/d", "bind": {"selinux": "z"}},
            # The shape the official normalizer gives a named volume with `:Z`.
            {"type": "volume", "source": "data", "target": "/d", "volume": {}, "bind": {"selinux": "Z"}},
        ]
        for entry in refused:
            with self.subTest(entry=entry):
                with self.assertRaises(compose.ShimError) as caught:
                    self.translate(entry)
                self.assertEqual(caught.exception.code, 64)
                self.assertIn("SELinux", str(caught.exception))
                self.assertIn("/d", str(caught.exception))

    def test_long_syntax_options_match_the_short_ones(self) -> None:
        long_ro = {"type": "bind", "source": "/abs", "target": "/d", "read_only": True}
        self.assertEqual(self.translate(long_ro), self.translate("/abs:/d:ro"))
        with_noops = {
            "type": "volume", "source": "data", "target": "/d", "consistency": "cached",
            "bind": {"create_host_path": True}, "volume": {},
        }
        self.assertEqual(self.translate(with_noops), self.translate("data:/d"))

    def test_service_mounts_is_pure(self) -> None:
        proj = compose.Project("p", {"services": {}}, "/tmp/p")
        service = compose.Service("web", {"volumes": ["data:/d:ro", "./rel:/r", "/abs:/a"]})
        original = compose._ensure_named_volume

        def forbidden(*args, **kwargs):
            raise AssertionError("_service_mounts must not create volumes")

        compose._ensure_named_volume = forbidden
        try:
            planned = compose._service_mounts(service, proj)
        finally:
            compose._ensure_named_volume = original
        self.assertEqual(
            [(m.mount.kind, m.mount.source, m.mount.readonly, m.volume_key) for m in planned],
            [
                ("volume", "p_data", True, "data"),
                ("bind", "/tmp/p/rel", False, None),
                ("bind", "/abs", False, None),
            ],
        )

    def test_service_volume_keys_lists_only_named_volumes(self) -> None:
        service = compose.Service(
            "web",
            {"volumes": ["data:/d", "./rel:/r", {"type": "bind", "source": "/abs", "target": "/a"},
                         {"type": "volume", "source": "other", "target": "/o"}]},
        )
        self.assertEqual(compose._service_volume_keys(service), ["data", "other"])


class ResourceProvisioningTests(unittest.TestCase):
    """Top-level `internal` and `labels` reach `network create` and `volume create`."""

    def capture(self, call):
        calls: list = []

        class Result:
            returncode = 0
            stdout = ""
            stderr = ""

        def fake(args):
            calls.append(args)
            result = Result()
            result.returncode = 1 if args[1] == "inspect" else 0
            return result

        original = compose._run_container_capture
        compose._run_container_capture = fake
        try:
            call()
        finally:
            compose._run_container_capture = original
        return [args for args in calls if args[1] == "create"]

    def test_internal_network_with_labels(self) -> None:
        proj = compose.Project(
            "p",
            {"services": {}, "networks": {"private": {"internal": True, "labels": {"tier": "db"}}}},
            "/tmp/p",
        )
        created = self.capture(lambda: compose._ensure_network(proj, "private"))
        args = created[0]
        self.assertIn("--internal", args)
        self.assertIn("tier=db", args)
        self.assertIn("com.docker.compose.network=private", args)
        self.assertEqual(args[-1], "p_private")

    def test_external_network_is_never_created(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "networks": {"edge": {"external": True}}}, "/tmp/p"
        )
        self.assertEqual(self.capture(lambda: compose._ensure_network(proj, "edge")), [])

    def test_explicit_network_name_is_the_created_name(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "networks": {"back": {"name": "shared-back"}}}, "/tmp/p"
        )
        created = self.capture(lambda: compose._ensure_network(proj, "back"))
        self.assertEqual(created[0][-1], "shared-back")

    def test_volume_labels_are_forwarded(self) -> None:
        created = self.capture(
            lambda: compose._ensure_named_volume("p_data", "p", {"tier": "db"})
        )
        self.assertIn("tier=db", created[0])
        self.assertEqual(created[0][-1], "p_data")

    def test_volume_labels_come_from_the_project_declaration(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "volumes": {"data": {"labels": ["tier=db"]}}}, "/tmp/p"
        )
        created = self.capture(
            lambda: compose._classify_and_translate_volume("data:/d", proj, set())
        )
        self.assertIn("tier=db", created[0])
        self.assertIn("com.docker.compose.volume=data", created[0])

    def test_external_volume_is_mounted_by_its_own_name_and_never_created(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "volumes": {"data": {"external": True, "name": "shared_data"}}},
            "/tmp/p",
        )
        out: list = []
        created = self.capture(
            lambda: out.append(compose._classify_and_translate_volume("data:/d", proj, set()))
        )
        self.assertEqual(created, [])
        self.assertEqual(out[0], ["--mount", "type=volume,source=shared_data,target=/d"])

    def test_external_volume_without_name_keeps_the_key(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "volumes": {"shared": {"external": True}}}, "/tmp/p"
        )
        out: list = []
        created = self.capture(
            lambda: out.append(compose._classify_and_translate_volume("shared:/s", proj, set()))
        )
        self.assertEqual(created, [])
        self.assertEqual(out[0], ["--mount", "type=volume,source=shared,target=/s"])

    def test_explicit_volume_name_replaces_the_project_prefix(self) -> None:
        proj = compose.Project(
            "p", {"services": {}, "volumes": {"data": {"name": "fixed"}}}, "/tmp/p"
        )
        created = self.capture(
            lambda: compose._classify_and_translate_volume("data:/d", proj, set())
        )
        self.assertEqual(created[0][-1], "fixed")

    def test_service_networks_use_resolved_runtime_names(self) -> None:
        proj = compose.Project(
            "p",
            {
                "services": {"web": {"image": "x", "networks": ["edge", "back"]}},
                "networks": {"edge": {"external": True}, "back": {}},
            },
            "/tmp/p",
        )
        self.assertEqual(
            compose._service_networks(proj.services["web"], proj), ["edge", "p_back"]
        )

    def test_unlabelled_volume_gets_only_the_project_label(self) -> None:
        created = self.capture(lambda: compose._ensure_named_volume("p_data", "p"))
        labels = [created[0][i + 1] for i, a in enumerate(created[0]) if a == "--label"]
        self.assertEqual(len(labels), 1)


class InactiveProfileHintTests(unittest.TestCase):
    ERROR = 'compose: service "api" depends on undefined service "db": invalid compose project'

    def hint(self, services):
        original = compose.compose_normalizer.load_normalized
        compose.compose_normalizer.load_normalized = lambda *a, **k: ({"services": services}, "")
        try:
            return compose._inactive_profile_hint(
                "/bin/true", compose.compose_normalizer.Inputs(), self.ERROR
            )
        finally:
            compose.compose_normalizer.load_normalized = original

    def test_names_the_dependency_and_its_profile(self) -> None:
        text = self.hint({"api": {}, "db": {"profiles": ["debug"]}})
        self.assertIn('"db"', text)
        self.assertIn("profile debug", text)
        self.assertIn("--profile debug", text)

    def test_a_truly_undefined_dependency_keeps_the_original_message(self) -> None:
        self.assertIsNone(self.hint({"api": {}}))

    def test_other_errors_are_left_alone(self) -> None:
        self.assertIsNone(
            compose._inactive_profile_hint(
                "/bin/true", compose.compose_normalizer.Inputs(), "compose: boom"
            )
        )


class CheckResourcesRuntimeErrorTests(unittest.TestCase):
    def test_a_failing_list_aborts_before_any_creation(self) -> None:
        calls: list = []

        class Result:
            returncode = 3
            stdout = ""
            stderr = "runtime unavailable"

        def fake(args):
            calls.append(args)
            return Result()

        proj = compose.Project(
            "p", {"services": {"db": {"image": "x", "volumes": ["data:/d"]}}}, "/tmp/p"
        )
        original = compose._run_container_capture
        compose._run_container_capture = fake
        try:
            with self.assertRaises(compose.ShimError) as caught:
                compose._check_resources(proj, proj.topo_sorted())
        finally:
            compose._run_container_capture = original
        self.assertEqual(caught.exception.code, 3)
        self.assertIn("runtime unavailable", str(caught.exception))
        self.assertTrue(all(a[1] == "list" for a in calls))


class DependsRequiredParsingTests(unittest.TestCase):
    """`depends_on.required` is parsed into `Service.depends_required`."""

    def parse(self, depends_on) -> dict:
        service = compose.Service("api", {"image": "x", "depends_on": depends_on})
        return service.depends_required

    def test_list_form_is_required(self) -> None:
        self.assertEqual(self.parse(["db", "cache"]), {"db": True, "cache": True})

    def test_string_form_is_required(self) -> None:
        self.assertEqual(self.parse("db"), {"db": True})

    def test_map_form_defaults_to_required(self) -> None:
        self.assertEqual(self.parse({"db": {"condition": "service_healthy"}}), {"db": True})

    def test_required_false_is_optional(self) -> None:
        parsed = self.parse({"db": {"required": False}, "cache": {"required": True}})
        self.assertEqual(parsed, {"db": False, "cache": True})

    def test_required_false_as_text_is_optional(self) -> None:
        self.assertEqual(self.parse({"db": {"required": "false"}}), {"db": False})

    def test_invalid_required_exits_64(self) -> None:
        with self.assertRaises(compose.ShimError) as caught:
            self.parse({"db": {"required": "maybe"}})
        self.assertEqual(caught.exception.code, 64)

    def test_conditions_are_unchanged(self) -> None:
        service = compose.Service(
            "api",
            {"image": "x", "depends_on": {"db": {"condition": "service_healthy", "required": False}}},
        )
        self.assertEqual(service.depends_conditions, {"db": "service_healthy"})
        self.assertEqual(service.depends_on, ["db"])

    def test_no_depends_on(self) -> None:
        self.assertEqual(self.parse(None), {})


class AnonymousVolumeNamingTests(unittest.TestCase):
    """A new anonymous volume never takes the name of an existing volume (design D1)."""

    def run_with(self, existing: set, tokens: list):
        from unittest import mock

        calls: list = []

        def runtime(args: list) -> subprocess.CompletedProcess:
            calls.append(args)
            if args[:2] == ["volume", "inspect"]:
                return subprocess.CompletedProcess(args, 0 if args[-1] in existing else 1, "", "")
            return subprocess.CompletedProcess(args, 0, "", "")

        volume = compose._AnonymousVolume("web", "/data")
        with mock.patch.object(compose, "_run_container_capture", side_effect=runtime), \
                mock.patch.object(compose_resources.secrets, "token_hex", side_effect=tokens):
            try:
                name = compose._create_anonymous_volume(volume, "demo", "node:20")
            except compose.ShimError as exc:
                return exc, calls
        return name, calls

    def test_a_free_name_is_created_and_seeded(self) -> None:
        name, calls = self.run_with(set(), ["aaaaaaaaaa"])
        self.assertEqual(name, "demo_web_anon-aaaaaaaaaa")
        self.assertEqual([c[:2] for c in calls if c[:2] == ["volume", "create"]], [["volume", "create"]])
        self.assertEqual([c for c in calls if c[:2] == ["volume", "rm"]], [])

    def test_a_taken_name_is_skipped_and_the_existing_volume_is_left_alone(self) -> None:
        name, calls = self.run_with({"demo_web_anon-aaaaaaaaaa"}, ["aaaaaaaaaa", "bbbbbbbbbb"])
        self.assertEqual(name, "demo_web_anon-bbbbbbbbbb")
        touched = [c[-1] for c in calls if c[:2] in (["volume", "create"], ["volume", "rm"])]
        self.assertEqual(touched, ["demo_web_anon-bbbbbbbbbb"])

    def test_three_taken_names_fail_without_creating_anything(self) -> None:
        taken = {f"demo_web_anon-{c * 10}" for c in "abc"}
        error, calls = self.run_with(taken, ["a" * 10, "b" * 10, "c" * 10])
        self.assertIsInstance(error, compose.ShimError)
        self.assertEqual(error.code, 1)
        self.assertIn("/data", str(error))
        self.assertEqual([c for c in calls if c[:2] != ["volume", "inspect"]], [])


if __name__ == "__main__":
    unittest.main()
