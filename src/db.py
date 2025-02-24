from git import Repo
import os
import logging
import shutil
import aiofiles
import asyncio
from pathlib import Path
import base64

logger = logging.getLogger("uvicorn")
data_folder = os.environ["LOCAL_REPO_FOLDER"]

PUSH_DUE: bool = False

if os.path.isdir(os.environ["LOCAL_REPO_FOLDER"]):
    shutil.rmtree(os.environ["LOCAL_REPO_FOLDER"])

async def push_loop():
    global PUSH_DUE
    while True:
        await asyncio.sleep(int(os.environ["PUSH_EVERY_N_SECONDS"]))
        if PUSH_DUE:
            push()
            PUSH_DUE = False
        else:
            logger.info("skipping PUSH")

if asyncio.get_event_loop().is_running():
    asyncio.create_task(push_loop())



def setup_ssh_key():
    # Get the base64 encoded private key from environment variable
    private_key = os.getenv('PRIVATE_KEY')
    if private_key[-1] != '\n':
        private_key += '\n'

    
    # Create .ssh directory
    ssh_dir = Path.home() / '.ssh'
    ssh_dir.mkdir(mode=0o700, exist_ok=True)
    
    # Write the private key to a file
    key_path = ssh_dir / 'id_rsa'
    key_path.write_text(private_key)
    
    # Set correct permissions
    key_path.chmod(0o600)
    
    # Create known_hosts file if it doesn't exist
    known_hosts = ssh_dir / 'known_hosts'
    if not known_hosts.exists():
        known_hosts.touch(mode=0o600)
        
    # Add github.com to known hosts
    os.system('ssh-keyscan -t rsa github.com >> ~/.ssh/known_hosts')

    # Debug: Print the first few characters of the key (safely)
    print("First few chars of key:", private_key[:50])
    # Debug: Check file permissions
    print("Key file permissions:", oct(key_path.stat().st_mode)[-3:])

setup_ssh_key()


if os.environ.get("SKIP_REMOTE_CONNECTION", False):
    repo = Repo.init(os.environ["LOCAL_REPO_FOLDER"])
    logger.info("init repo")
else:
    repu_url = os.environ["REPO_URL"]
    if "${REPO}" in repu_url:
        repu_url = repu_url.replace("${REPO}", os.environ["REPO"])
    if "${GITHUB_PAT}" in repu_url:
        repu_url = repu_url.replace("${GITHUB_PAT}", os.environ["GITHUB_PAT"])
    logger.info("cloning repo: " + repu_url)

    repo = Repo.clone_from(repu_url, os.environ["LOCAL_REPO_FOLDER"])

logger.info("setting username and email")
repo.config_writer().set_value("user", "name", os.environ["USER_NAME"]).release()
repo.config_writer().set_value("user", "email", os.environ["USER_EMAIL"]).release()


async def get_data(key: str) -> bytes:
    check_valid_path(key)
    async with aiofiles.open(os.path.join(data_folder, key), "rb") as f:
        data = await f.read()
    return data


async def set_data(key: str, data: bytes) -> None:
    path = os.path.join(data_folder, key)
    is_new_file = not os.path.isfile(path)
    async with aiofiles.open(path, "wb+") as f:
        await f.write(data)
    commit(
        file_path=key,
        commit_message=("new" if is_new_file else "edit") + f": {key}",
    )
    global PUSH_DUE
    PUSH_DUE = True


async def delete_data(key: str) -> None:
    check_valid_path(key)
    os.remove(os.path.join(data_folder, key))

    commit(
        file_path=None,
        commit_message=f"deleted: {key}"
    )
    global PUSH_DUE
    PUSH_DUE = True


async def list_data() -> list[str]:
    return [entry for entry in os.listdir(data_folder)]


def check_valid_path(key: str):
    if not os.path.isfile(os.path.join(data_folder, key)):
        raise FileNotFoundError(f"File {os.path.join(data_folder, key)} not found")


def commit(file_path: str | None, commit_message: str):
    if file_path is not None:
        repo.index.add([file_path])
    else:
        repo.git.add(u=True)
    logger.info(f"committing {commit_message}")
    repo.index.commit(commit_message)


def push():
    try:
        origin = repo.remote(name='origin')
    except ValueError:
        logger.info("Skipping due to missing remote origin")
    origin.push()
