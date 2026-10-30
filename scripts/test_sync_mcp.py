#!/usr/bin/env python3
"""T-0068 ①②: the mirror from recorded registry answers — a full run over two pages, then an incremental
run with a new server, a new version, an older version changed, a server deleted and a version deleted;
nothing written when nothing changed; a registry error writes nothing. The records' shape is the
registry's own (recorded from registry.modelcontextprotocol.io on 2026-10-28, names changed).

    python3 scripts/test_sync_mcp.py
"""
import copy
import json
import os
import shutil
import sys
import tempfile
import unittest
import urllib.parse

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import sync_mcp as s  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCHEMA = "https://static.modelcontextprotocol.io/schemas/2025-12-11/server.schema.json"


def record(name, version, latest=True, status="active", updated="2026-10-28T01:00:00Z", **server):
    server = {"$schema": SCHEMA, "name": name, "description": f"The {name} server.", "version": version,
              **server}
    return {"server": server, "_meta": {s.OFFICIAL: {
        "status": status, "statusChangedAt": updated, "publishedAt": updated, "updatedAt": updated,
        "isLatest": latest}}}


NPM = {"packages": [{"registryType": "npm", "identifier": "@acme/mcp", "version": "1.0.0",
                     "transport": {"type": "stdio"}}]}
REMOTE = {"remotes": [{"type": "streamable-http", "url": "https://mcp.example.com/mcp"}]}


class Recorded:
    """The registry's answers by URL; every URL asked for is kept."""

    def __init__(self, answers):
        self.answers, self.asked = answers, []

    def __call__(self, url):
        self.asked.append(url)
        return self.answers.get(url, (404, None))


def page(records, cursor=None):
    return 200, {"servers": records, "metadata": {"count": len(records), **({"nextCursor": cursor} if cursor else {})}}


def url(query):
    """The address `Registry.pages` asks for: the filters, the page size, then the cursor."""
    cursor = query.pop("cursor", None)
    q = dict(query, limit=s.PAGE)
    if cursor:
        q["cursor"] = cursor
    return f"{s.REGISTRY}/v0.1/servers?{urllib.parse.urlencode(q)}"


def latest_url(name):
    return f"{s.REGISTRY}/v0.1/servers/{urllib.parse.quote(name, safe='')}/versions/latest"


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.dir = tempfile.mkdtemp(prefix="agent-catalog-")
        self.mcp = os.path.join(self.dir, "mcp")
        self.lines = []

    def tearDown(self):
        shutil.rmtree(self.dir, ignore_errors=True)

    def run_sync(self, answers, since):
        manifest, servers = s.read_mirror(self.mcp)
        before = dict(servers)
        registry = s.Registry(opener=Recorded(answers))
        rejected = s.sync(registry, servers, since, log=self.lines.append)
        if manifest is None or servers != before:
            s.write_mirror(self.mcp, servers, registry.base + "/v0.1/servers", "2026-10-28T10:00:00Z")
        return registry.opener, rejected

    def names(self):
        return sorted(s.read_mirror(self.mcp)[1])

    def full(self):
        first = [record("io.github.acme/alpha", "1.0.0", **NPM), record("com.example/beta", "1.0.0", **REMOTE)]
        second = [record("dev.gamma/gamma", "2.0.0"), record("ai.delta/delta", "1.0.0"),
                  {"server": {"name": "bad/one"}, "_meta": {}}]
        answers = {url({"version": "latest"}): page(first, "com.example/beta:1.0.0"),
                   url({"version": "latest", "cursor": "com.example/beta:1.0.0"}): page(second)}
        return self.run_sync(answers, None)

    def test_full_over_two_pages(self):
        opener, rejected = self.full()
        self.assertEqual(len(opener.asked), 2)
        self.assertEqual(self.names(), ["ai.delta/delta", "com.example/beta", "dev.gamma/gamma",
                                        "io.github.acme/alpha"])
        self.assertEqual(rejected, 1)
        self.assertIn("left out bad/one: server.description missing; server.version missing", self.lines)
        manifest, servers = s.read_mirror(self.mcp)
        self.assertEqual(manifest["count"], 4)
        self.assertEqual(sum(v["count"] for v in manifest["shards"].values()), 4)
        self.assertEqual(set(manifest["shards"]), set(s.SHARDS))
        alpha = servers["io.github.acme/alpha"]
        self.assertEqual(alpha["server"]["packages"][0]["registryType"], "npm")
        self.assertNotIn("$schema", alpha["server"])
        self.assertEqual((alpha["status"], alpha["updated_at"]), ("active", "2026-10-28T01:00:00Z"))
        # One server per line, and the manifest's sha256 is the file's.
        for digit, info in manifest["shards"].items():
            text = open(os.path.join(self.mcp, "servers", f"{digit}.json"), encoding="utf-8").read()
            self.assertEqual(len(text.splitlines()), info["count"] + 2 if info["count"] else 1)
            import hashlib
            self.assertEqual(hashlib.sha256(text.encode()).hexdigest(), info["sha256"])

    def test_incremental_new_update_delete(self):
        self.full()
        since = "2026-10-28T10:00:00Z"
        changes = [
            record("org.new/epsilon", "0.1.0", updated="2026-10-28T11:00:00Z"),                # new
            record("com.example/beta", "1.1.0", updated="2026-10-28T11:01:00Z", **REMOTE),     # new version
            record("com.example/beta", "1.0.0", latest=False, updated="2026-10-28T11:01:00Z"),  # superseded
            record("dev.gamma/gamma", "1.0.0", latest=False, updated="2026-10-28T11:02:00Z",
                   description="an older version changed"),                                    # ignored
            record("ai.delta/delta", "1.0.0", status="deleted", updated="2026-10-28T11:03:00Z"),  # gone
            record("io.github.acme/alpha", "0.9.0", latest=False, status="deleted",
                   updated="2026-10-28T11:04:00Z"),                                            # old one deleted
        ]
        answers = {url({"updated_since": since}): page(changes),
                   latest_url("io.github.acme/alpha"): (200, record("io.github.acme/alpha", "1.0.0", **NPM))}
        opener, _ = self.run_sync(answers, since)
        self.assertEqual(self.names(), ["com.example/beta", "dev.gamma/gamma", "io.github.acme/alpha",
                                        "org.new/epsilon"])
        servers = s.read_mirror(self.mcp)[1]
        self.assertEqual(servers["com.example/beta"]["server"]["version"], "1.1.0")
        self.assertEqual(servers["dev.gamma/gamma"]["server"]["version"], "2.0.0")
        self.assertEqual(servers["io.github.acme/alpha"]["server"]["version"], "1.0.0")
        self.assertIn(latest_url("ai.delta/delta"), opener.asked, "a deleted server is asked again")
        manifest = s.read_mirror(self.mcp)[0]
        self.assertEqual(manifest["count"], 4)
        self.assertFalse(any(e["status"] == "deleted" for e in servers.values()))

    def test_deprecated_stays_marked(self):
        self.full()
        since = "2026-10-28T10:00:00Z"
        answers = {url({"updated_since": since}): page([record("dev.gamma/gamma", "2.0.0", status="deprecated")])}
        self.run_sync(answers, since)
        self.assertEqual(s.read_mirror(self.mcp)[1]["dev.gamma/gamma"]["status"], "deprecated")

    def test_nothing_changed_nothing_written(self):
        self.full()
        before = {f: os.stat(os.path.join(self.mcp, "servers", f)).st_mtime_ns
                  for f in os.listdir(os.path.join(self.mcp, "servers"))}
        manifest = open(os.path.join(self.mcp, "servers.json")).read()
        since = "2026-10-28T10:00:00Z"
        self.run_sync({url({"updated_since": since}): page([])}, since)
        self.assertEqual(open(os.path.join(self.mcp, "servers.json")).read(), manifest)
        after = {f: os.stat(os.path.join(self.mcp, "servers", f)).st_mtime_ns
                 for f in os.listdir(os.path.join(self.mcp, "servers"))}
        self.assertEqual(before, after)

    def test_a_registry_error_writes_nothing(self):
        with self.assertRaisesRegex(RuntimeError, "HTTP 503"):
            self.run_sync({url({"version": "latest"}): (503, None)}, None)
        self.assertFalse(os.path.exists(self.mcp))

    def test_the_schema_check_leaves_a_record_out(self):
        try:
            import jsonschema  # noqa: F401
        except ImportError:
            self.skipTest("jsonschema not installed")
        schema = {"type": "object", "required": ["name", "description", "version"],
                  "properties": {"packages": {"type": "array", "items": {"required": ["registryType"]}}}}
        check = s.schema_validator(os.path.join(self.dir, "cache"), fetch=lambda u: (200, schema))
        self.assertEqual(check(record("a/b", "1", **NPM)["server"]), [])
        old = record("a/c", "1", packages=[{"registry_name": "npm"}])["server"]
        self.assertTrue(check(old)[0].startswith("schema: 'registryType' is a required property"))
        self.assertIn("is not an official server schema", check({"$schema": "http://x"})[0])


