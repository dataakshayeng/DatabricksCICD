import argparse
import base64
import json
import os
import sys
from pathlib import Path
from typing import Optional
from urllib import error, request


def api_request(host: str, token: str, method: str, endpoint: str, data: dict | None = None) -> dict:
    url = f"{host.rstrip('/')}/api/2.0{endpoint}"
    body = None
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }

    if data is not None:
        body = json.dumps(data).encode("utf-8")

    req = request.Request(url, data=body, headers=headers, method=method)
    try:
        with request.urlopen(req, timeout=60) as response:
            payload = response.read().decode("utf-8")
            if payload:
                return json.loads(payload)
            return {}
    except error.HTTPError as exc:
        details = exc.read().decode("utf-8", errors="ignore")
        parsed_details = _parse_databricks_error(details)
        raise RuntimeError(
            f"Databricks API request failed: {exc.code} {endpoint} {parsed_details}"
        ) from exc


def _parse_databricks_error(payload: str) -> str:
    try:
        error_json = json.loads(payload)
        if isinstance(error_json, dict):
            message = error_json.get("message")
            if message:
                return message
        return payload
    except json.JSONDecodeError:
        return payload


def ensure_workspace_path(host: str, token: str, path: str) -> None:
    api_request(host, token, "POST", "/workspace/mkdirs", {"path": path})


def delete_workspace_path(host: str, token: str, path: str) -> None:
    try:
        api_request(host, token, "POST", "/workspace/delete", {"path": path, "recursive": True})
    except RuntimeError as exc:
        message = str(exc).lower()
        if "404" in message and (
            "resource_does_not_exist" in message
            or "resource does not exist" in message
            or "path (" in message
            or "doesn't exist" in message
        ):
            return
        raise


def import_file(host: str, token: str, source_path: Path, workspace_path: str, file_format: str, language: str | None = None) -> None:
    file_bytes = source_path.read_bytes()
    payload = {
        "path": workspace_path,
        "format": file_format,
        "overwrite": True,
        "content": base64.b64encode(file_bytes).decode("utf-8"),
    }
    if language:
        payload["language"] = language
    api_request(host, token, "POST", "/workspace/import", payload)


def get_format(source_path: Path) -> tuple[str, str | None]:
    suffix = source_path.suffix.lower()
    if suffix == ".ipynb":
        return "JUPYTER", None
    if suffix == ".py":
        return "SOURCE", "PYTHON"
    if suffix == ".sql":
        return "SOURCE", "SQL"
    return "SOURCE", None


def deploy_directory(host: str, token: str, source_dir: Path, workspace_root: str) -> None:
    if not source_dir.exists():
        raise FileNotFoundError(f"Source directory not found: {source_dir}")

    delete_workspace_path(host, token, workspace_root)
    ensure_workspace_path(host, token, workspace_root)

    for path in sorted(source_dir.rglob("*")):
        if path.is_dir():
            continue

        rel_path = path.relative_to(source_dir)
        workspace_path = f"{workspace_root.rstrip('/')}/{rel_path.as_posix()}"
        parent_dir = str(Path(workspace_path).parent)
        ensure_workspace_path(host, token, parent_dir)

        file_format, language = get_format(path)
        import_file(host, token, path, workspace_path, file_format, language)
        print(f"Uploaded {path} -> {workspace_path}")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Deploy local files to Azure Databricks workspace")
    parser.add_argument("--source", default=os.getenv("SOURCE_FOLDER", "databricks"), help="Local source directory")
    parser.add_argument(
        "--workspace-path",
        default=os.getenv("DATABRICKS_WORKSPACE_PATH", "/Production"),
        help="Destination Databricks workspace path",
    )
    parser.add_argument(
        "--host",
        default=os.getenv("DATABRICKS_HOST", ""),
        help="Databricks workspace host",
    )
    parser.add_argument(
        "--token",
        default=os.getenv("DATABRICKS_TOKEN", ""),
        help="Databricks access token",
    )
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    source = args.source
    workspace_path = args.workspace_path
    host = args.host.strip()
    token = args.token.strip()

    if not host or not token:
        print("Missing DATABRICKS_HOST or DATABRICKS_TOKEN environment variables or arguments", file=sys.stderr)
        return 1

    if not host.startswith("http"):
        host = f"https://{host}"

    source_dir = Path(source).resolve()
    deploy_directory(host, token, source_dir, workspace_path)
    print(f"Deployment complete. Files synced to {workspace_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
