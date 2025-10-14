#!/usr/bin/env python3
from __future__ import annotations

import argparse
from uamm.config.settings import load_settings
from uamm.storage.db import ensure_schema
from uamm.storage.workspaces import (
    normalize_root,
    ensure_allowed_root,
    ensure_workspace_fs,
)
from uamm.security.auth import (
    create_workspace,
    issue_api_key,
    list_keys,
    list_workspaces,
)
import sqlite3


def cmd_create(args):
    settings = load_settings()
    ensure_schema(settings.db_path, settings.schema_path)
    con = sqlite3.connect(settings.db_path)
    try:
        root = None
        if args.root:
            root = normalize_root(args.root)
            ensure_allowed_root(
                root,
                tuple(settings.workspace_base_dirs or []),
                bool(settings.workspace_restrict_to_bases),
            )
            # Initialize per-workspace FS and DB
            ensure_workspace_fs(root, settings.schema_path)
        create_workspace(con, args.slug, args.name or args.slug, root)
    finally:
        con.close()
    out = {"created": args.slug}
    if args.root:
        out["root"] = root
    print(out)


def cmd_issue(args):
    settings = load_settings()
    ensure_schema(settings.db_path, settings.schema_path)
    token = issue_api_key(settings.db_path, workspace=args.slug, role=args.role, label=args.label, prefix=settings.api_key_prefix)
    print({"api_key": token})


def cmd_list_keys(args):
    settings = load_settings()
    ensure_schema(settings.db_path, settings.schema_path)
    keys = list_keys(settings.db_path, workspace=args.slug)
    print({"keys": [k.__dict__ for k in keys]})


def cmd_list(args):
    settings = load_settings()
    ensure_schema(settings.db_path, settings.schema_path)
    print({"workspaces": list_workspaces(settings.db_path)})


def main() -> int:
    p = argparse.ArgumentParser(description="Workspace & API key management")
    sub = p.add_subparsers(dest="cmd", required=True)

    c = sub.add_parser("create", help="Create a workspace")
    c.add_argument("slug")
    c.add_argument("--name")
    c.add_argument("--root", help="Filesystem root for this workspace (will be created if missing)")
    c.set_defaults(func=cmd_create)

    k = sub.add_parser("issue", help="Issue API key for a workspace")
    k.add_argument("slug")
    k.add_argument("role", choices=["admin", "editor", "viewer"])
    k.add_argument("label")
    k.set_defaults(func=cmd_issue)

    lk = sub.add_parser("list-keys", help="List keys for a workspace")
    lk.add_argument("slug")
    lk.set_defaults(func=cmd_list_keys)

    lw = sub.add_parser("list", help="List workspaces")
    lw.set_defaults(func=cmd_list)

    args = p.parse_args()
    return args.func(args) or 0


if __name__ == "__main__":
    raise SystemExit(main())
