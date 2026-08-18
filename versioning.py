"""
Tự động tính "version" cho bot mỗi lần deploy/khởi động lại, dựa trên SỐ FILE
MÃ NGUỒN (.py) thay đổi so với lần lưu trước trong Firebase (hash SHA-256
từng file). Không phải semver chuẩn - chỉ để có 1 con số tăng dần theo mức độ
thay đổi, hiển thị trên web dashboard:

  - Đổi NHIỀU file (>= VERSION_MANY_FILES_THRESHOLD, mặc định 5)  -> +0.5
  - Đổi VÀI file  (>= VERSION_FEW_FILES_THRESHOLD, mặc định 2)    -> +0.1
  - Đổi 1 file                                                    -> +0.01
  - Không đổi gì (hash y hệt lần trước, vd. bot chỉ restart chay) -> giữ nguyên

Ví dụ: 1.0 -> (sửa 6 file) -> 1.5 -> (sửa 2 file) -> 1.6 -> (sửa 1 file) -> 1.61
"""

import hashlib
import os
import time

import db
from config import log

# Chỉ tính diff trên file .py (mã nguồn thật sự) - bỏ qua asset (font, ảnh),
# cache, venv... vì đổi mấy thứ đó không phải "cập nhật tính năng/code".
_TRACKED_EXTENSIONS = (".py",)
_IGNORED_DIRS = {"__pycache__", ".git", "assets", "venv", ".venv", "node_modules", ".idea", ".vscode"}

VERSION_MANY_FILES_THRESHOLD = int(os.environ.get("VERSION_MANY_FILES_THRESHOLD", "5"))
VERSION_FEW_FILES_THRESHOLD = int(os.environ.get("VERSION_FEW_FILES_THRESHOLD", "2"))

VERSION_BUMP_MANY = 0.5
VERSION_BUMP_FEW = 0.1
VERSION_BUMP_TINY = 0.01

DEFAULT_START_VERSION = 1.0


def _scan_source_hashes(root: str = ".") -> dict[str, str]:
    """Trả về {đường dẫn tương đối: sha256 nội dung} cho toàn bộ file .py
    trong thư mục project (đệ quy), bỏ qua các thư mục trong _IGNORED_DIRS."""
    hashes: dict[str, str] = {}
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORED_DIRS and not d.startswith(".")]
        for fname in filenames:
            if not fname.endswith(_TRACKED_EXTENSIONS):
                continue
            full_path = os.path.join(dirpath, fname)
            rel_path = os.path.relpath(full_path, root)
            try:
                with open(full_path, "rb") as f:
                    hashes[rel_path] = hashlib.sha256(f.read()).hexdigest()
            except OSError:
                continue
    return hashes


def _bump_amount(changed_count: int) -> float:
    if changed_count >= VERSION_MANY_FILES_THRESHOLD:
        return VERSION_BUMP_MANY
    if changed_count >= VERSION_FEW_FILES_THRESHOLD:
        return VERSION_BUMP_FEW
    return VERSION_BUMP_TINY


async def check_and_bump_version(root: str = ".") -> dict:
    """Gọi 1 LẦN lúc bot khởi động (on_ready). So sánh hash file .py hiện tại
    với lần lưu trước trong Firebase (bot_state), tự tăng version nếu có file
    đổi/thêm/xoá. Trả về {"version": float, "bumped": bool, "changed_files": int}."""
    state = await db.get_bot_state()
    old_version = float(state.get("version") or DEFAULT_START_VERSION)
    old_hashes = state.get("version_file_hashes") or {}

    new_hashes = _scan_source_hashes(root)

    changed = [p for p, h in new_hashes.items() if old_hashes.get(p) != h]
    removed = [p for p in old_hashes if p not in new_hashes]
    changed_count = len(changed) + len(removed)

    if not old_hashes:
        # Lần đầu tiên bật tính năng version tracking (chưa có hash cũ để so
        # sánh) -> chỉ lưu mốc ban đầu, không bump để tránh nhảy version ảo.
        await db.save_bot_state({
            "version": old_version,
            "version_file_hashes": new_hashes,
            "version_updated_at": int(time.time()),
        })
        return {"version": old_version, "bumped": False, "changed_files": 0}

    if changed_count == 0:
        return {"version": old_version, "bumped": False, "changed_files": 0}

    bump = _bump_amount(changed_count)
    new_version = round(old_version + bump, 2)

    await db.save_bot_state({
        "version": new_version,
        "version_file_hashes": new_hashes,
        "version_updated_at": int(time.time()),
        "version_last_changed_files": changed_count,
    })
    log.info(
        "Version bot: %.2f -> %.2f (%d file thay đổi: %s)",
        old_version, new_version, changed_count,
        ", ".join((changed + removed)[:10]),
    )
    return {
        "version": new_version,
        "bumped": True,
        "changed_files": changed_count,
        "changed_paths": changed,
        "removed_paths": removed,
    }
