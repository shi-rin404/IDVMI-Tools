import os
import re


GAME_DIR_RE = re.compile(r"^[A-Z]:\\Loading Bay Games\\Identity V\\?$", re.IGNORECASE)


def _iter_drive_roots():
    for drive_ord in range(ord("A"), ord("Z") + 1):
        yield f"{chr(drive_ord)}:\\"


def _find_game_directory():
    for drive_root in _iter_drive_roots():
        game_dir = os.path.join(drive_root, "Loading Bay Games", "Identity V")
        if os.path.isdir(game_dir) and GAME_DIR_RE.match(game_dir):
            return game_dir
    return None

def check_game_directory():
    game_dir = _find_game_directory()
    if game_dir:
        mod_dir = os.path.join(game_dir, "Documents", "res", "mod")
        try:
            os.makedirs(mod_dir, exist_ok=True)
        except OSError:
            return ""
        return mod_dir
    return ""
