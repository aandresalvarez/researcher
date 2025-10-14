#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os

import httpx
import base64
import json as _json

try:
    from google.cloud import storage  # type: ignore
except Exception:  # pragma: no cover - optional dependency
    storage = None  # type: ignore


def main() -> int:
    p = argparse.ArgumentParser(description="Restore workspace bundle from GCS")
    p.add_argument("slug", help="Workspace slug to import into")
    p.add_argument("gcs_uri", help="gs://bucket/path/to/bundle.zip OR gs://bucket/prefix/ when using --latest")
    p.add_argument("--base-url", default=os.getenv("UAMM_BASE_URL", "http://127.0.0.1:8000"))
    p.add_argument("--api-key", default=os.getenv("UAMM_API_KEY"))
    p.add_argument("--replace", action="store_true", help="Use replace=true to delete before import")
    p.add_argument("--reindex", action="store_true", help="Trigger vector reindex after import")
    p.add_argument("--latest", action="store_true", help="Resolve latest object under the given prefix")
    args = p.parse_args()

    if storage is None:
        print("google-cloud-storage not installed; run make install-gcp", flush=True)
        return 2

    if not args.gcs_uri.startswith("gs://"):
        print("gcs_uri must start with gs://", flush=True)
        return 2
    _, rest = args.gcs_uri.split("gs://", 1)
    bucket_name, key = rest.split("/", 1)

    client_gcs = storage.Client()
    bucket = client_gcs.bucket(bucket_name)
    if args.latest:
        # Treat key as prefix; resolve latest matching workspace bundle
        prefix = key if key.endswith("/") else key + "/"
        # Expect filenames like workspace_<slug>_...zip(.enc.json)
        target_prefix = prefix + f"workspace_{args.slug}_"
        candidates = list(client_gcs.list_blobs(bucket_name, prefix=target_prefix))
        candidates = [b for b in candidates if b.name.endswith('.zip') or b.name.endswith('.zip.enc.json')]
        if not candidates:
            print({"error": "no_backups_found", "prefix": target_prefix})
            return 2
        candidates.sort(key=lambda b: b.updated, reverse=True)
        blob = candidates[0]
        key = blob.name
    else:
        blob = bucket.blob(key)
    content = blob.download_as_bytes()
    # If encrypted envelope, decrypt via KMS
    if key.endswith(".enc.json"):
        try:
            envelope = _json.loads(content.decode("utf-8"))
            from cryptography.hazmat.primitives.ciphers.aead import AESGCM  # type: ignore
            from google.cloud import kms_v1  # type: ignore
            kms_client = kms_v1.KeyManagementServiceClient()
            wrapped = base64.b64decode(envelope["dek_wrapped"])  # type: ignore[index]
            dek = kms_client.decrypt(name=envelope["kms_key"], ciphertext=wrapped).plaintext  # type: ignore[index]
            aesgcm = AESGCM(dek)
            nonce = base64.b64decode(envelope["nonce"])  # type: ignore[index]
            ct = base64.b64decode(envelope["ciphertext"])  # type: ignore[index]
            content = aesgcm.decrypt(nonce, ct, associated_data=b"uamm")
        except Exception as exc:
            print(f"decrypt_failed: {exc}", flush=True)
            return 2

    headers = {}
    if args.api_key:
        headers["Authorization"] = f"Bearer {args.api_key}"
    url = f"{args.base_url}/workspaces/{args.slug}/import"
    params = {"replace": "true" if args.replace else "false"}

    filename = os.path.basename(key)
    if filename.endswith(".enc.json"):
        filename = filename.replace(".enc.json", "")
    files = {"file": (filename, content, "application/zip")}
    with httpx.Client(timeout=120) as client:
        r = client.post(url, headers=headers, params=params, files=files)
        r.raise_for_status()
        print(r.json())
        if args.reindex:
            r2 = client.post(f"{args.base_url}/workspaces/{args.slug}/vector/reindex", headers=headers)
            r2.raise_for_status()
            print({"reindex": r2.json()})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
