from __future__ import annotations

import argparse
import json
import mimetypes
import os
from pathlib import Path
from typing import Iterable

import requests


DRIVE_API = "https://www.googleapis.com/drive/v3"
DRIVE_UPLOAD_API = "https://www.googleapis.com/upload/drive/v3"
FOLDER_MIME = "application/vnd.google-apps.folder"

KEEPER_ROOT_FOLDER_NAME = "Keeper League Advisor"
LATEST_FOLDER_NAME = "latest"
HISTORY_FOLDER_NAME = "history"
WEEKLY_RESULTS_FOLDER_NAME = "weekly_results"

PERSISTENT_FILES = [
    "manifest.json",
    "league_state.json",
    "roster_state.json",
    "matchup_state.json",
    "keeper_state.json",
    "player_pool.json",
    "intelligence_state.json",
    "delta_state.json",
    "league_results.json",
    "advisor_packet.json",
]

DOWNLOAD_FILES = PERSISTENT_FILES


def _credentials() -> tuple[str, str, str, str]:
    required = [
        "GOOGLE_OAUTH_CLIENT_ID",
        "GOOGLE_OAUTH_CLIENT_SECRET",
        "GOOGLE_OAUTH_REFRESH_TOKEN",
        "GOOGLE_DRIVE_FOLDER_ID",
    ]
    missing = [key for key in required if not os.environ.get(key)]
    if missing:
        raise RuntimeError(
            f"Missing required environment variables: {', '.join(missing)}"
        )
    return tuple(os.environ[key] for key in required)  # type: ignore[return-value]


def _access_token() -> tuple[str, str]:
    client_id, client_secret, refresh_token, root_folder_id = _credentials()
    response = requests.post(
        "https://oauth2.googleapis.com/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "refresh_token": refresh_token,
            "grant_type": "refresh_token",
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"OAuth token exchange failed ({response.status_code}): {response.text}"
        )
    return response.json()["access_token"], root_folder_id


def _headers(token: str, content_type: str | None = None) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {token}"}
    if content_type:
        headers["Content-Type"] = content_type
    return headers


def _verify_root(token: str, root_folder_id: str) -> dict:
    response = requests.get(
        f"{DRIVE_API}/files/{root_folder_id}",
        headers=_headers(token),
        params={"fields": "id,name,mimeType,trashed"},
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Configured Drive folder is not accessible ({response.status_code}): {response.text}"
        )
    data = response.json()
    if data.get("mimeType") != FOLDER_MIME or data.get("trashed"):
        raise RuntimeError("Configured GOOGLE_DRIVE_FOLDER_ID is not an active folder")
    return data


def _find_child(
    token: str,
    parent_id: str,
    name: str,
    mime_type: str | None = None,
) -> dict | None:
    escaped_name = name.replace("'", "\\'")
    query = f"'{parent_id}' in parents and name = '{escaped_name}' and trashed = false"
    if mime_type:
        query += f" and mimeType = '{mime_type}'"
    response = requests.get(
        f"{DRIVE_API}/files",
        headers=_headers(token),
        params={
            "q": query,
            "fields": "files(id,name,mimeType,modifiedTime,size)",
            "pageSize": 100,
        },
        timeout=30,
    )
    if response.status_code != 200:
        raise RuntimeError(f"Drive list failed ({response.status_code}): {response.text}")
    files = response.json().get("files", [])
    return files[0] if files else None


def _ensure_folder(token: str, parent_id: str, folder_name: str) -> dict:
    existing = _find_child(token, parent_id, folder_name, FOLDER_MIME)
    if existing:
        return existing
    response = requests.post(
        f"{DRIVE_API}/files",
        headers=_headers(token, "application/json"),
        json={
            "name": folder_name,
            "mimeType": FOLDER_MIME,
            "parents": [parent_id],
        },
        params={"fields": "id,name,mimeType"},
        timeout=30,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Could not create {folder_name} folder ({response.status_code}): {response.text}"
        )
    return response.json()


def _keeper_root(token: str, root_folder_id: str) -> dict:
    return _ensure_folder(token, root_folder_id, KEEPER_ROOT_FOLDER_NAME)


def _download_file(token: str, file_id: str, destination: Path) -> None:
    response = requests.get(
        f"{DRIVE_API}/files/{file_id}",
        headers=_headers(token),
        params={"alt": "media"},
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Drive download failed for {destination.name} ({response.status_code}): {response.text}"
        )
    destination.write_bytes(response.content)


def download_latest(
    destination: str | Path,
    filenames: Iterable[str] = DOWNLOAD_FILES,
) -> dict:
    token, configured_root_id = _access_token()
    configured_root = _verify_root(token, configured_root_id)
    keeper_root = _keeper_root(token, configured_root_id)
    latest = _ensure_folder(token, keeper_root["id"], LATEST_FOLDER_NAME)

    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    downloaded: list[str] = []
    missing: list[str] = []
    for name in filenames:
        remote = _find_child(token, latest["id"], name)
        if not remote:
            missing.append(name)
            continue
        _download_file(token, remote["id"], destination / name)
        downloaded.append(name)

    prior_manifest = destination / "manifest.json"
    prior_validated = False
    if prior_manifest.exists():
        manifest = json.loads(prior_manifest.read_text(encoding="utf-8"))
        prior_validated = manifest.get("validation_status") == "PASS"

    return {
        "status": "PASS",
        "configured_drive_root_name": configured_root.get("name"),
        "keeper_root_id": keeper_root["id"],
        "latest_folder_id": latest["id"],
        "downloaded": downloaded,
        "missing": missing,
        "prior_validated_baseline_present": prior_validated,
    }


