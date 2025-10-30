#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Chrome Profile Transfer GUI (Tkinter) — v1.1
---------------------------------------------
NEW in v1.1:
- Tự đọc file "Local State" để lấy **tên hiển thị (display name)** của các profile.
- Combo box sẽ hiển thị:  "<folder> — <display name>"  (VD: "Profile 1 — Hồng Hào")
- Vẫn cho phép chọn thủ công thư mục profile nếu muốn.

Export (backup) và Import (restore) profile Chrome giữa 2 máy.
Lưu ý về mật khẩu/cookies: có thể không hoạt động trên máy khác do mã hoá theo OS/user.
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

APP_TITLE = "Chrome Profile Transfer (Tkinter) v1.1"
DEFAULT_ZIP_NAME = "chrome_profile_backup.zip"

def chrome_process_names():
    system = platform.system()
    if system == "Windows":
        return ["chrome.exe"]
    elif system == "Darwin":
        return ["Google Chrome", "Google Chrome Helper"]
    else:
        return ["chrome", "chrome-browser", "google-chrome", "google-chrome-stable", "chromium", "chromium-browser"]

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
    time.sleep(1.0)

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
    """Path tới file 'Local State' (cùng cấp với các thư mục profile)."""
    return user_profile_base() / "Local State"

def read_profile_display_names():
    """
    Đọc 'Local State' để lấy tên hiển thị của các profile.
    Trả về dict: { 'Default': 'Person 1', 'Profile 1': 'Work', ... }
    """
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
        # Nếu lỗi parse, trả về rỗng => fallback dùng tên thư mục
        pass
    return mapping

def list_profiles_with_names():
    """
    Liệt kê các thư mục profile thực có mặt trên đĩa,
    kèm tên hiển thị đọc từ Local State (nếu có).
    Trả về list các tuple: (folder_name, display_name)
    """
    base = user_profile_base()
    name_map = read_profile_display_names()
    results = []
    if base.exists():
        for p in base.iterdir():
            if p.is_dir() and (p.name == "Default" or p.name.startswith("Profile ")):
                results.append((p.name, name_map.get(p.name, p.name)))
    # Sắp xếp: 'Default' trước, sau đó Profile N tăng dần
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

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(APP_TITLE)
        self.geometry("900x620")
        self.minsize(800, 580)

        # Data
        self.profile_items = []   # list[(folder, display)]
        self.combo_value_to_folder = {}  # display string -> folder

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
        self.combo_profile = ttk.Combobox(self.tab_export, state="readonly", width=50)
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
        ttk.Label(self.tab_import, text="Tên profile đích (folder: Default / Profile 1 / ...):").grid(row=row, column=0, sticky="w", **pad)
        self.entry_dest_profile = ttk.Entry(self.tab_import)
        self.entry_dest_profile.insert(0, "Default")
        self.entry_dest_profile.grid(row=row, column=1, sticky="we", **pad)

        row += 1
        ttk.Label(self.tab_import, text="Hoặc đường dẫn đích (tuỳ chọn):").grid(row=row, column=0, sticky="w", **pad)
        self.entry_dest_path = ttk.Entry(self.tab_import)
        self.entry_dest_path.grid(row=row, column=1, sticky="we", **pad)
        ttk.Button(self.tab_import, text="Chọn...", command=self.pick_dest_folder).grid(row=row, column=2, **pad)

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
        self.text_log = tk.Text(self.frame_log, height=10, wrap="word")
        self.text_log.pack(fill="both", expand=True, padx=8, pady=8)
        self.scroll_log = ttk.Scrollbar(self.text_log, command=self.text_log.yview)
        self.text_log.configure(yscrollcommand=self.scroll_log.set)

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

        # Suggest default output zip
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

    def pick_dest_folder(self):
        d = filedialog.askdirectory(title="Chọn thư mục đích cho profile (sẽ là thư mục profile)")
        if d:
            self.entry_dest_path.delete(0, "end")
            self.entry_dest_path.insert(0, d)

    # Export/Import actions
    def run_export(self):
        t = threading.Thread(target=self._do_export, daemon=True)
        t.start()

    def run_import(self):
        t = threading.Thread(target=self._do_import, daemon=True)
        t.start()

    def _do_export(self):
        try:
            # Determine source folder
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

            dest_custom = self.entry_dest_path.get().strip()
            if dest_custom:
                dest = Path(dest_custom)
            else:
                prof_folder = self.entry_dest_profile.get().strip() or "Default"
                dest = default_profile_path(prof_folder)

            parent = dest.parent
            parent.mkdir(parents=True, exist_ok=True)

            self.log(f"File nhập: {zip_in}")
            self.log(f"Thư mục profile đích: {dest}")

            if self.var_kill2.get():
                self.log("Đang đóng Chrome...")
                kill_chrome(self.log)

            # Backup existing folder
            if dest.exists():
                backup_copy = dest.with_name(dest.name + ".bak_" + time.strftime("%Y%m%d_%H%M%S"))
                self.log(f"Đã tồn tại profile đích. Đang chuyển sang backup: {backup_copy}")
                shutil.move(str(dest), str(backup_copy))

            tmpdir = dest.with_name(dest.name + "_tmp_" + time.strftime("%Y%m%d_%H%M%S"))
            tmpdir.mkdir(parents=True, exist_ok=True)

            self.log("Đang giải nén...")
            unzip_to_folder(zip_in, tmpdir)

            self.log("Đang hoàn tất import...")
            shutil.move(str(tmpdir), str(dest))

            # On Linux/macOS, ensure ownership/permission (best effort)
            try:
                if platform.system() != "Windows":
                    import getpass
                    user = getpass.getuser()
                    subprocess.run(["chown", "-R", f"{user}:{user}", str(dest)], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            except Exception:
                pass

            self.log("Hoàn tất import! Hãy mở Chrome và kiểm tra.")
            messagebox.showinfo("Xong", f"Import hoàn tất vào:\n{dest}")

        except Exception as e:
            self.log(f"Lỗi import: {e}")
            messagebox.showerror("Lỗi", f"Import thất bại:\n{e}")

def main():
    app = App()
    app.mainloop()

if __name__ == "__main__":
    main()
