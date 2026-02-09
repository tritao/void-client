#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
from pathlib import Path


def _run_git(repo: Path, args: list[str], *, capture: bool = False) -> subprocess.CompletedProcess[str]:
    cmd = ["git", "-C", str(repo), *args]
    return subprocess.run(cmd, check=False, text=True, capture_output=capture)


def _ensure_repo_initialized(repo: Path, *, user_name: str, user_email: str) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    git_dir = repo / ".git"
    if not git_dir.exists():
        init_proc = _run_git(repo, ["init"])
        if init_proc.returncode != 0:
            raise RuntimeError(f"git init failed in {repo}")
        print(f"Initialized git repo: {repo}")

    name_proc = _run_git(repo, ["config", "--get", "user.name"], capture=True)
    email_proc = _run_git(repo, ["config", "--get", "user.email"], capture=True)
    current_name = (name_proc.stdout or "").strip() if name_proc.returncode == 0 else ""
    current_email = (email_proc.stdout or "").strip() if email_proc.returncode == 0 else ""

    if not current_name:
        set_name_proc = _run_git(repo, ["config", "user.name", user_name])
        if set_name_proc.returncode != 0:
            raise RuntimeError(f"git config user.name failed in {repo}")
    if not current_email:
        set_email_proc = _run_git(repo, ["config", "user.email", user_email])
        if set_email_proc.returncode != 0:
            raise RuntimeError(f"git config user.email failed in {repo}")

    _run_git(repo, ["config", "commit.gpgsign", "false"])


def _commit_if_dirty(repo: Path, *, message: str) -> bool:
    status_proc = _run_git(repo, ["status", "--porcelain"], capture=True)
    if status_proc.returncode != 0:
        raise RuntimeError(f"git status failed in {repo}")
    if not (status_proc.stdout or "").strip():
        print(f"No changes to commit for: {message}")
        return False

    add_proc = _run_git(repo, ["add", "-A"])
    if add_proc.returncode != 0:
        raise RuntimeError(f"git add failed in {repo}")

    staged_proc = _run_git(repo, ["diff", "--cached", "--name-only"], capture=True)
    if staged_proc.returncode != 0:
        raise RuntimeError(f"git diff --cached failed in {repo}")
    if not (staged_proc.stdout or "").strip():
        print(f"No staged changes after add for: {message}")
        return False

    commit_proc = _run_git(repo, ["commit", "--no-gpg-sign", "-m", message])
    if commit_proc.returncode != 0:
        raise RuntimeError(f"git commit failed in {repo} for message: {message}")
    print(f"Committed: {message}")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Initialize and commit per-step changes for client/refactor workflow.")
    parser.add_argument("--repo", type=Path, required=True, help="Path to git repository root")
    parser.add_argument("--init", action="store_true", help="Initialize repo if missing and configure identity")
    parser.add_argument("--commit-message", default="", help="If provided, create commit when repo is dirty")
    parser.add_argument("--user-name", default="Refactor Bot")
    parser.add_argument("--user-email", default="refactor-bot@local")
    args = parser.parse_args()

    repo = args.repo.resolve()
    if args.init or args.commit_message:
        _ensure_repo_initialized(repo, user_name=args.user_name, user_email=args.user_email)

    if args.commit_message:
        _commit_if_dirty(repo, message=args.commit_message)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
