#!/usr/bin/env python3
"""Mirror of the official MCP Registry (registry.modelcontextprotocol.io) into mcp/.

Frey and PiEv read the mirror's raw addresses, never the registry itself (AD108): its list API is for
mirrors, not for every client. The mirror holds the latest version of every server that is not deleted:

    mcp/servers.json          the manifest: when, how many, and the 16 shard files with their sha256
    mcp/servers/<0-f>.json    the servers whose sha256(name) starts with that hex digit, one per line,
                              sorted by name: {"server": {name, title, description, version, websiteUrl,
                              repository, icons, packages, remotes}, "status", "updated_at"}

(16 files instead of one: the registry had 51,410 servers on 2026-10-28, 32 MB as one file, and grows
by a thousand a day — GitHub warns at 50 MB a file. One server per line keeps a day's diff to the lines
that changed.)

    python3 scripts/sync_mcp.py            # incremental: what changed since the last run
    python3 scripts/sync_mcp.py --full     # everything again (the first run, or to start over)

Incremental: GET /v0.1/servers?updated_since=<the last run's start> (the registry then includes deleted
versions). A record that is its server's latest version and not deleted replaces the entry; a server with
a deleted version is asked again (GET /v0.1/servers/<name>/versions/latest): still there, its latest
entry; gone (404 or deleted), it leaves the mirror. Older versions changing touch nothing.

Every record is checked before it goes in: the fields clients read, and — when the jsonschema package is
installed (the workflow installs it) — the official server.schema.json its "$schema" names. A record that
fails is left out and named on stderr; the run does not fail for it. Files are written only when the
servers changed (a day without changes is no commit); "synced_at" is the start of that run.

Only the standard library besides the optional jsonschema. Exit code 0, or 1 when the registry could not
be read (nothing is written then).
"""
import argparse
import datetime
import hashlib
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request

REGISTRY = "https://registry.modelcontextprotocol.io"
OFFICIAL = "io.modelcontextprotocol.registry/official"
PAGE = 100
SHARDS = "0123456789abcdef"
KEEP = ("name", "title", "description", "version", "websiteUrl", "repository", "icons", "packages", "remotes")
USER_AGENT = "agent-catalog-sync (+https://github.com/lannerk/agent-catalog)"


