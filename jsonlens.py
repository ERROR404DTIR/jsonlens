#!/usr/bin/env python3
"""jsonlens - one-file, zero-dependency JSON toolkit.

  python jsonlens.py view  data.json          pretty, colored output
  python jsonlens.py get   data.json "users[*].name"
  python jsonlens.py flat  data.json          flatten to path = value
  python jsonlens.py diff  a.json b.json      structural diff
  python jsonlens.py csv   data.json -o out.csv
  python jsonlens.py shape data.json          schema overview

Use "-" (or no file) to read from stdin. JSON Lines is detected automatically.
"""
import argparse
import csv
import json
import os
import sys

__version__ = "1.0.0"

if os.name == "nt":
    os.system("")  # enable ANSI colors on Windows 10+
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(encoding="utf-8")
    except Exception:
        pass

USE_COLOR = sys.stdout.isatty() and "NO_COLOR" not in os.environ


def c(code, text):
    return f"\033[{code}m{text}\033[0m" if USE_COLOR else str(text)


def die(msg):
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(2)


# ---------------------------------------------------------------- loading
def load(src):
    try:
        text = sys.stdin.read() if src in (None, "-") else open(src, encoding="utf-8-sig").read()
    except OSError as e:
        die(e.strerror or str(e))
    try:
        return json.loads(text)
    except json.JSONDecodeError as first:
        rows = []
        for i, line in enumerate(text.splitlines(), 1):
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                die(f"invalid JSON: {first.msg} (line {first.lineno}, col {first.colno})")
        if not rows:
            die("empty input")
        return rows


# ---------------------------------------------------------------- pretty
def scalar(v):
    if isinstance(v, str):
        return c("32", json.dumps(v, ensure_ascii=False))
    if v is None:
        return c("90", "null")
    return c("33" if isinstance(v, bool) else "35", json.dumps(v))


def pretty(v, indent=2, lvl=0):
    pad, inner = " " * (indent * lvl), " " * (indent * (lvl + 1))
    if isinstance(v, dict):
        if not v:
            return "{}"
        rows = [f"{inner}{c('36', json.dumps(k, ensure_ascii=False))}: {pretty(x, indent, lvl + 1)}"
                for k, x in v.items()]
        return "{\n" + ",\n".join(rows) + f"\n{pad}}}"
    if isinstance(v, list):
        if not v:
            return "[]"
        rows = [f"{inner}{pretty(x, indent, lvl + 1)}" for x in v]
        return "[\n" + ",\n".join(rows) + f"\n{pad}]"
    return scalar(v)


# ---------------------------------------------------------------- query
def tokens(path):
    toks, i, n = [], 0, len(path)
    while i < n:
        ch = path[i]
        if ch == ".":
            if path.startswith("..", i):
                i += 2
                j = i
                while j < n and path[j] not in ".[":
                    j += 1
                toks.append(("deep", path[i:j]))
                i = j
            else:
                i += 1
        elif ch == "[":
            j = path.find("]", i)
            if j < 0:
                die("missing ] in path")
            toks.append(("idx", path[i + 1:j].strip().strip("\"'")))
            i = j + 1
        else:
            j = i
            while j < n and path[j] not in ".[":
                j += 1
            toks.append(("key", path[i:j]))
            i = j
    return toks


def find(node, key):
    if isinstance(node, dict):
        for k, v in node.items():
            if k == key:
                yield v
            yield from find(v, key)
    elif isinstance(node, list):
        for x in node:
            yield from find(x, key)


def query(data, path):
    nodes = [data]
    for kind, t in tokens(path):
        nxt = []
        for n in nodes:
            if kind == "deep":
                nxt.extend(find(n, t))
            elif t in ("*", ""):
                if isinstance(n, dict):
                    nxt.extend(n.values())
                elif isinstance(n, list):
                    nxt.extend(n)
            elif isinstance(n, dict) and t in n:
                nxt.append(n[t])
            elif isinstance(n, list) and kind == "idx":
                try:
                    nxt.append(n[int(t)])
                except (ValueError, IndexError):
                    pass
        nodes = nxt
    return nodes


# ---------------------------------------------------------------- flatten
def flatten(v, p=""):
    if isinstance(v, dict) and v:
        for k, x in v.items():
            yield from flatten(x, f"{p}.{k}" if p else str(k))
    elif isinstance(v, list) and v:
        for i, x in enumerate(v):
            yield from flatten(x, f"{p}[{i}]")
    else:
        yield (p or "$"), v


# ---------------------------------------------------------------- shape
TYPES = {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null",
         int: "number", float: "number"}


