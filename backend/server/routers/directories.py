"""Directory, file lifecycle, search, rename, and move endpoints."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import cast

from fastapi import APIRouter, Body, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from backend.database.models import Directory, File, User
from backend.server.jwt_handler import get_current_user
from backend.server.security import create_public_stream_token, create_public_zip_token
from backend.server.utils import perform_validation

router: APIRouter = APIRouter()


class ZipDownloadRequest(BaseModel):
    """List of active file IDs to combine in a streamed ZIP archive."""

    file_ids: list[int] = Field(min_length=2, max_length=500)


@router.post("/files/zip-link")
async def create_zip_download_link(payload: ZipDownloadRequest, user: User = Depends(get_current_user)) -> dict[str, object]:
    """Validate the selected files and create a short-lived streaming ZIP URL."""

    file_ids = payload.file_ids
    if any(file_id <= 0 for file_id in file_ids):
        raise HTTPException(status_code=422, detail="File IDs must be positive integers")
    if len(set(file_ids)) != len(file_ids):
        raise HTTPException(status_code=422, detail="Duplicate file IDs are not allowed")

    # Resolve ownership and active/trash state before minting a public download token.
    for file_id in file_ids:
        file = await File.get(user.username, fid=file_id)
        if file is None or file.username != user.username:
            raise HTTPException(status_code=404, detail=f"Active file {file_id} was not found")

    return {"public_token": create_public_zip_token(file_ids, user.username), "expires_in": 900}


@router.post("/create-folder")
async def create_folder(directory: str, name: str, user: User = Depends(get_current_user)) -> JSONResponse:
    """Create a directory within the authenticated user’s filesystem."""

    folder: Directory = Directory(
        path=Path(perform_validation(directory, "directory")) / perform_validation(name, "file_name"),
        modified_at=datetime.now(timezone.utc),
        username=user.username,
    )
    await folder.save()
    return JSONResponse({
        "message": "Folder created successfully",
        "directory": str(folder),
    })


@router.get("/search")
async def search(
        query: str = Query(..., min_length=1, description="File or directory name to search for"),
        user: User = Depends(get_current_user),
        trash: bool = Query(False, description="Search trashed files and directories"),
) -> dict[str, list[dict[str, object]]]:
    """Search this user's files and directories by name across all folders."""

    query = query.strip()

    if not query:
        raise HTTPException(status_code=422, detail="Search query cannot be empty")

    matching_files: list[File] = await File.get_all(user.username, trash=trash, file_name=query)
    matching_directories: list[Directory] = await Directory.get_all(user.username, trash=trash, directory_name=query, )
    files: list[dict[str, object]] = []

    for file in matching_files:
        directory: Directory | None = await Directory.get(user.username, did=file.directory_id, trash=trash)
        item: dict[str, object] = file.model_dump(mode="json")
        item["directory"] = str(directory.path) if directory is not None else ""
        files.append(item)

    directories: list[dict[str, object]] = [directory.model_dump(mode="json") for directory in matching_directories]
    return {"files": files, "directories": directories}


