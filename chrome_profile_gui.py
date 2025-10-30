#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chrome Profile Transfer GUI (Tkinter) — v1.3
---------------------------------------------
Fixes & improvements:
- Restore: luôn tạo thư mục "Profile N" tiếp theo (Profile 1, 2, ...).
- Ghi tên hiển thị vào cả:
    * Local State -> profile.info_cache[folder].name
    * Preferences (trong thư mục profile) -> profile.name
- Cập nhật profile.last_used và last_active_profiles.
- Xử lý trường hợp file .zip có cấu trúc lồng 1 thư mục gốc.
- Tìm và mở Chrome theo nhiều biến thể (Stable/Beta/Canary/Linux).
- Tăng độ an toàn khi ghi Local State (backup, thử lại nếu bị khoá).

Lưu ý: mật khẩu/cookies có thể không hoạt động giữa máy khác do cơ chế mã hoá OS/user.
"""

import json
import os
import platform
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox

APP_TITLE = "Chrome Profile Transfer (Tkinter) v1.3"
DEFAULT_ZIP_NAME = "chrome_profile_backup.zip"

def chrome_process_names():
    system = platform.system()
    if system == "Windows":
        return ["chrome.exe", "chrome", "googleupdate.exe"]
    elif system == "Darwin":
        return ["Google Chrome", "Google Chrome Helper"]
    else:
        return ["chrome", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

def kill_chrome(logfn=print):
    system = platform.system()
    names = chrome_process_names()
    try:
        if system == "Windows":
            for n in names:
                subprocess.run(["taskkill", "/F", "/IM", n], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            for n in names:
                subprocess.run(["pkill", "-f", n], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        logfn("Đã cố gắng đóng Chrome (nếu đang chạy).")
    except Exception as e:
        logfn(f"Lỗi khi đóng Chrome: {e}")
    time.sleep(1.2)

def user_profile_base():
    user_home = Path.home()
    system = platform.system()
    if system == "Windows":
        localapp = os.environ.get("LOCALAPPDATA", str(user_home / "AppData" / "Local"))
        return Path(localapp) / "Google" / "Chrome" / "User Data"
    elif system == "Darwin":
        return user_home / "Library" / "Application Support" / "Google" / "Chrome"
    else:
        return user_home / ".config" / "google-chrome"

def local_state_path():
    return user_profile_base() / "Local State"

def read_json(path: Path, default=None):
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {} if default is None else default

def write_json_atomic(path: Path, obj, logfn=print):
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        txt = json.dumps(obj, ensure_ascii=False, indent=2)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(txt, encoding="utf-8")
        tmp.replace(path)
        return True
    except Exception as e:
        logfn(f"Lỗi ghi JSON: {e}")
        return False

def read_profile_display_names():
    path = local_state_path()
    mapping = {}
    try:
        if path.exists():
            data = json.loads(path.read_text(encoding="utf-8"))
            info_cache = data.get("profile", {}).get("info_cache", {})
            for folder, meta in info_cache.items():
                disp = meta.get("name") or meta.get("shortcut_name") or folder
                mapping[folder] = disp
    except Exception:
        pass
    return mapping

def list_profiles_with_names():
    base = user_profile_base()
    name_map = read_profile_display_names()
    results = []
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile ")):
                results.append((p.name, name_map.get(p.name, p.name)))
    def sort_key(t):
        folder = t[0]
        if folder == "Default":
            return (0, 0)
        try:
            num = int(folder.split(" ")[1])
        except Exception:
            num = 99999
        return (1, num)
    return sorted(results, key=sort_key)

def next_profile_folder_name():
    base = user_profile_base()
    max_n = 0
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and p.name.startswith("Profile "):
                try:
                    n = int(p.name.split(" ")[1])
                    if n > max_n:
                        max_n = n
                except Exception:
                    pass
    return f"Profile {max_n + 1}"

def default_profile_path(profile_folder="Default"):
    return user_profile_base() / profile_folder

def zip_folder(src_folder: Path, out_zip: Path, logfn=print):
    with zipfile.ZipFile(out_zip, 'w', zipfile.ZIP_DEFLATED) as zf:
        for root, dirs, files in os.walk(src_folder):
            for f in files:
                full = Path(root) / f
                arcname = str(full.relative_to(src_folder))
                try:
                    zf.write(str(full), arcname)
                except Exception as e:
                    logfn(f"Bỏ qua file lỗi: {full} ({e})")

def unzip_to_folder(zip_path: Path, dest_folder: Path):
    with zipfile.ZipFile(zip_path, 'r') as zf:
        zf.extractall(dest_folder)

def flatten_if_wrapped(root: Path, logfn=print):
    """
    Nếu zip giải nén ra 1 thư mục con duy nhất (wrap), di chuyển
    toàn bộ nội dung thư mục con đó lên root.
    """
    try:
        items = list(root.iterdir())
        if len(items) == 1 and items[0].is_dir():
            inner = items[0]
            logfn(f"Phát hiện gói lồng: đang dỡ nội dung {inner.name} lên {root.name}")
            for p in inner.iterdir():
                shutil.move(str(p), str(root / p.name))
            inner.rmdir()
    except Exception as e:
        logfn(f"Không thể flatten zip: {e}")

def try_get_profile_name_from_preferences(profile_dir: Path):
    prefs_path = profile_dir / "Preferences"
    try:
        if prefs_path.exists():
            prefs = json.loads(prefs_path.read_text(encoding="utf-8"))
            prof = prefs.get("profile", {})
            name = prof.get("name")
            if isinstance(name, str) and name.strip():
                return name.strip()
    except Exception:
        pass
    return None

def ensure_preferences_display_name(profile_dir: Path, display_name: str, logfn=print):
    """Ghi 'profile.name' trong Preferences nếu khác tên mong muốn."""
    prefs_path = profile_dir / "Preferences"
    try:
        prefs = read_json(prefs_path, default={})
        prof = prefs.setdefault("profile", {})
        if prof.get("name") != display_name:
            prof["name"] = display_name
            # avatar_index mặc định nếu chưa có
            prof.setdefault("avatar_index", 26)
            tmp_ok = write_json_atomic(prefs_path, prefs, logfn)
            if tmp_ok:
                logfn("Đã cập nhật Preferences: profile.name = " + display_name)
    except Exception as e:
        logfn(f"Lỗi khi ghi Preferences: {e}")

def update_local_state_register_profile(folder_name: str, display_name: str, logfn=print):
    """Thêm/ghi đè entry info_cache cho profile mới và đặt last_used."""
    ls_path = local_state_path()
    # backup trước khi ghi
    try:
        if ls_path.exists():
            bak = ls_path.with_name(ls_path.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
            shutil.copy2(ls_path, bak)
    except Exception:
        pass

    data = read_json(ls_path, default={})
    prof = data.setdefault("profile", {})
    info_cache = prof.setdefault("info_cache", {})

    meta = info_cache.get(folder_name, {})
    meta["name"] = display_name
    meta.setdefault("gaia_name", "")
    meta.setdefault("user_name", "")
    info_cache[folder_name] = meta

    # last_used
    prof["last_used"] = folder_name

    # last_active_profiles: de-dup và đưa lên đầu
    lst = prof.get("last_active_profiles", [])
    if not isinstance(lst, list):
        lst = []
    lst = [x for x in lst if x != folder_name]
    lst.insert(0, folder_name)
    prof["last_active_profiles"] = lst

    # ghi atomic
    if write_json_atomic(ls_path, data, logfn):
        logfn(f"Đã cập nhật Local State cho '{folder_name}' (name='{display_name}').")
    else:
        logfn("Không thể ghi Local State.")

def find_chrome_candidates():
    system = platform.system()
    candidates = []
    if system == "Windows":
        candidates += [
            Path("C:/Program Files/Google/Chrome/Application/chrome.exe"),
            Path("C:/Program Files (x86)/Google/Chrome/Application/chrome.exe"),
            Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "Google/Chrome/Application/chrome.exe",
            Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "Google/Chrome/Application/chrome.exe",
        ]
    elif system == "Darwin":
        candidates += [
            Path("/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
            Path("/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta"),
            Path("/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary"),
        ]
    else:
        # Linux
        for cmd in ["google-chrome", "google-chrome-stable", "chrome", "chromium", "chromium-browser"]:
            candidates.append(cmd)
    return candidates

def launch_chrome_with_profile(folder_name: str, logfn=print):
    """Thử mở Chrome với profile chỉ định."""
    args = ['--profile-directory=' + folder_name]
    system = platform.system()
    try:
        candidates = find_chrome_candidates()
        if system == "Linux":
            for cmd in candidates:
                try:
                    subprocess.Popen([cmd, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    logfn(f"Đã thử mở Chrome ({cmd}) với profile mới.")
                    return
                except Exception:
                    continue
            logfn("Không tìm thấy lệnh Chrome để mở tự động.")
        else:
            for exe in candidates:
                if isinstance(exe, Path):
                    if exe.exists():
                        subprocess.Popen([str(exe), *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        logfn(f"Đã mở Chrome: {exe.name} với profile mới.")
                        return
                else:
                    # string path (unlikely here)
                    try:
                        subprocess.Popen([exe, *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        logfn(f"Đã mở Chrome ({exe}) với profile mới.")
                        return
                    except Exception:
                        pass
            # Fallback to PATH on Windows/macOS too
            subprocess.Popen(["chrome", *args], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            logfn("Đã thử mở Chrome qua PATH.")
    except Exception as e:
        logfn(f"Không mở được Chrome tự động: {e}")

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("940x680")
        self.minsize(840, 620)

        self.profile_items = []
        self.combo_value_to_folder = {}

        self.create_widgets()
        self.populate_profiles()

    def create_widgets(self):
        pad = {'padx': 10, 'pady': 6}

        nb = ttk.Notebook(self)
        nb.pack(fill="both", expand=True)

        # --- Export tab ---
        self.tab_export = ttk.Frame(nb)
        nb.add(self.tab_export, text="Export (Backup)")

        row = 0
        ttk.Label(self.tab_export, text="Chọn profile cần export:").grid(row=row, column=0, sticky="w", **pad)
        self.combo_profile = ttk.Combobox(self.tab_export, state="readonly", width=54)
        self.combo_profile.grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(self.tab_export, text="Refresh", command=self.populate_profiles).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(self.tab_export, text="Hoặc đường dẫn profile (tuỳ chọn):").grid(row=row, column=0, sticky="w", **pad)
        self.entry_src_path = ttk.Entry(self.tab_export)
        self.entry_src_path.grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(self.tab_export, text="Chọn...", command=self.pick_src_folder).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(self.tab_export, text="File .zip xuất ra:").grid(row=row, column=0, sticky="w", **pad)
        self.entry_zip_out = ttk.Entry(self.tab_export)
        self.entry_zip_out.grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(self.tab_export, text="Lưu thành...", command=self.pick_zip_out).grid(row=row, column=2, **pad)

        row += 1
        self.var_kill = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.tab_export, text="Tự đóng Chrome trước khi export", variable=self.var_kill).grid(row=row, column=1, sticky="w", **pad)

        row += 1
        ttk.Button(self.tab_export, text="Export (Backup)", command=self.run_export).grid(row=row, column=1, sticky="e", **pad)
        ttk.Button(self.tab_export, text="Mở thư mục chứa file", command=self.open_zip_folder).grid(row=row, column=2, **pad)

        for c in range(3):
            self.tab_export.grid_columnconfigure(c, weight=1)

        # --- Import tab ---
        self.tab_import = ttk.Frame(nb)
        nb.add(self.tab_import, text="Import (Restore)")

        row = 0
        ttk.Label(self.tab_import, text="Chọn file .zip profile:").grid(row=row, column=0, sticky="w", **pad)
        self.entry_zip_in = ttk.Entry(self.tab_import)
        self.entry_zip_in.grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(self.tab_import, text="Chọn...", command=self.pick_zip_in).grid(row=row, column=2, **pad)

        row += 1
        ttk.Label(self.tab_import, text="Tên hiển thị (tuỳ chọn, nếu để trống sẽ lấy từ Preferences hoặc dùng 'Profile N'):").grid(row=row, column=0, sticky="w", **pad)
        self.entry_display_name = ttk.Entry(self.tab_import)
        self.entry_display_name.grid(row=row, column=1, sticky="we", **pad)

        row += 1
        self.var_launch = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.tab_import, text="Mở Chrome ngay với profile vừa import", variable=self.var_launch).grid(row=row, column=1, sticky="w", **pad)

        row += 1
        self.var_kill2 = tk.BooleanVar(value=True)
        ttk.Checkbutton(self.tab_import, text="Tự đóng Chrome trước khi import", variable=self.var_kill2).grid(row=row, column=1, sticky="w", **pad)

        row += 1
        ttk.Button(self.tab_import, text="Import (Restore)", command=self.run_import).grid(row=row, column=1, sticky="e", **pad)

        for c in range(3):
            self.tab_import.grid_columnconfigure(c, weight=1)

        # --- Log area ---
        self.frame_log = ttk.LabelFrame(self, text="Log")
        self.frame_log.pack(fill="both", expand=True, padx=10, pady=(0,10))
        self.text_log = tk.Text(self.frame_log, height=12, wrap="word")
        self.text_log.pack(fill="both", expand=True, padx=8, pady=8)

    # Helpers
    def log(self, msg):
        self.text_log.insert("end", msg + "\n")
        self.text_log.see("end")
        self.update_idletasks()

    def populate_profiles(self):
        items = list_profiles_with_names()
        self.profile_items = items
        display_values = []
        self.combo_value_to_folder.clear()
        for folder, disp in items:
            show = f"{folder} — {disp}"
            display_values.append(show)
            self.combo_value_to_folder[show] = folder

        if not display_values:
            display_values = ["Default — (chưa tìm thấy tên hiển thị)"]
            self.combo_value_to_folder[display_values[0]] = "Default"

        self.combo_profile["values"] = display_values
        if not self.combo_profile.get():
            self.combo_profile.set(display_values[0])

        desktop = Path.home() / "Desktop"
        default_zip = desktop / DEFAULT_ZIP_NAME if desktop.exists() else Path.cwd() / DEFAULT_ZIP_NAME
        if not self.entry_zip_out.get():
            self.entry_zip_out.delete(0, "end")
            self.entry_zip_out.insert(0, str(default_zip))

    # File pickers
    def pick_src_folder(self):
        d = filedialog.askdirectory(title="Chọn thư mục profile nguồn (Default / Profile 1 / ...)")
        if d:
            self.entry_src_path.delete(0, "end")
            self.entry_src_path.insert(0, d)

    def pick_zip_out(self):
        f = filedialog.asksaveasfilename(title="Lưu file backup (.zip)",
                                         defaultextension=".zip",
                                         filetypes=[("Zip Archive", "*.zip")],
                                         initialfile=DEFAULT_ZIP_NAME)
        if f:
            self.entry_zip_out.delete(0, "end")
            self.entry_zip_out.insert(0, f)

    def open_zip_folder(self):
        path = Path(self.entry_zip_out.get().strip() or ".")
        folder = path.parent if path.suffix else path
        try:
            if platform.system() == "Windows":
                os.startfile(str(folder))
            elif platform.system() == "Darwin":
                subprocess.run(["open", str(folder)])
            else:
                subprocess.run(["xdg-open", str(folder)])
        except Exception as e:
            messagebox.showerror("Lỗi", f"Không mở được thư mục: {e}")

    def pick_zip_in(self):
        f = filedialog.askopenfilename(title="Chọn file backup (.zip)",
                                       filetypes=[("Zip Archive", "*.zip")])
        if f:
            self.entry_zip_in.delete(0, "end")
            self.entry_zip_in.insert(0, f)

    # Export/Import actions
    def run_export(self):
        t = threading.Thread(target=self._do_export, daemon=True)
        t.start()

    def run_import(self):
        t = threading.Thread(target=self._do_import, daemon=True)
        t.start()

    def _do_export(self):
        try:
            src_custom = self.entry_src_path.get().strip()
            if src_custom:
                src = Path(src_custom)
                chosen_label = "(tuỳ chọn)"
            else:
                chosen_display = self.combo_profile.get().strip()
                folder = self.combo_value_to_folder.get(chosen_display, "Default")
                src = default_profile_path(folder)
                chosen_label = chosen_display

            if not src.exists():
                messagebox.showerror("Lỗi", f"Không tìm thấy thư mục profile: {src}")
                return

            zip_out = Path(self.entry_zip_out.get().strip() or DEFAULT_ZIP_NAME)
            zip_out.parent.mkdir(parents=True, exist_ok=True)

            self.log(f"Profile nguồn: {src}")
            self.log(f"Đã chọn: {chosen_label}")
            self.log(f"File xuất: {zip_out}")

            if self.var_kill.get():
                self.log("Đang đóng Chrome...")
                kill_chrome(self.log)

            self.log("Đang nén profile, vui lòng đợi...")
            zip_folder(src, zip_out, self.log)
            self.log("Hoàn tất export!")

            messagebox.showinfo("Xong", f"Đã tạo file backup:\n{zip_out}")

        except Exception as e:
            self.log(f"Lỗi export: {e}")
            messagebox.showerror("Lỗi", f"Export thất bại:\n{e}")

    def _do_import(self):
        try:
            zip_in = Path(self.entry_zip_in.get().strip())
            if not zip_in.exists():
                messagebox.showerror("Lỗi", f"Không tìm thấy file zip: {zip_in}")
                return

            base = user_profile_base()
            base.mkdir(parents=True, exist_ok=True)

            if self.var_kill2.get():
                self.log("Đang đóng Chrome...")
                kill_chrome(self.log)

            folder_name = next_profile_folder_name()
            dest = base / folder_name

            self.log(f"File nhập: {zip_in}")
            self.log(f"Tạo profile mới: {folder_name}")
            self.log(f"Thư mục profile đích: {dest}")

            tmpdir = dest.with_name(dest.name + "_tmp_" + time.strftime("%Y%m%d_%H%M%S"))
            tmpdir.mkdir(parents=True, exist_ok=True)

            self.log("Đang giải nén...")
            unzip_to_folder(zip_in, tmpdir)
            flatten_if_wrapped(tmpdir, self.log)

            self.log("Đang hoàn tất import...")
            shutil.move(str(tmpdir), str(dest))

            # Đặt tên hiển thị
            desired_name = self.entry_display_name.get().strip()
            if not desired_name:
                desired_name = try_get_profile_name_from_preferences(dest) or folder_name

            # Cập nhật Preferences trong profile
            ensure_preferences_display_name(dest, desired_name, self.log)

            # Cập nhật Local State (đăng ký profile & set last_used)
            update_local_state_register_profile(folder_name, desired_name, self.log)

            # Fix quyền trên *nix
            try:
                if platform.system() != "Windows":
                    import getpass
                    user = getpass.getuser()
                    subprocess.run(["chown", "-R", f"{user}:{user}", str(dest)],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            self.log("Hoàn tất import!")
            messagebox.showinfo("Xong", f"Đã import vào profile mới:\n{folder_name}\nTên hiển thị: {desired_name}\nThư mục: {dest}")

            if self.var_launch.get():
                launch_chrome_with_profile(folder_name, self.log)

        except Exception as e:
            self.log(f"Lỗi import: {e}")
            messagebox.showerror("Lỗi", f"Import thất bại:\n{e}")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
