#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import time
from datetime import datetime

import httpx
import base64
from typing import Optional

try:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    AESGCM = None  # type: ignore

try:
    from google.cloud import kms_v1  # type: ignore
except Exception:  # pragma: no cover
    kms_v1 = None  # type: ignore

try:
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    storage = None  # type: ignore


def fmt_ts(ts: float | None) -> str:
    if not ts:
        return ""
    return datetime.utcfromtimestamp(ts).strftime("%Y%m%dT%H%M%SZ")


def main() -> int:
    p = argparse.ArgumentParser(description="Backup workspace bundle to GCS")
    p.add_argument("slug", help="Workspace slug to export")
    p.add_argument("--base-url", default=os.getenv("UAMM_BASE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--api-key", default=os.getenv("UAMM_API_KEY"))
    p.add_argument("--since", type=float, default=None, help="Export since ts (epoch seconds)")
    p.add_argument("--until", type=float, default=None, help="Export until ts (epoch seconds)")
    p.add_argument("--bucket", default=os.getenv("UAMM_GCS_BUCKET"))
    p.add_argument("--prefix", default=os.getenv("UAMM_GCS_PREFIX", "backups"))
    p.add_argument("--replace", action="store_true", help="Tag filename as replace bundle")
    p.add_argument("--kms-key", default=os.getenv("UAMM_GCP_KMS_KEY"), help="Full KMS key resource name for envelope encryption (optional)")
    p.add_argument("--retention-count", type=int, default=int(os.getenv("UAMM_GCS_RETENTION_COUNT", "0")), help="Keep the N most recent backups (0=disable)")
    p.add_argument("--retention-days", type=int, default=int(os.getenv("UAMM_GCS_RETENTION_DAYS", "0")), help="Delete backups older than N days (0=disable)")
    args = p.parse_args()

    if storage is None:
        print("google-cloud-storage not installed; run make install-gcp", flush=True)
        return 2
    if not args.bucket:
        print("Missing --bucket or UAMM_GCS_BUCKET", flush=True)
        return 2

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    url = f"{args.base_url}/workspaces/{args.slug}/export"
    params = {}
    if args.since:
        params["since_ts"] = args.since
    if args.until:
        params["until_ts"] = args.until

    with httpx.Client(timeout=120) as client:
        r = client.get(url, headers=headers, params=params)
        r.raise_for_status()
        content = r.content

    ts = fmt_ts(time.time())
    tag = "replace" if args.replace else "merge"
    name = f"workspace_{args.slug}_{ts}_{tag}.zip"
    encrypted: Optional[bytes] = None
    if args.kms_key:
        if AESGCM is None or kms_v1 is None:
            print("Encryption requested but dependencies missing (cryptography/google-cloud-kms)", flush=True)
            return 2
        try:
            # Generate DEK and wrap with KMS
            dek = os.urandom(32)
            kms_client = kms_v1.KeyManagementServiceClient()
            wrapped = kms_client.encrypt(name=args.kms_key, plaintext=dek).ciphertext
            aesgcm = AESGCM(dek)
            nonce = os.urandom(12)
            ct = aesgcm.encrypt(nonce, content, associated_data=b"uamm")
            envelope = {
                "type": "uamm_encrypted_bundle",
                "algorithm": "AES-256-GCM",
                "kms_key": args.kms_key,
                "dek_wrapped": base64.b64encode(wrapped).decode("ascii"),
                "nonce": base64.b64encode(nonce).decode("ascii"),
                "ciphertext": base64.b64encode(ct).decode("ascii"),
                "created_at": time.time(),
                "filename": name,
            }
            import json as _json

            encrypted = _json.dumps(envelope).encode("utf-8")
            name = name + ".enc.json"
        except Exception as exc:
            print(f"encryption_failed: {exc}", flush=True)
            return 2

    path = f"{args.prefix.rstrip('/')}/{name}" if args.prefix else name

    client_gcs = storage.Client()  # ADC or env creds
    bucket = client_gcs.bucket(args.bucket)
    blob = bucket.blob(path)
    if encrypted is not None:
        blob.upload_from_string(encrypted, content_type="application/json")
        print({"uploaded": f"gs://{args.bucket}/{path}", "bytes": len(encrypted), "encrypted": True})
    else:
        blob.upload_from_string(content, content_type="application/zip")
        print({"uploaded": f"gs://{args.bucket}/{path}", "bytes": len(content), "encrypted": False})

    # Retention: delete older backups beyond thresholds
    try:
        count = int(args.retention_count or 0)
        days = int(args.retention_days or 0)
        if count > 0 or days > 0:
            # List blobs for this workspace
            prefix = (args.prefix.rstrip('/') + '/') if args.prefix else ''
            key_prefix = f"{prefix}workspace_{args.slug}_"
            blobs = list(client_gcs.list_blobs(args.bucket, prefix=key_prefix))
            # Filter to zip variants
            blobs = [b for b in blobs if b.name.endswith('.zip') or b.name.endswith('.zip.enc.json')]
            # Sort newest first by updated
            blobs.sort(key=lambda b: b.updated, reverse=True)
            keep_set = set()
            if count > 0:
                for b in blobs[:count]:
                    keep_set.add(b.name)
            # Time cutoff
            cutoff = None
            if days > 0:
                cutoff = time.time() - days * 86400
            deleted = []
            for b in blobs:
                if b.name == path or b.name in keep_set:
                    continue
                if cutoff is not None:
                    # Convert to epoch seconds
                    ts = b.updated.timestamp() if hasattr(b.updated, 'timestamp') else None
                    if ts is not None and ts >= cutoff:
                        continue  # still within retention window
                # Delete
                try:
                    bucket.blob(b.name).delete()
                    deleted.append(b.name)
                except Exception as exc:
                    print({"warn": "delete_failed", "name": b.name, "error": str(exc)})
            if deleted:
                print({"retention_deleted": deleted})
    except Exception as exc:
        print({"warn": "retention_error", "error": str(exc)})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
