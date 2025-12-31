"""
GitHub Uploader - Uploads audio files to GitHub Releases.
"""

import os
from typing import Optional

from github import Github, GithubException

from config import GITHUB_TOKEN, GITHUB_REPO


# Release tag for audio files
AUDIO_RELEASE_TAG = "audio"
AUDIO_RELEASE_NAME = "Podcast Audio Files"


def get_github_client() -> Optional[Github]:
    """Get authenticated GitHub client."""
    if not GITHUB_TOKEN:
        print("Warning: GITHUB_TOKEN not set")
        return None
    return Github(GITHUB_TOKEN)


def get_or_create_release(repo) -> Optional[object]:
    """Get or create the audio release."""
    try:
        # Try to get existing release
        release = repo.get_release(AUDIO_RELEASE_TAG)
        return release
    except GithubException:
        pass

    # Create new release
    try:
        release = repo.create_git_release(
            tag=AUDIO_RELEASE_TAG,
            name=AUDIO_RELEASE_NAME,
            message="Audio files for Research Radio podcast episodes",
            draft=False,
            prerelease=False
        )
        print(f"Created new release: {AUDIO_RELEASE_TAG}")
        return release
    except GithubException as e:
        print(f"Error creating release: {e}")
        return None


def upload_audio_to_release(audio_path: str) -> bool:
    """
    Upload an audio file to the GitHub release.

    Args:
        audio_path: Path to the local audio file

    Returns:
        True if successful, False otherwise
    """
    if not os.path.exists(audio_path):
        print(f"Audio file not found: {audio_path}")
        return False

    gh = get_github_client()
    if not gh:
        return False

    try:
        repo = gh.get_repo(GITHUB_REPO)
    except GithubException as e:
        print(f"Error accessing repo {GITHUB_REPO}: {e}")
        return False

    release = get_or_create_release(repo)
    if not release:
        return False

    filename = os.path.basename(audio_path)

    # Check if asset already exists
    try:
        for asset in release.get_assets():
            if asset.name == filename:
                print(f"Asset {filename} already exists. Deleting old version...")
                asset.delete_asset()
                break
    except GithubException:
        pass

    # Upload new asset
    try:
        print(f"Uploading {filename} to GitHub release...")
        asset = release.upload_asset(
            audio_path,
            content_type='audio/mpeg',
            name=filename
        )
        print(f"Upload complete: {asset.browser_download_url}")
        return True
    except GithubException as e:
        print(f"Error uploading asset: {e}")
        return False


def get_release_asset_url(filename: str) -> Optional[str]:
    """Get the download URL for an asset in the release."""
    gh = get_github_client()
    if not gh:
        return None

    try:
        repo = gh.get_repo(GITHUB_REPO)
        release = repo.get_release(AUDIO_RELEASE_TAG)

        for asset in release.get_assets():
            if asset.name == filename:
                return asset.browser_download_url

    except GithubException:
        pass

    return None