@router.get("/directory")
async def get_directory(directory: str, user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    """Return the child directories and files at a requested path."""

    directory_obj: Directory | None = await Directory.get(user.username, path=Path(directory))

    if directory_obj is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    return await Directory.get_all(user.username, directory=Path(directory)), await File.get_all(user.username, directory_id=directory_obj.id)


@router.get("/trash")
async def get_trash(user: User = Depends(get_current_user)) -> tuple[list[Directory], list[File]]:
    """Return trashed directories and files after purging expired entries."""

    await File.purge_expired(user.username)
    return await Directory.get_all(user.username, trash=True), await File.get_all(user.username, trash=True)


@router.get("/tree")
async def get_file_tree(user: User = Depends(get_current_user)) -> JSONResponse:
    """Return the authenticated user's complete file tree and storage total in one request."""

    await File.purge_expired(user.username)

    active_directories: list[Directory] = await Directory.get_all(user.username)
    trashed_directories: list[Directory] = await Directory.get_all(user.username, trash=True)
    active_files: list[File] = await File.get_all(user.username)
    trashed_files: list[File] = await File.get_all(user.username, trash=True)

    all_directories: list[Directory] = [*active_directories, *trashed_directories]
    all_files: list[File] = [*active_files, *trashed_files]
    directory_path_by_id: dict[int, str] = {directory.id: str(directory.path) for directory in all_directories if directory.id is not None}
    directory_records: list[dict[str, object]] = []

    for directory in all_directories:
        record: dict[str, object] = directory.model_dump(mode="json")
        record["is_deleted"] = directory.deleted_at is not None
        directory_records.append(record)

    file_records: list[dict[str, object]] = []

    for file in all_files:
        record = file.model_dump(mode="json", exclude={"links"})
        record["directory"] = directory_path_by_id.get(file.directory_id, "")
        record["is_deleted"] = file.deleted_at is not None
        file_records.append(record)

    storage_used_bytes: int = sum(max(0, int(file.size or 0)) for file in all_files)
    return JSONResponse({
        "directories": directory_records,
        "files": file_records,
        "storage_used_bytes": storage_used_bytes,
    })


def _clean_name(name: str, entity: str) -> str:
    """Validate a single file/folder name shared by rename operations."""

    clean_name: str = perform_validation(name.strip(), "file_name")

    if not clean_name or clean_name in {".", ".."} or "/" in clean_name or "\\" in clean_name:
        raise HTTPException(status_code=422, detail=f"Invalid {entity} name")

    return clean_name


async def _update_file_fields(file: File, *, name: str | None = None, directory_id: int | None = None) -> None:
    """Persist supported file changes for both rename and move operations."""

    if name is not None:
        file.name = name

    if directory_id is not None:
        file.directory_id = directory_id

    await file.update()


async def _rewrite_directory_tree_path(user: User, directory: Directory, new_path: Path) -> None:
    """Update a directory path and its descendant paths after rename or move."""

    old_path: Path = Path(directory.path)

    if old_path == new_path:
        return

    all_directories: list[Directory] = await Directory.get_all(user.username)
    descendants: list[tuple[Directory, Path]] = []

    for candidate in all_directories:
        if candidate.id == directory.id or candidate.username != user.username:
            continue

        try:
            relative_path: Path = Path(candidate.path).relative_to(old_path)

        except ValueError:
            continue

        if relative_path != Path("."):
            descendants.append((candidate, relative_path))

    directory.path = new_path
    await directory.update()

    for child, relative_path in sorted(descendants, key=lambda entry: len(entry[1].parts), reverse=True):
        child.path = new_path / relative_path
        await child.update()


@router.patch("/file/{fid}/rename")
async def rename_file(fid: int, name: str = Query(..., min_length=1, max_length=255), user: User = Depends(get_current_user)) -> JSONResponse:
    """Rename a file owned by the authenticated user."""

    file: File | None = await File.get(user.username, fid=fid)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    clean_name: str = _clean_name(name, "file")

    if clean_name != file.name:
        same_directory_files: list[File] = await File.get_all(user.username, directory_id=file.directory_id)

        if any(candidate.id != file.id and candidate.name == clean_name for candidate in same_directory_files):
            raise HTTPException(status_code=409, detail="A file with that name already exists in this directory")

        await _update_file_fields(file, name=clean_name)

    return JSONResponse({"message": "File renamed successfully", "id": fid, "name": file.name})


@router.patch("/directory/{did}/rename")
async def rename_directory(did: int, name: str = Query(..., min_length=1, max_length=255), user: User = Depends(get_current_user), ) -> JSONResponse:
    """Rename a directory using the shared path-rewrite helper."""

    directory: Directory | None = await Directory.get(user.username, did=did)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    old_path: Path = Path(directory.path)

    if old_path == Path("/home"):
        raise HTTPException(status_code=400, detail="The home directory cannot be renamed")

    clean_name: str = _clean_name(name, "directory")
    new_path: Path = old_path.with_name(clean_name)

    if new_path == old_path:
        return JSONResponse({
            "message": "Directory name unchanged",
            "id": did,
            "name": clean_name,
            "path": str(old_path),
        })

    existing: Directory | None = await Directory.get(user.username, path=new_path)

    if existing is not None and existing.id != did:
        raise HTTPException(status_code=409, detail="A folder with that name already exists")

    await _rewrite_directory_tree_path(user, directory, new_path)
    return JSONResponse({
        "message": "Directory renamed successfully",
        "id": did,
        "name": clean_name,
        "path": str(new_path),
    })


async def _resolve_move_destination(user: User, directory_path: str) -> Directory:
    """Find an existing destination directory owned by the authenticated user."""

    destination: Directory | None = await Directory.get(user.username, path=Path(directory_path))

    if destination is None or destination.username != user.username:
        raise HTTPException(status_code=404, detail="Destination directory not found")

    return destination


async def _move_file_to_directory(user: User, fid: int, destination: Directory) -> dict[str, object]:
    """Move one owned file to a destination, shared by single and bulk routes."""

    file: File | None = await File.get(user.username, fid=fid)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    if file.directory_id == destination.id:
        return {
            "status": "unchanged", "type": "file", "id": fid, "name": file.name,
            "message": "File is already in that directory", "directory": str(destination.path),
        }

    destination_files: list[File] = await File.get_all(user.username, directory_id=destination.id)

    if any(candidate.id != file.id and candidate.name == file.name for candidate in destination_files):
        raise HTTPException(status_code=409, detail=f'A file named "{file.name}" already exists in the destination')

    await _update_file_fields(file, directory_id=destination.id)
    return {
        "status": "moved", "type": "file", "id": fid, "name": file.name,
        "message": "File moved successfully", "directory_id": destination.id,
        "directory": str(destination.path),
    }


async def _move_directory_to_destination(user: User, did: int, destination: Directory) -> dict[str, object]:
    """Move one owned directory, preserving descendant paths."""
    source: Directory | None = await Directory.get(user.username, did=did)

    if source is None or source.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    old_path: Path = Path(source.path)

    if old_path == Path("/home"):
        raise HTTPException(status_code=400, detail="The home directory cannot be moved")

    destination_path: Path = Path(destination.path)

    try:
        destination_path.relative_to(old_path)

    except ValueError:
        pass

    else:
        raise HTTPException(status_code=400, detail="A folder cannot be moved into itself or one of its descendants")

    new_path: Path = destination_path / old_path.name

    if new_path == old_path:
        return {
            "status": "unchanged", "type": "folder", "id": did, "name": old_path.name,
            "message": "Directory is already in that location", "path": str(old_path),
        }

    existing: Directory | None = await Directory.get(user.username, path=new_path)

    if existing is not None and existing.id != did:
        raise HTTPException(status_code=409, detail=f'A folder named "{new_path.name}" already exists in the destination')

    await _rewrite_directory_tree_path(user, source, new_path)
    return {
        "status": "moved", "type": "folder", "id": did, "name": new_path.name,
        "message": "Directory moved successfully", "path": str(new_path),
    }


@router.patch("/file/{fid}/move")
async def move_file(fid: int, directory: str = Query(..., min_length=1), user: User = Depends(get_current_user)) -> JSONResponse:
    """Move a file to an existing destination directory."""

    destination: Directory = await _resolve_move_destination(user, directory)
    result: dict[str, object] = await _move_file_to_directory(user, fid, destination)
    return JSONResponse(result)


@router.patch("/directory/{did}/move")
async def move_directory(did: int, directory: str = Query(..., min_length=1), user: User = Depends(get_current_user)) -> JSONResponse:
    """Move a directory and preserve the paths of its descendants."""

    destination: Directory = await _resolve_move_destination(user, directory)
    result: dict[str, object] = await _move_directory_to_destination(user, did, destination)
    return JSONResponse(result)


@router.patch("/move")
async def move_items(directory: str = Body(...), items: list[dict[str, object]] = Body(...), user: User = Depends(get_current_user)) -> JSONResponse:
    """Move files and folders using decomposed request-body arguments."""

    if not items:
        raise HTTPException(status_code=422, detail="Select at least one file or folder to move")

    validated_items: list[tuple[str, int]] = []

    for item in items:
        item_type: object = item.get("type")
        item_id: object = item.get("id")

        if not isinstance(item_type, str) or item_type not in {"file", "folder"}:
            raise HTTPException(status_code=422, detail="Each item must have type 'file' or 'folder'")

        if isinstance(item_id, bool) or not isinstance(item_id, (int, str)):
            raise HTTPException(status_code=422, detail="Each item must have an integer id")

        try:
            parsed_id: int = int(item_id)

        except ValueError as error:
            raise HTTPException(status_code=422, detail="Each item must have an integer id") from error

        validated_items.append((item_type, parsed_id))

    destination: Directory = await _resolve_move_destination(user, directory)
    moved: list[dict[str, object]] = []
    unchanged: list[dict[str, object]] = []
    failed: list[dict[str, object]] = []
    seen: set[tuple[str, int]] = set()

    for item_type, item_id in validated_items:
        identity: tuple[str, int] = (cast(str, item_type), cast(int, item_id))

        if identity in seen:
            continue

        seen.add(identity)

        try:
            result: dict[str, object] = await (
                _move_file_to_directory(user, cast(int, item_id), destination)
                if item_type == "file"
                else _move_directory_to_destination(user, cast(int, item_id), destination)
            )

            if result.get("status") == "unchanged":
                unchanged.append(result)

            else:
                moved.append(result)

        except HTTPException as error:
            failed.append({
                "type": item_type,
                "id": item_id,
                "error": error.detail,
                "status_code": error.status_code,
            })

    return JSONResponse({
        "message": "Bulk move completed",
        "destination": str(destination.path),
        "moved": moved,
        "unchanged": unchanged,
        "failed": failed,
    })


@router.delete("/file/{fid}")
async def file_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Move an owned file to the user’s trash."""

    file: File | None = await File.get(user.username, fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    await file.move_to_trash()
    return JSONResponse({"message": "File moved to trash"})


@router.delete("/directory/{did}")
async def directory_delete(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Move an owned directory to the user’s trash."""

    directory: Directory | None = await Directory.get(user.username, did=did)

    if directory is None:
        raise HTTPException(status_code=404, detail="Directory not found")

    if directory.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    await directory.move_to_trash()
    return JSONResponse({"message": "Directory moved to trash"})


@router.delete("/trash/file/{fid}")
async def permanently_delete(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Permanently delete a file already in the user’s trash."""

    file: File | None = await File.get(user.username, fid=fid, trash=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.delete()
    return JSONResponse({"message": "Permanently deleted"})


async def _permanently_delete_directory_tree(user: User, directory: Directory) -> None:
    """Permanently delete all files and subdirectories regardless of their individual trash state."""

    if directory.id is None:
        raise ValueError("Directory has no ID")

    files: list[File] = [
        *await File.get_all(user.username, directory_id=directory.id),
        *await File.get_all(user.username, directory_id=directory.id, trash=True),
    ]

    for file in files:
        await file.delete()

    active_children: list[Directory] = await Directory.get_all(user.username, directory=directory.path)
    trashed_children: list[Directory] = await Directory.get_all(user.username, directory=directory.path, trash=True)
    children_by_id: dict[int, Directory] = {child.id: child for child in (*active_children, *trashed_children) if child.id is not None}

    for child in children_by_id.values():
        await _permanently_delete_directory_tree(user, child)

    await directory.delete()


@router.delete("/trash/directory/{did}")
async def permanently_delete_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Delete a trashed directory and recursively delete its contents."""

    directory: Directory | None = await Directory.get(user.username, did=did, trash=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    await _permanently_delete_directory_tree(user, directory)
    return JSONResponse({"message": "Permanently deleted"})


@router.post("/trash/file/{fid}/restore")
async def restore_file(fid: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Restore a file from the user’s trash."""

    file: File | None = await File.get(user.username, fid=fid, trash=True)

    if file is None or file.username != user.username:
        raise HTTPException(status_code=404, detail="File not found")

    await file.restore()
    return JSONResponse({"message": "Restored"})


@router.post("/trash/directory/{did}/restore")
async def restore_directory(did: int, user: User = Depends(get_current_user)) -> JSONResponse:
    """Restore a directory from the user’s trash."""

    directory: Directory | None = await Directory.get(user.username, did=did, trash=True)

    if directory is None or directory.username != user.username:
        raise HTTPException(status_code=404, detail="Directory not found")

    await directory.restore()
    return JSONResponse({"message": "Restored"})


@router.post("/file/{fid}/public-link")
async def create_public_link(fid: int, user: User = Depends(get_current_user)) -> dict[str, str]:
    """Create a signed public stream token for an owned file."""

    file: File | None = await File.get(user.username, fid=fid)

    if file is None:
        raise HTTPException(status_code=404, detail="File not found")

    if file.username != user.username:
        raise HTTPException(status_code=403, detail="Access denied")

    return {"public_token": create_public_stream_token(file=file, username=file.username)}