def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            return r.status, json.loads(r.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        return e.code, None


class Registry:
    """The registry's two reads. `opener` is replaced in the tests by recorded answers."""

    def __init__(self, base=REGISTRY, opener=None):
        self.base = base.rstrip("/")
        self.opener = opener or http_get

    def pages(self, **query):
        """Every record of /v0.1/servers with these filters, following nextCursor."""
        cursor = None
        while True:
            q = dict(query, limit=PAGE)
            if cursor:
                q["cursor"] = cursor
            url = f"{self.base}/v0.1/servers?{urllib.parse.urlencode(q)}"
            status, body = self.opener(url)
            if status != 200 or not isinstance(body, dict):
                raise RuntimeError(f"{url}: HTTP {status}")
            yield from body.get("servers") or []
            cursor = (body.get("metadata") or {}).get("nextCursor")
            if not cursor:
                return

    def latest(self, name):
        """The latest version of `name`, or None when it is gone."""
        url = f"{self.base}/v0.1/servers/{urllib.parse.quote(name, safe='')}/versions/latest"
        status, body = self.opener(url)
        if status == 404:
            return None
        if status != 200 or not isinstance(body, dict):
            raise RuntimeError(f"{url}: HTTP {status}")
        return body


def meta(record):
    return (record.get("_meta") or {}).get(OFFICIAL) or {}


def problems(record, validator=None):
    """What is wrong with a registry record for the mirror (empty: it goes in)."""
    server = record.get("server")
    if not isinstance(server, dict):
        return ["no server object"]
    wrong = [f"server.{f} missing" for f in ("name", "description", "version")
             if not isinstance(server.get(f), str) or not server[f]]
    wrong += [f"server.{f} is not a list" for f in ("packages", "remotes")
              if f in server and not isinstance(server[f], list)]
    if meta(record).get("status") == "deleted":
        wrong.append("deleted")
    if validator is not None and not wrong:
        wrong += validator(server)
    return wrong


def entry(record):
    """A registry record as the mirror keeps it."""
    m = meta(record)
    return {"server": {k: record["server"][k] for k in KEEP if k in record["server"]},
            "status": m.get("status", "active"), "updated_at": m.get("updatedAt")}


def schema_validator(cache_dir, fetch=http_get):
    """Checks a server against the official schema its "$schema" names (read once per run, kept in
    `cache_dir`). None when the jsonschema package is not installed."""
    try:
        import jsonschema
    except ImportError:
        return None
    loaded = {}

    def get(url):
        if url not in loaded:
            path = os.path.join(cache_dir, urllib.parse.quote(url, safe=""))
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    loaded[url] = json.load(f)
            else:
                status, body = fetch(url)
                loaded[url] = body if status == 200 else None
                if loaded[url] is not None:
                    os.makedirs(cache_dir, exist_ok=True)
                    with open(path, "w", encoding="utf-8") as f:
                        json.dump(body, f)
        return loaded[url]

    def check(server):
        url = server.get("$schema")
        if not isinstance(url, str) or not url.startswith("https://static.modelcontextprotocol.io/"):
            return [f"$schema {url!r} is not an official server schema"]
        schema = get(url.split("#")[0])
        if schema is None:
            return [f"$schema {url} could not be read"]
        cls = jsonschema.validators.validator_for(schema)
        return [f"schema: {e.message} at {'/'.join(map(str, e.absolute_path)) or '(root)'}"
                for e in cls(schema).iter_errors(server)][:3]

    return check


def shard_of(name):
    return hashlib.sha256(name.encode("utf-8")).hexdigest()[0]


def now():
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def sync(registry, servers, since, validator=None, log=lambda line: print(line, file=sys.stderr)):
    """Brings `servers` (name → entry; changed in place) up to date: everything when `since` is None,
    else what changed since then. Returns how many records were left out."""
    rejected = 0

    def take(record):
        nonlocal rejected
        wrong = problems(record, validator)
        name = (record.get("server") or {}).get("name", "?")
        if wrong:
            if wrong != ["deleted"]:
                rejected += 1
                log(f"left out {name}: {'; '.join(wrong)}")
            servers.pop(name, None)
        else:
            servers[name] = entry(record)

    if since is None:
        servers.clear()
        for record in registry.pages(version="latest"):
            take(record)
        return rejected
    again = set()
    for record in registry.pages(updated_since=since):
        name = (record.get("server") or {}).get("name")
        if not isinstance(name, str):
            continue
        if meta(record).get("status") == "deleted":
            again.add(name)
        elif meta(record).get("isLatest"):
            take(record)
    for name in sorted(again):
        latest = registry.latest(name)
        if latest is None:
            servers.pop(name, None)
        else:
            take(latest)
    return rejected


def shard_text(digit, servers):
    """One shard file: valid JSON, one server per line."""
    names = sorted(n for n in servers if shard_of(n) == digit)
    lines = [json.dumps(servers[n], ensure_ascii=False, separators=(",", ":"), sort_keys=True) for n in names]
    body = ",\n".join(lines)
    return f'{{"schema":1,"shard":"{digit}","servers":[\n{body}\n]}}\n' if lines else \
        f'{{"schema":1,"shard":"{digit}","servers":[]}}\n'


def read_mirror(mcp_dir):
    """(manifest, servers) as on disk; (None, {}) when there is no mirror yet."""
    path = os.path.join(mcp_dir, "servers.json")
    if not os.path.exists(path):
        return None, {}
    with open(path, encoding="utf-8") as f:
        manifest = json.load(f)
    servers = {}
    for digit in SHARDS:
        with open(os.path.join(mcp_dir, "servers", f"{digit}.json"), encoding="utf-8") as f:
            for e in json.load(f)["servers"]:
                servers[e["server"]["name"]] = e
    return manifest, servers


def write_mirror(mcp_dir, servers, source, synced_at):
    """Writes the shards that differ and the manifest. Returns the files written."""
    os.makedirs(os.path.join(mcp_dir, "servers"), exist_ok=True)
    written, shards = [], {}
    for digit in SHARDS:
        text = shard_text(digit, servers)
        path = os.path.join(mcp_dir, "servers", f"{digit}.json")
        old = open(path, encoding="utf-8").read() if os.path.exists(path) else None
        if text != old:
            with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
                f.write(text)
            os.replace(path + ".tmp", path)
            written.append(path)
        shards[digit] = {"count": sum(1 for n in servers if shard_of(n) == digit),
                         "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest()}
    manifest = {"schema": 1, "source": source, "synced_at": synced_at, "count": len(servers),
                "shards": shards}
    path = os.path.join(mcp_dir, "servers.json")
    with open(path + ".tmp", "w", encoding="utf-8", newline="\n") as f:
        json.dump(manifest, f, indent=1)
        f.write("\n")
    os.replace(path + ".tmp", path)
    return written + [path]


def main(argv=None):
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--full", action="store_true", help="start from nothing")
    ap.add_argument("--registry", default=REGISTRY)
    ap.add_argument("--mcp", default=os.path.join(root, "mcp"), help="the mirror's directory")
    ap.add_argument("--schemas", default=os.path.join(root, ".cache", "schemas"),
                    help="where the official schemas are kept between runs (not committed)")
    a = ap.parse_args(argv)
    started = now()
    manifest, servers = (None, {}) if a.full else read_mirror(a.mcp)
    before = dict(servers)
    registry = Registry(a.registry)
    try:
        rejected = sync(registry, servers, manifest and manifest["synced_at"], schema_validator(a.schemas))
    except (RuntimeError, OSError, ValueError) as e:
        print(f"sync_mcp: {e}", file=sys.stderr)
        return 1
    if rejected:
        print(f"sync_mcp: {rejected} record(s) left out (see above)", file=sys.stderr)
    if manifest is not None and servers == before:
        print(f"sync_mcp: no change ({len(servers)} servers, synced at {manifest['synced_at']})")
        return 0
    written = write_mirror(a.mcp, servers, registry.base + "/v0.1/servers", started)
    print(f"sync_mcp: {len(servers)} servers; {len(written)} file(s) written")
    return 0


if __name__ == "__main__":
    sys.exit(main())