def _upload_new(token: str, folder_id: str, local_path: Path) -> dict:
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    boundary = "keeper_league_advisor_boundary"
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
    response = requests.post(
        f"{DRIVE_UPLOAD_API}/files",
        headers=_headers(token, f"multipart/related; boundary={boundary}"),
        params={"uploadType": "multipart", "fields": "id,name,modifiedTime,size"},
        data=prefix + content + suffix,
        timeout=60,
    )
    if response.status_code not in (200, 201):
        raise RuntimeError(
            f"Drive upload failed for {local_path.name} ({response.status_code}): {response.text}"
        )
    return response.json()


def _update_existing(token: str, file_id: str, local_path: Path) -> dict:
    mime = mimetypes.guess_type(local_path.name)[0] or "application/octet-stream"
    response = requests.patch(
        f"{DRIVE_UPLOAD_API}/files/{file_id}",
        headers=_headers(token, mime),
        params={"uploadType": "media", "fields": "id,name,modifiedTime,size"},
        data=local_path.read_bytes(),
        timeout=60,
    )
    if response.status_code != 200:
        raise RuntimeError(
            f"Drive update failed for {local_path.name} ({response.status_code}): {response.text}"
        )
    return response.json()


def _upsert_file(token: str, folder_id: str, local_path: Path) -> dict:
    existing = _find_child(token, folder_id, local_path.name)
    result = (
        _update_existing(token, existing["id"], local_path)
        if existing
        else _upload_new(token, folder_id, local_path)
    )
    return {
        "name": local_path.name,
        "id": result.get("id"),
        "mode": "updated" if existing else "created",
    }


def _copy_required_files(
    token: str,
    folder_id: str,
    source: Path,
    filenames: Iterable[str],
) -> list[dict]:
    uploaded: list[dict] = []
    for name in filenames:
        local_path = source / name
        if not local_path.exists():
            raise RuntimeError(f"Required persistent file missing: {name}")
        uploaded.append(_upsert_file(token, folder_id, local_path))
    return uploaded


def upload_validated(
    source: str | Path,
    filenames: Iterable[str] = PERSISTENT_FILES,
) -> dict:
    token, configured_root_id = _access_token()
    configured_root = _verify_root(token, configured_root_id)
    keeper_root = _keeper_root(token, configured_root_id)

    source = Path(source)
    manifest_path = source / "manifest.json"
    if not manifest_path.exists():
        raise RuntimeError(
            "manifest.json missing; refusing to persist unvalidated Keeper refresh"
        )
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("validation_status") != "PASS":
        raise RuntimeError(
            "manifest validation_status is not PASS; refusing to overwrite validated Keeper state"
        )

    run_id = str(manifest.get("run_id") or "").strip()
    if not run_id:
        raise RuntimeError("manifest run_id missing; refusing to persist Keeper refresh")

    latest = _ensure_folder(token, keeper_root["id"], LATEST_FOLDER_NAME)
    latest_uploaded = _copy_required_files(token, latest["id"], source, filenames)

    history = _ensure_folder(token, keeper_root["id"], HISTORY_FOLDER_NAME)
    run_folder = _ensure_folder(token, history["id"], run_id)
    history_uploaded = _copy_required_files(token, run_folder["id"], source, filenames)

    weekly_folder_id = None
    weekly_uploaded: list[dict] = []
    results_path = source / "league_results.json"
    if results_path.exists():
        results = json.loads(results_path.read_text(encoding="utf-8"))
        if results.get("results_status") == "FINAL_RESULTS":
            results_week = int(results["week"])
            weekly_root = _ensure_folder(
                token, keeper_root["id"], WEEKLY_RESULTS_FOLDER_NAME
            )
            week_folder = _ensure_folder(
                token, weekly_root["id"], f"week_{results_week:02d}"
            )
            weekly_folder_id = week_folder["id"]
            weekly_uploaded = _copy_required_files(
                token,
                week_folder["id"],
                source,
                ["manifest.json", "league_results.json", "advisor_packet.json"],
            )

    return {
        "status": "PASS",
        "configured_drive_root_name": configured_root.get("name"),
        "keeper_root_id": keeper_root["id"],
        "latest_folder_id": latest["id"],
        "history_run_folder_id": run_folder["id"],
        "weekly_results_folder_id": weekly_folder_id,
        "latest_uploaded": latest_uploaded,
        "history_uploaded": history_uploaded,
        "weekly_uploaded": weekly_uploaded,
        "run_id": run_id,
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Google Drive persistence helper for Keeper League Advisor"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    download = sub.add_parser(
        "download",
        help="Download prior validated Keeper baseline from Drive",
    )
    download.add_argument("--dest", default="keeper_output/last_known_good")

    upload = sub.add_parser(
        "upload",
        help="Persist validated Keeper state to Drive",
    )
    upload.add_argument("--source", default="keeper_output/latest")

    args = parser.parse_args()

    try:
        result = (
            download_latest(args.dest)
            if args.command == "download"
            else upload_validated(args.source)
        )
        print(json.dumps(result, indent=2))
    except Exception as exc:
        print(json.dumps({"status": "FAIL", "error": str(exc)}, indent=2))
        raise SystemExit(2)


if __name__ == "__main__":
    main()