def shape(samples, lvl, out, label):
    names = sorted({TYPES.get(type(s), "?") for s in samples})
    count = f" {c('90', f'x{len(samples)}')}" if len(samples) > 1 else ""
    out.append(f"{'  ' * lvl}{c('36', label)}: {' | '.join(names)}{count}")
    objs = [s for s in samples if isinstance(s, dict)]
    keys = {}
    for o in objs:
        for k, x in o.items():
            keys.setdefault(k, []).append(x)
    for k, vals in keys.items():
        shape(vals, lvl + 1, out, k + ("?" if len(vals) < len(objs) else ""))
    items = [x for s in samples if isinstance(s, list) for x in s]
    if items:
        shape(items, lvl + 1, out, "[]")


# ---------------------------------------------------------------- commands
def cmd_view(a):
    data = load(a.file)
    if a.compact:
        print(json.dumps(data, ensure_ascii=False, separators=(",", ":")))
    else:
        print(pretty(data, a.indent))


def cmd_get(a):
    data = load(a.file)
    res = query(data, a.path)
    if not res:
        sys.stderr.write("no match\n")
        return 1
    for r in res:
        print(r if (a.raw and isinstance(r, str)) else pretty(r))
    return 0


def cmd_flat(a):
    for p, v in flatten(load(a.file)):
        print(f"{c('36', p)} = {scalar(v) if not isinstance(v, (dict, list)) else json.dumps(v)}")


def cmd_diff(a):
    x, y = dict(flatten(load(a.a))), dict(flatten(load(a.b)))
    n = 0
    for k in sorted(set(x) | set(y)):
        if k not in y:
            print(c("31", f"- {k} = {json.dumps(x[k], ensure_ascii=False)}"))
        elif k not in x:
            print(c("32", f"+ {k} = {json.dumps(y[k], ensure_ascii=False)}"))
        elif x[k] != y[k] or type(x[k]) is not type(y[k]):
            print(c("33", f"~ {k}: {json.dumps(x[k], ensure_ascii=False)} -> {json.dumps(y[k], ensure_ascii=False)}"))
        else:
            continue
        n += 1
    print(c("90", f"{n} difference(s)") if n else c("32", "identical"))
    return 1 if n else 0


def cmd_csv(a):
    data = load(a.file)
    rows = data if isinstance(data, list) else [data]
    flat = [dict(flatten(r)) if isinstance(r, (dict, list)) else {"value": r} for r in rows]
    cols = []
    for r in flat:
        for k in r:
            if k not in cols:
                cols.append(k)
    out = open(a.output, "w", newline="", encoding="utf-8-sig") if a.output else sys.stdout
    w = csv.writer(out)
    w.writerow(cols)
    for r in flat:
        w.writerow(["" if r.get(k) is None else r.get(k) for k in cols])
    if a.output:
        out.close()
        sys.stderr.write(f"wrote {len(flat)} row(s) to {a.output}\n")


def cmd_shape(a):
    out = []
    shape([load(a.file)], 0, out, "$")
    print("\n".join(out))


def main():
    p = argparse.ArgumentParser(prog="jsonlens", description="One-file JSON toolkit.")
    p.add_argument("-V", "--version", action="version", version=__version__)
    sub = p.add_subparsers(dest="cmd", required=True)

    def add(name, fn, help_):
        s = sub.add_parser(name, help=help_)
        s.set_defaults(fn=fn)
        return s

    s = add("view", cmd_view, "pretty-print with colors")
    s.add_argument("file", nargs="?")
    s.add_argument("-c", "--compact", action="store_true", help="minify instead")
    s.add_argument("-i", "--indent", type=int, default=2)

    s = add("get", cmd_get, 'query a path, e.g. "users[*].name" or "..id"')
    s.add_argument("file")
    s.add_argument("path")
    s.add_argument("-r", "--raw", action="store_true", help="print strings without quotes")

    s = add("flat", cmd_flat, "flatten to path = value")
    s.add_argument("file", nargs="?")

    s = add("diff", cmd_diff, "compare two JSON files")
    s.add_argument("a")
    s.add_argument("b")

    s = add("csv", cmd_csv, "convert to CSV")
    s.add_argument("file", nargs="?")
    s.add_argument("-o", "--output")

    s = add("shape", cmd_shape, "show the structure/schema")
    s.add_argument("file", nargs="?")

    a = p.parse_args()
    try:
        sys.exit(a.fn(a) or 0)
    except BrokenPipeError:
        sys.exit(0)
    except KeyboardInterrupt:
        sys.exit(130)


if __name__ == "__main__":
    main()
