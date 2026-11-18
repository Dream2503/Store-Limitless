from base64 import b64encode

from aiohttp import ClientSession

from backend.database.models import GitHubCursor
from core.data_center import ConfigMeta, DataCenter
from core.utils import Progress, getenv, write_log


class GitHub(DataCenter, metaclass=ConfigMeta):
    NAME: str = "GitHub"
    TOKEN: str = getenv("GITHUB_TOKEN")
    USERNAME: str = getenv("GITHUB_USERNAME")
    API: str = "https://api.github.com"
    MAX_REPO_SIZE: int = 5 * 1024 * 1024 * 1024

    @staticmethod
    async def upload(chunk: bytes, filename: str, progress: Progress) -> str:
        return ""
        cursor: GitHubCursor = GitHubCursor.get()
        headers: dict[str, str] = {
            "Accept": "application/vnd.github+json",
            "Authorization": f"Bearer {GitHub.TOKEN}",
            "X-GitHub-Api-Version": "2026-03-10",
        }

        if cursor.repo_id == 0 or cursor.used + len(chunk) > GitHub.MAX_REPO_SIZE:
            cursor.increment_repo_id()
            cursor.set_used(0)
            name: str = f"storage-repo-{cursor.repo_id:02d}"

            async with ClientSession(headers=headers) as session:
                async with session.post(f"{GitHub.API}/user/repos", json={"name": name, "private": True, "auto_init": False}) as response:
                    if response.status != 201:
                        write_log("ERROR", GitHub, "REPO", "",
                                  f"Failed to create repository '{name}': HTTP {response.status}: {await response.text()}")
                        raise OSError(f"GitHub repository creation failed: HTTP {response.status}")

        repo: str = f"storage-repo-{cursor.repo_id:02d}"

        async with ClientSession(headers=headers) as session:
            async with session.put(
                    f"{GitHub.API}/repos/{GitHub.USERNAME}/{repo}/contents/{filename}",
                    json={
                        "message": f"Upload {filename}",
                        "content": b64encode(chunk).decode("ascii"),
                    },
            ) as response:
                if response.status not in (200, 201):
                    write_log("ERROR", GitHub, "UPLOAD", "", f"Failed to upload '{filename}': HTTP {response.status}: {await response.text()}", )
                    raise OSError(f"GitHub upload failed: HTTP {response.status}")

                data = await response.json()

        cursor.set_used(cursor.used + len(chunk))
        return data["content"]["download_url"]

    @staticmethod
    async def download(flink: str) -> bytes:
        return bytes()
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
