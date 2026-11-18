from base64 import b64encode

from aiohttp import ClientSession

from backend.database import github_cursor_get_repo_id, github_cursor_get_used, github_cursor_increment_repo_id, github_cursor_set_used
from core.config import getenv
from core.data_center import ConfigMeta, DataCenter
from core.utils import write_log


class GitHub(DataCenter, metaclass=ConfigMeta):
    NAME: str = "GitHub"
    TOKEN: str = getenv("GITHUB_TOKEN")
    USERNAME: str = getenv("GITHUB_USERNAME")
    API: str = "https://api.github.com"
    MAX_REPO_SIZE: int = 5 * 1024 * 1024 * 1024

    @staticmethod
    async def upload(chunk: bytes, filename: str) -> str:
        repo_id: int = github_cursor_get_repo_id()
        used: int = github_cursor_get_used()
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GitHub.TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
        }

        # Create the first repository or move to the next repository.
        if repo_id == 0 or used + len(chunk) > GitHub.MAX_REPO_SIZE:
            github_cursor_increment_repo_id()
            github_cursor_set_used(0)
            repo_id: int = github_cursor_get_repo_id()
            name: str = f"storage-repo-{repo_id:02d}"

            async with ClientSession(headers=headers) as session:
                async with session.post(f"{GitHub.API}/user/repos", json={"name": name, "private": True, "auto_init": False}) as response:
                    if response.status != 201:
                        write_log(
                            "ERROR", GitHub, "REPO", "",
                            f"Failed to create repository '{name}': HTTP {response.status}: {await response.text()}",
                        )
                        raise OSError(f"GitHub repository creation failed: HTTP {response.status}")

        repo: str = f"storage-repo-{repo_id:02d}"

        async with ClientSession(headers=headers) as session:
            async with session.put(
                    f"{GitHub.API}/repos/{GitHub.USERNAME}/{repo}/contents/{filename}",
                    json={"message": f"Upload {filename}", "content": b64encode(chunk).decode("ascii")},
            ) as response:
                if response.status not in (200, 201):
                    write_log(
                        "ERROR", GitHub, "UPLOAD", "",
                        f"Failed to upload '{filename}': HTTP {response.status}: {await response.text()}",
                    )
                    raise OSError(f"GitHub upload failed: HTTP {response.status}")

                data = await response.json()

        github_cursor_set_used(used + len(chunk))
        return data["content"]["download_url"]

    @staticmethod
    async def download(flink: str) -> bytes:
        headers = {"Authorization": f"Bearer {GitHub.TOKEN}"}

        async with ClientSession() as session:
            async with session.get(flink, headers=headers) as response:
                if response.status != 200:
                    write_log(
                        "ERROR", GitHub, "DOWNLOAD", "",
                        f"Failed to download '{flink}': HTTP {response.status}: {await response.text()}",
                    )
                    raise OSError(f"GitHub download failed: HTTP {response.status}")

                return await response.read()