class FilesTest(unittest.TestCase):
    """The mirror's files and the hand-written ones against their schemas."""

    def validate(self, schema_file, document):
        try:
            import jsonschema
        except ImportError:
            self.skipTest("jsonschema not installed")
        with open(os.path.join(ROOT, schema_file), encoding="utf-8") as f:
            schema = json.load(f)
        jsonschema.validators.validator_for(schema)(schema).validate(document)

    def test_the_committed_mirror(self):
        manifest, servers = s.read_mirror(os.path.join(ROOT, "mcp"))
        if manifest is None:
            self.skipTest("no mirror yet")
        self.validate("mcp/servers.schema.json", manifest)
        self.assertEqual(manifest["count"], len(servers))
        for digit in s.SHARDS:
            with open(os.path.join(ROOT, "mcp", "servers", f"{digit}.json"), encoding="utf-8") as f:
                shard = json.load(f)
            self.validate("mcp/shard.schema.json", shard)
        self.assertFalse([n for n, e in servers.items() if e["status"] == "deleted"])

    def test_the_hand_written_files(self):
        for path, schema in [("mcp/curated.json", "mcp/curated.schema.json"),
                             ("skills/index.json", "skills/index.schema.json")]:
            with open(os.path.join(ROOT, path), encoding="utf-8") as f:
                self.validate(schema, json.load(f))
        for market in ("frey", "piev"):
            with open(os.path.join(ROOT, market, "marketplace.json"), encoding="utf-8") as f:
                m = json.load(f)
            self.assertTrue(m["name"] and m["owner"]["name"] and isinstance(m["plugins"], list))

    def test_a_copy_round_trips(self):
        manifest, servers = s.read_mirror(os.path.join(ROOT, "mcp"))
        if manifest is None:
            self.skipTest("no mirror yet")
        tmp = tempfile.mkdtemp()
        try:
            s.write_mirror(tmp, copy.deepcopy(servers), manifest["source"], manifest["synced_at"])
            for digit in s.SHARDS:
                a = open(os.path.join(ROOT, "mcp", "servers", f"{digit}.json"), encoding="utf-8").read()
                b = open(os.path.join(tmp, "servers", f"{digit}.json"), encoding="utf-8").read()
                self.assertEqual(a, b, digit)
        finally:
            shutil.rmtree(tmp)


if __name__ == "__main__":
    unittest.main()
