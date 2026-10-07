#!/usr/bin/env python3
"""Make sure installing the new pianojuke.py does not break what calls the old one.

    routecheck.py [--python PY] OLD.py NEW.py
        Lists every Flask route in OLD.py (read with ast, never imported) and checks
        that NEW.py serves the same path with at least the same methods. Exit 0 when
        nothing would disappear, 3 when something would.

    routecheck.py --min-velocity OLD.py
        Prints OLD.py's MIN_VELOCITY so the installer can carry it over.
"""
import argparse
import ast
import json
import os
import re
import subprocess
import sys

VERBS = {"get": "GET", "post": "POST", "put": "PUT", "delete": "DELETE", "patch": "PATCH"}
IGNORED = {"HEAD", "OPTIONS"}


def literal(node):
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, TypeError):
        return None


def methods_kw(keywords, default):
    for kw in keywords:
        if kw.arg == "methods":
            value = literal(kw.value)
            if isinstance(value, (list, tuple, set)):
                return sorted({str(m).upper() for m in value} - IGNORED)
    return default


def old_routes(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    routes, blueprints = [], False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and getattr(node.func, "id", getattr(node.func, "attr", "")) == "Blueprint":
            blueprints = True
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) \
                and node.func.attr == "add_url_rule" and node.args:
            rule = literal(node.args[0])
            if isinstance(rule, str):
                routes.append({"path": rule, "methods": methods_kw(node.keywords, ["GET"]),
                               "where": f"add_url_rule, line {node.lineno}"})
        for dec in getattr(node, "decorator_list", []):
            if not (isinstance(dec, ast.Call) and isinstance(dec.func, ast.Attribute) and dec.args):
                continue
            verb = dec.func.attr
            if verb != "route" and verb not in VERBS:
                continue
            rule = literal(dec.args[0])
            if not isinstance(rule, str):
                continue
            methods = methods_kw(dec.keywords, ["GET"]) if verb == "route" else [VERBS[verb]]
            routes.append({"path": rule, "methods": methods, "where": f"{node.name}(), line {node.lineno}"})
    return routes, blueprints


def new_routes(path, python):
    env = dict(os.environ, PIANOJUKE_PORT="none")
    out = subprocess.run([python, path, "--routes"], env=env, check=True,
                         capture_output=True, text=True, timeout=60).stdout
    return json.loads(out)


def shape(rule):
    """/api/play/<path:name> and /api/play/<file> are the same route to a caller."""
    return re.sub(r"<(?:[^:<>]+:)?[^<>]+>", "<>", rule)


def min_velocity(path):
    with open(path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), path)
    for node in tree.body:
        if isinstance(node, ast.Assign) and any(getattr(t, "id", None) == "MIN_VELOCITY" for t in node.targets):
            value = literal(node.value)
            if isinstance(value, int) and 0 < value < 128:
                return value
    return None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--python", default=sys.executable, help="interpreter that runs NEW.py")
    ap.add_argument("--min-velocity", action="store_true")
    ap.add_argument("files", nargs="+")
    args = ap.parse_args()

    if args.min_velocity:
        value = min_velocity(args.files[0])
        if value is not None:
            print(value)
        return 0

    old_path, new_path = args.files
    old, blueprints = old_routes(old_path)
    new = {shape(r["path"]): r for r in new_routes(new_path, args.python)}

    missing, fewer_methods, kept = [], [], []
    for r in old:
        match = new.get(shape(r["path"]))
        if match is None:
            missing.append(r)
        elif set(r["methods"]) - set(match["methods"]):
            fewer_methods.append((r, sorted(set(r["methods"]) - set(match["methods"]))))
        else:
            kept.append(r)

    print(f"Old version has {len(old)} routes; the new one serves {len(new)}.")
    if blueprints:
        print("NOTE: the old file uses Flask Blueprints; their url_prefix is not applied above.")
    for r in kept:
        print(f"  kept     {'/'.join(r['methods']):<10} {r['path']}   ({r['where']})")
    for r, lost in fewer_methods:
        print(f"  METHODS  {'/'.join(r['methods']):<10} {r['path']}   new version lacks {', '.join(lost)}   ({r['where']})")
    for r in missing:
        print(f"  MISSING  {'/'.join(r['methods']):<10} {r['path']}   ({r['where']})")
    if kept:
        print("Kept routes have the same path and methods. If Home Assistant sends parameters to them,")
        print("compare the old functions listed above with the new ones before relying on them.")
    if missing or fewer_methods:
        print(f"\n{len(missing) + len(fewer_methods)} route(s) would break for anything calling them.")
        return 3
    return 0


if __name__ == "__main__":
    sys.exit(main())
