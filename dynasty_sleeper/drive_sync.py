from __future__ import annotations

import argparse
import json
import mimetypes
import os
import sys
from pathlib import Path
from typing import Iterable

import requests

DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"
LATEST_FOLDER_NAME = "latest"
PERSISTENT_FILES = [
    "manifest.json",
    "league_state_current.csv",
    "roster_summary_current.csv",
    "league_transactions_master.csv",
    "reconciliation_report.csv",
    "roster_delta_reconciliation.csv",
    "weekly_matchup_context.csv",
    "weekly_matchup_summary.csv",
    "framework_matchup_packet.md",
    "player_week_context.csv",
]
DOWNLOAD_FILES = PERSISTENT_FILES


def _credentials() -> tuple[str, str, str, str]:
    required = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
        "GOOGLE_DRIVE_FOLDER_ID",
    ]
    missing = [k for k in required if not os.environ.get(k)]
    if missing:
        raise RuntimeError(f"Missing required environment variables: {', '.join(missing)}")
    return tuple(os.environ[k] for k in required)  # type: ignore[return-value]


def _access_token() -> tuple[str, str]:
    client_id, client_secret, refresh_token, root_folder_id = _credentials()
    resp = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"OAuth token exchange failed ({resp.status_code}): {resp.text}")
    return resp.json()["access_token"], root_folder_id


def _headers(token: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _verify_root(token: str, root_folder_id: str) -> dict:
    resp = requests.get(
        f"{DRIVE_API}/files/{root_folder_id}",
        headers=_headers(token),
        params={"fields": "id,name,mimeType,trashed"},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Configured Drive folder is not accessible ({resp.status_code}): {resp.text}")
    data = resp.json()
    if data.get("mimeType") != FOLDER_MIME or data.get("trashed"):
        raise RuntimeError("Configured GOOGLE_DRIVE_FOLDER_ID is not an active folder")
    return data


def _find_child(token: str, parent_id: str, name: str, mime_type: str | None = None) -> dict | None:
    escaped_name = name.replace("'", "\\'")
    q = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
    if mime_type:
        q += f" and mimeType = '{mime_type}'"
    resp = requests.get(
        f"{DRIVE_API}/files",
        headers=_headers(token),
        params={"q": q, "fields": "files(id,name,mimeType,modifiedTime,size)", "pageSize": 100},
        timeout=30,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Drive list failed ({resp.status_code}): {resp.text}")
    files = resp.json().get("files", [])
    return files[0] if files else None


def _ensure_latest_folder(token: str, root_folder_id: str) -> dict:
    existing = _find_child(token, root_folder_id, LATEST_FOLDER_NAME, FOLDER_MIME)
    if existing:
        return existing
    resp = requests.post(
        f"{DRIVE_API}/files",
        headers=_headers(token, "application/json"),
        json={"name": LATEST_FOLDER_NAME, "mimeType": FOLDER_MIME, "parents": [root_folder_id]},
        params={"fields": "id,name,mimeType"},
        timeout=30,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Could not create latest folder ({resp.status_code}): {resp.text}")
    return resp.json()


def download_latest(destination: str | Path, filenames: Iterable[str] = DOWNLOAD_FILES) -> dict:
    token, root_folder_id = _access_token()
    root = _verify_root(token, root_folder_id)
    latest = _ensure_latest_folder(token, root_folder_id)
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    downloaded: list[str] = []
    missing: list[str] = []
    for name in filenames:
        remote = _find_child(token, latest["id"], name)
        if not remote:
            missing.append(name)
            continue
        resp = requests.get(
            f"{DRIVE_API}/files/{remote['id']}",
            headers=_headers(token),
            params={"alt": "media"},
            timeout=60,
        )
        if resp.status_code != 200:
            raise RuntimeError(f"Download failed for {name} ({resp.status_code}): {resp.text}")
        (destination / name).write_bytes(resp.content)
        downloaded.append(name)
    return {
        "status": "PASS",
        "drive_root_name": root.get("name"),
        "latest_folder_id": latest["id"],
        "downloaded": downloaded,
        "missing": missing,
        "prior_baseline_present": "league_state_current.csv" in downloaded,
    }


def _upload_new(token: str, folder_id: str, local_path: Path) -> dict:
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    boundary = "dynasty_sleeper_boundary"
    metadata = json.dumps({"name": local_path.name, "parents": [folder_id]})
    content = local_path.read_bytes()
    prefix = (
        f"--{boundary}\r\n"
        "Content-Type: application/json; charset=UTF-8\r\n\r\n"
        f"{metadata}\r\n"
        f"--{boundary}\r\n"
        f"Content-Type: {mime}\r\n\r\n"
    ).encode("utf-8")
    suffix = f"\r\n--{boundary}--".encode("utf-8")
    resp = requests.post(
        f"{DRIVE_UPLOAD_API}/files",
        headers=_headers(token, f"multipart/related; boundary={boundary}"),
        params={"uploadType": "multipart", "fields": "id,name,modifiedTime,size"},
        data=prefix + content + suffix,
        timeout=60,
    )
    if resp.status_code not in (200, 201):
        raise RuntimeError(f"Upload failed for {local_path.name} ({resp.status_code}): {resp.text}")
    return resp.json()


def _update_existing(token: str, file_id: str, local_path: Path) -> dict:
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    resp = requests.patch(
        f"{DRIVE_UPLOAD_API}/files/{file_id}",
        headers=_headers(token, mime),
        params={"uploadType": "media", "fields": "id,name,modifiedTime,size"},
        data=local_path.read_bytes(),
        timeout=60,
    )
    if resp.status_code != 200:
        raise RuntimeError(f"Update failed for {local_path.name} ({resp.status_code}): {resp.text}")
    return resp.json()


def upload_latest(source: str | Path, filenames: Iterable[str] = PERSISTENT_FILES) -> dict:
    token, root_folder_id = _access_token()
    root = _verify_root(token, root_folder_id)
    latest = _ensure_latest_folder(token, root_folder_id)
    source = Path(source)

    manifest_path = source / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError("manifest.json missing; refusing to persist unvalidated refresh")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("overall_status") != "PASS":
        raise RuntimeError("manifest overall_status is not PASS; refusing to overwrite last known-good baseline")

    uploaded: list[dict] = []
    for name in filenames:
        local_path = source / name
        if not local_path.exists():
            raise RuntimeError(f"Required persistent file missing: {name}")
        existing = _find_child(token, latest["id"], name)
        result = _update_existing(token, existing["id"], local_path) if existing else _upload_new(token, latest["id"], local_path)
        uploaded.append({"name": name, "id": result.get("id"), "mode": "updated" if existing else "created"})

    return {
        "status": "PASS",
        "drive_root_name": root.get("name"),
        "latest_folder_id": latest["id"],
        "uploaded": uploaded,
        "baseline_status": manifest.get("baseline_status"),
        "generated_at_utc": manifest.get("generated_at_utc"),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Google Drive persistence helper for Dynasty Sleeper automation")
    sub = parser.add_subparsers(dest="command", required=True)
    d = sub.add_parser("download", help="Download prior validated baseline from Drive")
    d.add_argument("--dest", default="baseline")
    u = sub.add_parser("upload", help="Upload validated baseline to Drive")
    u.add_argument("--source", default="output")
    args = parser.parse_args()

    try:
        result = download_latest(args.dest) if args.command == "download" else upload_latest(args.source)
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
