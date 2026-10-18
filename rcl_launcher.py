import ast
import hashlib
import json
import os
import subprocess
import sys
import threading
import tempfile
import time
import uuid
import webbrowser
from datetime import datetime, timezone
import customtkinter as ctk
import requests
from tkinter import messagebox, filedialog
import minecraft_launcher_lib

# Cấu hình giao diện CustomTkinter
ctk.set_appearance_mode("Dark")
ctk.set_default_color_theme("green")

DEFAULT_MINECRAFT_DIR = "D:/rclmc/.minecraft"
CLIENT_ID = "00000000402b5328"  # Public Microsoft Client ID
REDIRECT_URL = "https://login.live.com/oauth20_desktop.srf"
CONFIG_FILE = os.path.join(os.path.dirname(__file__), "rcl_config.json")
MOD_LOADER_IDS = {
    "Vanilla": None,
    "Forge": "forge",
    "Fabric": "fabric",
    "NeoForge": "neoforge",
    "Quilt": "quilt",
}
MANUAL_PROFILE = "Tự chọn phiên bản"
APP_VERSION = "1.0.1"
GITHUB_REPOSITORY = "MinhbaoGDVN/rclmc"
GITHUB_RELEASE_API = (
    f"https://api.github.com/repos/{GITHUB_REPOSITORY}/releases/latest"
)
UPDATE_ASSET_NAME = "rcl_launcher.py"


def read_launcher_profiles(minecraft_directory):
  profiles_path = os.path.join(minecraft_directory, "launcher_profiles.json")
  if not os.path.exists(profiles_path):
    return {"profiles": {}, "settings": {}, "version": 3}

  with open(profiles_path, "r", encoding="utf-8") as profile_file:
    data = json.load(profile_file)

  if not isinstance(data, dict) or not isinstance(data.get("profiles"), dict):
    raise ValueError("launcher_profiles.json không có cấu trúc profile hợp lệ.")
  return data


def register_launcher_profile(
    minecraft_directory, profile_key, name, installed_version
):
  os.makedirs(minecraft_directory, exist_ok=True)
  data = read_launcher_profiles(minecraft_directory)
  profiles = data["profiles"]
  profile_id = next(
      (
          key for key, profile in profiles.items()
          if isinstance(profile, dict)
          and profile.get("rclManaged") is True
          and profile.get("rclProfileKey") == profile_key
      ),
      None,
  )
  if profile_id is None:
    profile_id = str(uuid.uuid4())

  now = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
  old_profile = profiles.get(profile_id, {})
  profiles[profile_id] = {
      **old_profile,
      "name": name,
      "type": "custom",
      "created": old_profile.get("created", now),
      "lastUsed": now,
      "lastVersionId": installed_version,
      "rclManaged": True,
      "rclProfileKey": profile_key,
  }

  profiles_path = os.path.join(minecraft_directory, "launcher_profiles.json")
  file_descriptor, temporary_path = tempfile.mkstemp(
      prefix="launcher_profiles_", suffix=".tmp", dir=minecraft_directory
  )
  try:
    with os.fdopen(file_descriptor, "w", encoding="utf-8") as profile_file:
      json.dump(data, profile_file, ensure_ascii=False, indent=4)
      profile_file.write("\n")
    os.replace(temporary_path, profiles_path)
  except Exception:
    if os.path.exists(temporary_path):
      os.remove(temporary_path)
    raise
  return profile_id


def launcher_profile_to_options(profile):
  version_id = profile.get("lastVersionId")
  name = profile.get("name")
  if not isinstance(version_id, str) or not isinstance(name, str):
    raise ValueError("Profile không có tên hoặc phiên bản hợp lệ.")

  version_type = (
      version_id
      if version_id in ("latest-release", "latest-snapshot")
      else "custom"
  )
  vanilla_profile = {"name": name, "versionType": version_type}
  if version_type == "custom":
    vanilla_profile["version"] = version_id

  if isinstance(profile.get("gameDir"), str):
    vanilla_profile["gameDirectory"] = profile["gameDir"]
  if isinstance(profile.get("javaDir"), str):
    vanilla_profile["javaExecutable"] = profile["javaDir"]
  if isinstance(profile.get("javaArgs"), str):
    vanilla_profile["javaArguments"] = profile["javaArgs"].split()

  resolution = profile.get("resolution")
  if (
      isinstance(resolution, dict)
      and isinstance(resolution.get("width"), int)
      and isinstance(resolution.get("height"), int)
      and resolution["width"] > 0
      and resolution["height"] > 0
  ):
    vanilla_profile["customResolution"] = {
        "width": resolution["width"],
        "height": resolution["height"],
    }

  version = (
      minecraft_launcher_lib.vanilla_launcher
      .get_vanilla_launcher_profile_version(vanilla_profile)
  )
  options = (
      minecraft_launcher_lib.vanilla_launcher
      .vanilla_launcher_profile_to_minecraft_options(vanilla_profile)
  )
  return version, options


def generate_offline_uuid(username):
  name_bytes = f"OfflinePlayer:{username}".encode("utf-8")
  return str(
      uuid.UUID(
          bytes=hashlib.md5(name_bytes, usedforsecurity=False).digest(),
          version=3,
      )
  )


class RCLLauncher(ctk.CTk):

  def __init__(self):
    super().__init__()

    self.title("RCL MC LAUNCHER")
    self.iconbitmap(os.path.join(os.path.dirname(__file__), "minecraft.ico"))
    self.geometry("800x610")
    self.resizable(False, False)

    # Đọc cấu hình đã lưu
    self.config = self.load_config()
    self.minecraft_directory = self.config.get(
        "directory", DEFAULT_MINECRAFT_DIR
    )
    self.ram_allocation = self.config.get("ram", "2G")
    self.java_path = self.config.get("java_path", "")
    self.account_data = self.config.get("account", None)
    self.loader_request_id = 0
    self.release_versions = []
    self.loader_versions_ready = False
    self.launcher_profiles = {}
    self.active_launcher_profile = None

    # Biến kiểm soát tiến trình
    self.is_downloading = False
    self.cancel_download = False
    self.game_process = None

    # Layout chính
    self.grid_columnconfigure(1, weight=1)
    self.grid_rowconfigure(0, weight=1)

    # 1. Sidebar
    self.sidebar_frame = ctk.CTkFrame(
        self, width=200, fg_color="#111215", corner_radius=0
    )
    self.sidebar_frame.grid(row=0, column=0, sticky="nsew")
    self.sidebar_frame.grid_rowconfigure(5, weight=1)

    self.logo_label = ctk.CTkLabel(
        self.sidebar_frame,
        text="RCL CLIENT",
        font=ctk.CTkFont(family="Arial", size=20, weight="bold"),
        text_color="#22c55e",
    )
    self.logo_label.grid(row=0, column=0, padx=20, pady=(25, 20), sticky="w")

    self.btn_play = ctk.CTkButton(
        self.sidebar_frame,
        text="🎮   Play",
        fg_color="#2b2d31",
        hover_color="#3f4147",
        anchor="w",
        height=40,
        font=ctk.CTkFont(family="Arial", size=13, weight="bold"),
        command=lambda: self.select_tab("play"),
    )
    self.btn_play.grid(row=1, column=0, padx=15, pady=5, sticky="ew")

    self.btn_account = ctk.CTkButton(
        self.sidebar_frame,
        text="👤   Account",
        fg_color="transparent",
        hover_color="#2b2d31",
        anchor="w",
        height=40,
        text_color="#9ca3af",
        font=ctk.CTkFont(family="Arial", size=13, weight="bold"),
        command=lambda: self.select_tab("account"),
    )
    self.btn_account.grid(row=2, column=0, padx=15, pady=5, sticky="ew")

    self.btn_settings = ctk.CTkButton(
        self.sidebar_frame,
        text="⚙️   Settings",
        fg_color="transparent",
        hover_color="#2b2d31",
        anchor="w",
        height=40,
        text_color="#9ca3af",
        font=ctk.CTkFont(family="Arial", size=13, weight="bold"),
        command=lambda: self.select_tab("settings"),
    )
    self.btn_settings.grid(row=3, column=0, padx=15, pady=5, sticky="ew")

    self.account_status_sidebar = ctk.CTkLabel(
        self.sidebar_frame,
        text="Offline Mode",
        font=ctk.CTkFont(family="Arial", size=11),
        text_color="#22c55e",
    )
    self.account_status_sidebar.grid(
        row=6, column=0, padx=20, pady=(0, 5), sticky="sw"
    )

    self.version_info_label = ctk.CTkLabel(
        self.sidebar_frame,
        text=f"v{APP_VERSION} - RCL MC Launcher",
        font=ctk.CTkFont(family="Arial", size=10),
        text_color="#4b5563",
    )
    self.version_info_label.grid(
        row=7, column=0, padx=20, pady=(0, 15), sticky="sw"
    )

    # 2. Main Content
    self.content_frame = ctk.CTkFrame(
        self, fg_color="#1a1b1e", corner_radius=0
    )
    self.content_frame.grid(row=0, column=1, sticky="nsew")

    self.create_play_tab()
    self.create_account_tab()
    self.create_settings_tab()

    self.select_tab("play")
    self.update_account_ui()
    self.load_versions()
    self.on_mod_loader_changed()
    self.refresh_launcher_profiles()
    self.check_for_updates()

  # Configuration
  def load_config(self):
    if os.path.exists(CONFIG_FILE):
      try:
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
          return json.load(f)
      except Exception:
        pass
    return {}

  def save_config(self):
    data = {
        "directory": self.minecraft_directory,
        "ram": self.ram_allocation,
        "java_path": self.java_path,
        "account": self.account_data,
        "mod_loader": self.loader_combobox.get(),
        "launcher_profile_id": (
            self.active_launcher_profile.get("id")
            if self.active_launcher_profile
            else None
        ),
    }
    self.config = data
    with open(CONFIG_FILE, "w", encoding="utf-8") as f:
      json.dump(data, f, indent=4)

  def select_tab(self, tab_name):
    for btn in [self.btn_play, self.btn_account, self.btn_settings]:
      btn.configure(fg_color="transparent", text_color="#9ca3af")

    if tab_name == "play":
      self.btn_play.configure(fg_color="#2b2d31", text_color="#ffffff")
      self.play_tab_frame.lift()
    elif tab_name == "account":
      self.btn_account.configure(fg_color="#2b2d31", text_color="#ffffff")
      self.account_tab_frame.lift()
    elif tab_name == "settings":
      self.btn_settings.configure(fg_color="#2b2d31", text_color="#ffffff")
      self.settings_tab_frame.lift()

  # ================= TAB 1: PLAY =================
  def create_play_tab(self):
    self.play_tab_frame = ctk.CTkFrame(
        self.content_frame, fg_color="transparent"
    )
    self.play_tab_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

    title = ctk.CTkLabel(
        self.play_tab_frame,
        text="LAUNCH MINECRAFT",
        font=ctk.CTkFont(family="Arial", size=22, weight="bold"),
        text_color="#ffffff",
    )
    title.pack(anchor="w", padx=40, pady=(35, 15))

    lbl_user = ctk.CTkLabel(
        self.play_tab_frame,
        text="TÊN NGƯỜI CHƠI (USERNAME)",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_user.pack(anchor="w", padx=40)

    self.username_entry = ctk.CTkEntry(
        self.play_tab_frame,
        height=38,
        font=ctk.CTkFont(family="Arial", size=12),
        fg_color="#2b2d31",
        border_color="#3f4147",
        text_color="#ffffff",
    )
    self.username_entry.pack(fill="x", padx=40, pady=(5, 15))
    self.username_entry.insert(0, "RCLPlayer")

    lbl_ver = ctk.CTkLabel(
        self.play_tab_frame,
        text="PHIÊN BẢN GAME",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_ver.pack(anchor="w", padx=40)

    self.version_combobox = ctk.CTkComboBox(
        self.play_tab_frame,
        height=38,
        font=ctk.CTkFont(family="Arial", size=12),
        fg_color="#2b2d31",
        border_color="#3f4147",
        button_color="#22c55e",
        button_hover_color="#16a34a",
        dropdown_fg_color="#1f2023",
        text_color="#ffffff",
        state="readonly",
        command=self.on_game_version_changed,
    )
    self.version_combobox.pack(fill="x", padx=40, pady=(5, 10))

    lbl_loader = ctk.CTkLabel(
        self.play_tab_frame,
        text="TRÌNH TẢI MOD",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_loader.pack(anchor="w", padx=40)

    self.loader_combobox = ctk.CTkComboBox(
        self.play_tab_frame,
        height=35,
        values=list(MOD_LOADER_IDS),
        font=ctk.CTkFont(family="Arial", size=12),
        fg_color="#2b2d31",
        border_color="#3f4147",
        button_color="#22c55e",
        button_hover_color="#16a34a",
        dropdown_fg_color="#1f2023",
        text_color="#ffffff",
        state="readonly",
        command=self.on_mod_loader_changed,
    )
    configured_loader = self.config.get("mod_loader", "Vanilla")
    if (
        not isinstance(configured_loader, str)
        or configured_loader not in MOD_LOADER_IDS
    ):
      configured_loader = "Vanilla"
    self.loader_combobox.set(configured_loader)
    self.loader_combobox.pack(fill="x", padx=40, pady=(5, 8))

    self.loader_version_label = ctk.CTkLabel(
        self.play_tab_frame,
        text="PHIÊN BẢN TRÌNH TẢI MOD",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    self.loader_version_combobox = ctk.CTkComboBox(
        self.play_tab_frame,
        height=35,
        values=[],
        font=ctk.CTkFont(family="Arial", size=12),
        fg_color="#2b2d31",
        border_color="#3f4147",
        button_color="#22c55e",
        button_hover_color="#16a34a",
        dropdown_fg_color="#1f2023",
        text_color="#ffffff",
        state="readonly",
    )

    lbl_profile = ctk.CTkLabel(
        self.play_tab_frame,
        text="PROFILE TỪ MINECRAFT LAUNCHER",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_profile.pack(anchor="w", padx=40, pady=(4, 0))

    profile_frame = ctk.CTkFrame(
        self.play_tab_frame, fg_color="transparent"
    )
    profile_frame.pack(fill="x", padx=40, pady=(5, 5))
    self.profile_combobox = ctk.CTkComboBox(
        profile_frame,
        height=35,
        values=[MANUAL_PROFILE],
        font=ctk.CTkFont(family="Arial", size=12),
        fg_color="#2b2d31",
        border_color="#3f4147",
        button_color="#22c55e",
        button_hover_color="#16a34a",
        dropdown_fg_color="#1f2023",
        text_color="#ffffff",
        state="readonly",
        command=self.on_launcher_profile_changed,
    )
    self.profile_combobox.pack(
        side="left", fill="x", expand=True, padx=(0, 10)
    )
    self.profile_combobox.set(MANUAL_PROFILE)

    self.refresh_profiles_button = ctk.CTkButton(
        profile_frame,
        text="Tải lại",
        width=75,
        height=35,
        fg_color="#22c55e",
        command=lambda: self.refresh_launcher_profiles(show_errors=True),
    )
    self.refresh_profiles_button.pack(side="right")

    self.progress_bar = ctk.CTkProgressBar(
        self.play_tab_frame,
        orientation="horizontal",
        height=8,
        progress_color="#22c55e",
        fg_color="#2b2d31",
    )
    self.progress_bar.pack(fill="x", padx=40, pady=(15, 5))
    self.progress_bar.set(0)

    self.status_label = ctk.CTkLabel(
        self.play_tab_frame,
        text="Trạng thái: Sẵn sàng",
        font=ctk.CTkFont(family="Arial", size=10),
        text_color="#9ca3af",
    )
    self.status_label.pack(anchor="w", padx=40, pady=(0, 15))

    self.main_action_button = ctk.CTkButton(
        self.play_tab_frame,
        text="LAUNCH GAME",
        height=48,
        font=ctk.CTkFont(family="Arial", size=14, weight="bold"),
        fg_color="#22c55e",
        hover_color="#16a34a",
        text_color="#ffffff",
        command=self.handle_main_action,
    )
    self.main_action_button.pack(fill="x", padx=40)

  # ================= TAB 2: ACCOUNT (MICROSOFT OFFICIAL DOCS) =================
  def create_account_tab(self):
    self.account_tab_frame = ctk.CTkFrame(
        self.content_frame, fg_color="transparent"
    )
    self.account_tab_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

    title = ctk.CTkLabel(
        self.account_tab_frame,
        text="TÀI KHOẢN MINECRAFT",
        font=ctk.CTkFont(family="Arial", size=22, weight="bold"),
        text_color="#ffffff",
    )
    title.pack(anchor="w", padx=40, pady=(35, 20))

    self.account_card = ctk.CTkFrame(
        self.account_tab_frame, fg_color="#2b2d31", corner_radius=10
    )
    self.account_card.pack(fill="x", padx=40, pady=10)

    self.lbl_account_name = ctk.CTkLabel(
        self.account_card,
        text="Chưa đăng nhập (Chế độ Offline)",
        font=ctk.CTkFont(family="Arial", size=14, weight="bold"),
        text_color="#ffffff",
    )
    self.lbl_account_name.pack(anchor="w", padx=20, pady=(20, 5))

    self.lbl_account_type = ctk.CTkLabel(
        self.account_card,
        text="Đăng nhập tài khoản Microsoft để kết nối Server Online chính thức.",
        font=ctk.CTkFont(family="Arial", size=11),
        text_color="#9ca3af",
        wraplength=450,
        justify="left",
    )
    self.lbl_account_type.pack(anchor="w", padx=20, pady=(0, 20))

    self.btn_ms_login = ctk.CTkButton(
        self.account_tab_frame,
        text="🌐  Đăng nhập bằng Microsoft",
        height=42,
        font=ctk.CTkFont(family="Arial", size=12, weight="bold"),
        fg_color="#00a4ef",
        hover_color="#0078d4",
        command=self.start_ms_login_thread,
    )
    self.btn_ms_login.pack(fill="x", padx=40, pady=(15, 10))

    self.btn_logout = ctk.CTkButton(
        self.account_tab_frame,
        text="Đăng xuất (Chuyển sang Offline)",
        height=38,
        font=ctk.CTkFont(family="Arial", size=11),
        fg_color="#ef4444",
        hover_color="#dc2626",
        command=self.logout_account,
    )

  def update_account_ui(self):
    if self.account_data:
      name = self.account_data.get("name", "Player")
      self.lbl_account_name.configure(text=f"🟢 {name} (Microsoft Online)")
      self.lbl_account_type.configure(
          text="Tài khoản đã xác thực thành công. Sẵn sàng vào Server Online!"
      )
      self.account_status_sidebar.configure(
          text=f"Online: {name}", text_color="#22c55e"
      )
      self.username_entry.delete(0, "end")
      self.username_entry.insert(0, name)
      self.username_entry.configure(state="disabled")
      self.btn_ms_login.pack_forget()
      self.btn_logout.pack(fill="x", padx=40, pady=10)
    else:
      self.lbl_account_name.configure(text="⚪ Chế độ Offline (Cracked)")
      self.lbl_account_type.configure(
          text="Chế độ Offline cho phép chơi Singleplayer hoặc Server Offline."
      )
      self.account_status_sidebar.configure(
          text="Offline Mode", text_color="#22c55e"
      )
      self.username_entry.configure(state="normal")
      self.btn_logout.pack_forget()
      self.btn_ms_login.pack(fill="x", padx=40, pady=(15, 10))

  def start_ms_login_thread(self):
    threading.Thread(target=self.login_microsoft, daemon=True).start()

  def login_microsoft(self):
    try:
      # Gọi chuẩn theo Docs minecraft_launcher_lib.microsoft_account
      login_url, state, code_verifier = (
          minecraft_launcher_lib.microsoft_account.get_secure_login_data(
              CLIENT_ID, REDIRECT_URL
          )
      )
      webbrowser.open(login_url)

      dialog = ctk.CTkInputDialog(
          text=(
              "Trình duyệt đã mở để đăng nhập Microsoft.\nSau khi đăng nhập"
              " xong, hãy copy URL của trang web được chuyển hướng và dán vào"
              " đây:"
          ),
          title="Microsoft Login Auth",
      )
      redirected_url = dialog.get_input()

      if redirected_url:
        auth_code = (
            minecraft_launcher_lib.microsoft_account.parse_auth_code_url(
                redirected_url, state
            )
        )
        login_data = (
            minecraft_launcher_lib.microsoft_account.complete_login(
                CLIENT_ID, None, REDIRECT_URL, auth_code, code_verifier
            )
        )

        self.account_data = login_data
        self.save_config()
        self.after(0, self.update_account_ui)
        messagebox.showinfo("Thành công", f"Chào mừng {login_data['name']}!")

    except Exception as exc:
      print(f"Lỗi MS Login: {exc}")
      msg = str(exc)
      self.after(
          0,
          lambda: self.lbl_account_type.configure(text=f"Lỗi đăng nhập: {msg}"),
      )

  def logout_account(self):
    self.account_data = None
    self.save_config()
    self.update_account_ui()
    messagebox.showinfo("Đăng xuất", "Đã chuyển về chế độ Offline!")

  # ================= TAB 3: SETTINGS =================
  def create_settings_tab(self):
    self.settings_tab_frame = ctk.CTkFrame(
        self.content_frame, fg_color="transparent"
    )
    self.settings_tab_frame.place(relx=0, rely=0, relwidth=1, relheight=1)

    title = ctk.CTkLabel(
        self.settings_tab_frame,
        text="CÀI ĐẶT HỆ THỐNG",
        font=ctk.CTkFont(family="Arial", size=22, weight="bold"),
        text_color="#ffffff",
    )
    title.pack(anchor="w", padx=40, pady=(35, 15))

    lbl_dir = ctk.CTkLabel(
        self.settings_tab_frame,
        text="THƯ MỤC GAME (.minecraft)",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_dir.pack(anchor="w", padx=40)

    dir_frame = ctk.CTkFrame(self.settings_tab_frame, fg_color="transparent")
    dir_frame.pack(fill="x", padx=40, pady=(5, 10))

    self.dir_entry = ctk.CTkEntry(
        dir_frame,
        height=35,
        font=ctk.CTkFont(family="Arial", size=11),
        fg_color="#2b2d31",
        border_color="#3f4147",
        text_color="#ffffff",
    )
    self.dir_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
    self.dir_entry.insert(0, self.minecraft_directory)

    btn_browse_dir = ctk.CTkButton(
        dir_frame,
        text="Chọn...",
        width=80,
        height=35,
        fg_color="#22c55e",
        command=self.browse_directory,
    )
    btn_browse_dir.pack(side="right")

    lbl_java = ctk.CTkLabel(
        self.settings_tab_frame,
        text="ĐƯỜNG DẪN JAVA (EXECUTABLE PATH)",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_java.pack(anchor="w", padx=40, pady=(5, 0))

    java_frame = ctk.CTkFrame(self.settings_tab_frame, fg_color="transparent")
    java_frame.pack(fill="x", padx=40, pady=(5, 10))

    self.java_entry = ctk.CTkEntry(
        java_frame,
        height=35,
        placeholder_text="Mặc định sử dụng Java của hệ thống",
        font=ctk.CTkFont(family="Arial", size=11),
        fg_color="#2b2d31",
        border_color="#3f4147",
        text_color="#ffffff",
    )
    self.java_entry.pack(side="left", fill="x", expand=True, padx=(0, 10))
    if self.java_path:
      self.java_entry.insert(0, self.java_path)

    btn_browse_java = ctk.CTkButton(
        java_frame,
        text="Duyệt...",
        width=80,
        height=35,
        fg_color="#22c55e",
        command=self.browse_java,
    )
    btn_browse_java.pack(side="right")

    lbl_ram = ctk.CTkLabel(
        self.settings_tab_frame,
        text="BỘ NHỚ RAM (Xmx)",
        font=ctk.CTkFont(family="Arial", size=10, weight="bold"),
        text_color="#9ca3af",
    )
    lbl_ram.pack(anchor="w", padx=40, pady=(5, 0))

    self.ram_combobox = ctk.CTkComboBox(
        self.settings_tab_frame,
        height=35,
        values=["2G", "3G", "4G", "6G", "8G", "12G", "16G"],
        font=ctk.CTkFont(family="Arial", size=11),
        fg_color="#2b2d31",
        border_color="#3f4147",
        button_color="#22c55e",
        text_color="#ffffff",
        state="readonly",
    )
    self.ram_combobox.pack(fill="x", padx=40, pady=(5, 15))
    self.ram_combobox.set(self.ram_allocation)

    action_frame = ctk.CTkFrame(
        self.settings_tab_frame, fg_color="transparent"
    )
    action_frame.pack(fill="x", padx=40, pady=10)

    btn_save = ctk.CTkButton(
        action_frame,
        text="SAVE",
        height=40,
        font=ctk.CTkFont(family="Arial", size=12, weight="bold"),
        fg_color="#22c55e",
        hover_color="#16a34a",
        command=self.save_settings,
    )
    btn_save.pack(side="left", fill="x", expand=True, padx=(0, 10))

    btn_dont_save = ctk.CTkButton(
        action_frame,
        text="DON'T SAVE",
        height=40,
        font=ctk.CTkFont(family="Arial", size=12, weight="bold"),
        fg_color="#ef4444",
        hover_color="#dc2626",
        command=self.dont_save_settings,
    )
    btn_dont_save.pack(side="right", fill="x", expand=True)

    self.update_button = ctk.CTkButton(
        self.settings_tab_frame,
        text="KIỂM TRA CẬP NHẬT",
        height=38,
        fg_color="#22c55e",
        hover_color="#16a34a",
        command=lambda: self.check_for_updates(show_errors=True),
    )
    self.update_button.pack(fill="x", padx=40, pady=(0, 10))

  def browse_directory(self):
    selected = filedialog.askdirectory(initialdir=self.minecraft_directory)
    if selected:
      self.minecraft_directory = os.path.join(selected, ".minecraft")
      self.dir_entry.delete(0, "end")
      self.dir_entry.insert(0, self.minecraft_directory)

  def browse_java(self):
    selected_file = filedialog.askopenfilename(
        filetypes=[("Executable", "javaw.exe;java.exe"), ("All Files", "*.*")]
    )
    if selected_file:
      self.java_path = selected_file
      self.java_entry.delete(0, "end")
      self.java_entry.insert(0, selected_file)

  def save_settings(self):
    self.minecraft_directory = self.dir_entry.get().strip()
    self.java_path = self.java_entry.get().strip()
    self.ram_allocation = self.ram_combobox.get()
    config_data = {
        "directory": self.minecraft_directory,
        "ram": self.ram_allocation,
        "java_path": self.java_path,
        "account": self.account_data,
        "mod_loader": self.loader_combobox.get(),
        "launcher_profile_id": self.config.get("launcher_profile_id"),
    }
    self.update_status("Đang lưu cài đặt...")

    def save_and_load_profiles():
      try:
        with open(CONFIG_FILE, "w", encoding="utf-8") as config_file:
          json.dump(config_data, config_file, indent=4)
        profile_data = read_launcher_profiles(self.minecraft_directory)
        self.after(
            0,
            lambda: self.finish_settings_save(config_data, profile_data),
        )
      except Exception as exc:
        error = str(exc)
        self.after(0, lambda: self.show_settings_save_error(error))

    threading.Thread(target=save_and_load_profiles, daemon=True).start()

  def finish_settings_save(self, config_data, profile_data):
    self.config = config_data
    self.refresh_launcher_profiles(profile_data=profile_data)
    messagebox.showinfo("Thành công", "Đã lưu cài đặt Launcher!")
    self.select_tab("play")

  def show_settings_save_error(self, error):
    self.update_status("Lỗi lưu cài đặt")
    messagebox.showerror(
        "Lỗi", f"Không thể lưu cài đặt hoặc đọc profile:\n{error}"
    )

  def dont_save_settings(self):
    self.dir_entry.delete(0, "end")
    self.dir_entry.insert(0, self.minecraft_directory)

    self.java_entry.delete(0, "end")
    if self.java_path:
      self.java_entry.insert(0, self.java_path)

    self.ram_combobox.set(self.ram_allocation)
    self.select_tab("play")

  @staticmethod
  def parse_version(version):
    normalized = version.removeprefix("v").removeprefix("V")
    parts = normalized.split(".")
    if not parts or any(not part.isdigit() for part in parts):
      raise ValueError(f"Tag phiên bản không hợp lệ: {version}")
    parsed = [int(part) for part in parts]
    while len(parsed) > 1 and parsed[-1] == 0:
      parsed.pop()
    return tuple(parsed)

  def check_for_updates(self, show_errors=False):
    self.update_button.configure(state="disabled")
    self.update_status("Đang kiểm tra bản cập nhật...")

    def check():
      try:
        response = requests.get(
            GITHUB_RELEASE_API,
            headers={"Accept": "application/vnd.github+json"},
            timeout=10,
        )
        if response.status_code == 404:
          self.after(
              0,
              lambda: self.finish_update_check(
                  None, None, show_errors, no_release=True
              ),
          )
          return
        response.raise_for_status()
        release = response.json()
        tag_name = release.get("tag_name")
        if not isinstance(tag_name, str):
          raise ValueError("GitHub Release thiếu tag phiên bản.")

        latest_version = self.parse_version(tag_name)
        current_version = self.parse_version(APP_VERSION)
        if latest_version <= current_version:
          self.after(
              0,
              lambda: self.finish_update_check(
                  None, "Bạn đang dùng phiên bản mới nhất.", show_errors
              ),
          )
          return

        asset = next(
            (
                item for item in release.get("assets", [])
                if item.get("name") == UPDATE_ASSET_NAME
            ),
            None,
        )
        if asset is None or not isinstance(
            asset.get("browser_download_url"), str
        ):
          raise ValueError(
              f"Bản phát hành không có asset {UPDATE_ASSET_NAME}."
          )

        self.after(
            0,
            lambda: self.finish_update_check(
                (tag_name, asset), None, show_errors
            ),
        )
      except Exception as exc:
        error = str(exc)
        self.after(
            0,
            lambda: self.finish_update_check(None, error, show_errors),
        )

    threading.Thread(target=check, daemon=True).start()

  def finish_update_check(
      self, update, error, show_errors, no_release=False
  ):
    self.update_button.configure(state="normal")
    if error:
      self.update_status(f"Lỗi kiểm tra cập nhật: {error}")
      if show_errors:
        messagebox.showerror("Lỗi cập nhật", error)
      return

    if update is None:
      if no_release:
        self.update_status("Repo chưa có bản phát hành để cập nhật.")
        if show_errors:
          messagebox.showinfo(
              "Cập nhật",
              "Repo GitHub chưa có Release. Hãy tạo Release và tải lên "
              f"asset {UPDATE_ASSET_NAME}.",
          )
        return

      self.update_status("Bạn đang dùng phiên bản mới nhất.")
      if show_errors:
        messagebox.showinfo(
            "Cập nhật", "Bạn đang dùng phiên bản mới nhất."
        )
      return

    tag_name, asset = update
    if not messagebox.askyesno(
        "Có bản cập nhật",
        f"Đã có phiên bản {tag_name} (hiện tại: v{APP_VERSION}).\n"
        "Bạn muốn tải và cài bản cập nhật ngay không?",
    ):
      self.update_status(f"Có bản cập nhật mới: {tag_name}")
      return

    self.update_button.configure(state="disabled")
    self.update_status(f"Đang tải bản cập nhật {tag_name}...")
    threading.Thread(
        target=self.download_update, args=(tag_name, asset), daemon=True
    ).start()

  def download_update(self, tag_name, asset):
    temporary_path = None
    try:
      response = requests.get(
          asset["browser_download_url"], timeout=(10, 60)
      )
      response.raise_for_status()
      content = response.content
      if not content or len(content) > 10 * 1024 * 1024:
        raise ValueError("Kích thước file cập nhật không hợp lệ.")

      expected_digest = asset.get("digest")
      if isinstance(expected_digest, str) and expected_digest.startswith(
          "sha256:"
      ):
        actual_digest = hashlib.sha256(content).hexdigest()
        if actual_digest.lower() != expected_digest[7:].lower():
          raise ValueError("SHA-256 của file cập nhật không khớp.")

      source = content.decode("utf-8")
      compile(source, UPDATE_ASSET_NAME, "exec")

      script_path = os.path.abspath(__file__)
      file_descriptor, temporary_path = tempfile.mkstemp(
          prefix="rcl_update_", suffix=".py", dir=os.path.dirname(script_path)
      )
      with os.fdopen(file_descriptor, "wb") as update_file:
        update_file.write(content)

      os.replace(temporary_path, script_path)
      temporary_path = None
      self.after(0, lambda: self.restart_with_update(tag_name))
    except Exception as exc:
      if temporary_path and os.path.exists(temporary_path):
        os.remove(temporary_path)
      error = str(exc)
      self.after(0, lambda: self.finish_update_download_error(error))

  def finish_update_download_error(self, error):
    self.update_button.configure(state="normal")
    self.update_status("Lỗi tải hoặc cài bản cập nhật")
    messagebox.showerror(
        "Lỗi cập nhật", f"Không thể cài bản cập nhật:\n{error}"
    )

  def restart_with_update(self, tag_name):
    try:
      subprocess.Popen(
          [sys.executable, os.path.abspath(__file__)],
          cwd=os.path.dirname(os.path.abspath(__file__)),
      )
    except OSError as exc:
      self.finish_update_download_error(
          f"Đã thay file thành {tag_name}, nhưng không thể khởi động lại: {exc}"
      )
      return
    self.destroy()

  # ================= TẢI VÀ CHẠY GAME =================
  def load_versions(self):
    def fetch():
      try:
        versions = minecraft_launcher_lib.utils.get_version_list()
        version_list = [v["id"] for v in versions if v["type"] == "release"]
        self.after(0, lambda: self.set_release_versions(version_list))
      except Exception as exc:
        print(f"Lỗi danh sách phiên bản: {exc}")
        self.update_status(f"Không tải được danh sách phiên bản: {exc}")

    threading.Thread(target=fetch, daemon=True).start()

  def set_release_versions(self, versions):
    self.release_versions = versions
    if self.active_launcher_profile is not None:
      return
    if self.loader_combobox.get() == "Vanilla":
      self.version_combobox.configure(values=versions)
      if versions and self.version_combobox.get() not in versions:
        self.version_combobox.set(versions[0])
    else:
      self.on_mod_loader_changed()

  def refresh_launcher_profiles(
      self,
      show_errors=False,
      selected_profile_id=None,
      profile_data=None,
  ):
    if profile_data is None:
      try:
        profile_data = read_launcher_profiles(self.minecraft_directory)
      except (OSError, json.JSONDecodeError, ValueError) as exc:
        self.profile_combobox.configure(values=[MANUAL_PROFILE])
        self.profile_combobox.set(MANUAL_PROFILE)
        self.active_launcher_profile = None
        self.update_status(f"Lỗi đọc launcher_profiles.json: {exc}")
        if show_errors:
          messagebox.showerror(
              "Lỗi profile", f"Không thể đọc launcher_profiles.json:\n{exc}"
          )
        return

    choices = {}
    for profile_id, profile in profile_data["profiles"].items():
      if not isinstance(profile, dict):
        continue
      name = profile.get("name")
      version_id = profile.get("lastVersionId")
      if not isinstance(name, str) or not isinstance(version_id, str):
        continue

      label = f"{name} ({version_id})"
      if label in choices:
        label = f"{label} [{profile_id}]"
      choices[label] = {"id": profile_id, "data": profile}

    self.launcher_profiles = choices
    self.profile_combobox.configure(values=[MANUAL_PROFILE, *choices])
    if selected_profile_id is None:
      selected_profile_id = self.config.get("launcher_profile_id")
    selected_label = next(
        (
            label for label, profile in choices.items()
            if profile["id"] == selected_profile_id
        ),
        MANUAL_PROFILE,
    )
    self.profile_combobox.set(selected_label)
    self.on_launcher_profile_changed(selected_label, persist=False)

  def on_launcher_profile_changed(self, selected=None, persist=True):
    selected = selected or self.profile_combobox.get()
    self.loader_request_id += 1

    if selected == MANUAL_PROFILE or selected not in self.launcher_profiles:
      self.active_launcher_profile = None
      self.loader_combobox.configure(state="readonly")
      self.version_combobox.configure(state="readonly")
      self.profile_combobox.set(MANUAL_PROFILE)
      if self.loader_combobox.get() == "Vanilla":
        self.loader_versions_ready = True
      else:
        self.on_mod_loader_changed()
      if persist:
        self.save_config()
      return

    self.active_launcher_profile = self.launcher_profiles[selected]
    self.loader_versions_ready = True
    self.loader_combobox.configure(state="disabled")
    self.version_combobox.configure(state="disabled")
    self.loader_version_combobox.configure(state="disabled")
    profile = self.active_launcher_profile["data"]
    self.update_status(
        f"Đã chọn profile {profile['name']} — "
        f"{profile['lastVersionId']}. Nhấn LAUNCH GAME để chạy."
    )
    if persist:
      self.save_config()

  def install_vanilla_version(self, version, callback):
    max_attempts = 3
    for attempt in range(1, max_attempts + 1):
      try:
        minecraft_launcher_lib.install.install_minecraft_version(
            version, self.minecraft_directory, callback=callback
        )
        return
      except (requests.exceptions.ConnectionError, requests.exceptions.Timeout):
        if attempt == max_attempts or self.cancel_download:
          raise

        delay = attempt * 2
        self.update_status(
            f"Kết nối tải Minecraft bị gián đoạn. Thử lại "
            f"{attempt + 1}/{max_attempts} sau {delay} giây..."
        )
        time.sleep(delay)
        if self.cancel_download:
          return

  def on_mod_loader_changed(self, selected=None):
    loader_name = selected or self.loader_combobox.get()
    loader_id = MOD_LOADER_IDS[loader_name]
    self.loader_request_id += 1
    request_id = self.loader_request_id

    if selected is not None:
      self.save_config()

    if loader_id is None:
      self.loader_versions_ready = True
      self.loader_version_label.pack_forget()
      self.loader_version_combobox.pack_forget()
      self.loader_version_combobox.configure(values=[])
      self.version_combobox.configure(
          values=self.release_versions, state="readonly"
      )
      if (
          self.release_versions
          and self.version_combobox.get() not in self.release_versions
      ):
        self.version_combobox.set(self.release_versions[0])
      return

    self.loader_versions_ready = False
    self.loader_version_label.pack(anchor="w", padx=40)
    self.loader_version_combobox.pack(
        fill="x", padx=40, pady=(5, 8)
    )
    self.version_combobox.configure(state="disabled")
    self.loader_version_combobox.configure(state="disabled")

    if not self.release_versions:
      return

    current_version = self.version_combobox.get()
    release_versions = list(self.release_versions)
    self.update_status(f"Đang tải danh sách phiên bản {loader_name}...")
    threading.Thread(
        target=self.load_loader_versions,
        args=(loader_id, current_version, release_versions, request_id),
        daemon=True,
    ).start()

  def load_loader_versions(
      self, loader_id, current_version, release_versions, request_id
  ):
    try:
      loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_id)
      supported_versions = set(loader.get_minecraft_versions(True))
      versions = [
          version for version in release_versions
          if version in supported_versions
      ]
      if not versions:
        raise RuntimeError(
            f"{loader.get_name()} không hỗ trợ phiên bản Minecraft nào "
            "trong danh sách hiện tại."
        )

      selected_version = (
          current_version if current_version in versions else versions[0]
      )
      loader_versions = loader.get_loader_versions(selected_version, True)
      if not loader_versions:
        loader_versions = loader.get_loader_versions(selected_version, False)
      if not loader_versions:
        raise RuntimeError(
            f"Không tìm thấy phiên bản {loader.get_name()} cho Minecraft "
            f"{selected_version}."
        )

      self.after(
          0,
          lambda: self.apply_loader_versions(
              request_id, versions, selected_version, loader_versions
          ),
      )
    except Exception as exc:
      error = str(exc)
      self.after(0, lambda: self.show_loader_error(request_id, error))

  def apply_loader_versions(
      self, request_id, game_versions, selected_version, loader_versions
  ):
    if request_id != self.loader_request_id:
      return

    self.version_combobox.configure(values=game_versions, state="readonly")
    self.version_combobox.set(selected_version)
    self.loader_version_combobox.configure(
        values=loader_versions, state="readonly"
    )
    self.loader_version_combobox.set(loader_versions[0])
    self.loader_versions_ready = True
    self.update_status("Trạng thái: Sẵn sàng")

  def show_loader_error(self, request_id, error):
    if request_id != self.loader_request_id:
      return

    self.loader_versions_ready = False
    self.version_combobox.configure(state="readonly")
    self.loader_version_combobox.configure(values=[], state="disabled")
    self.update_status(f"Lỗi tải danh sách mod loader: {error}")

  def on_game_version_changed(self, selected=None):
    loader_id = MOD_LOADER_IDS[self.loader_combobox.get()]
    game_version = selected or self.version_combobox.get()
    if loader_id is None or not game_version:
      return

    self.loader_request_id += 1
    request_id = self.loader_request_id
    self.loader_versions_ready = False
    self.loader_version_combobox.configure(state="disabled")
    self.update_status(
        f"Đang tải phiên bản mod loader cho Minecraft {game_version}..."
    )
    threading.Thread(
        target=self.fetch_loader_versions,
        args=(loader_id, game_version, request_id),
        daemon=True,
    ).start()

  def fetch_loader_versions(self, loader_id, game_version, request_id):
    try:
      loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_id)
      loader_versions = loader.get_loader_versions(game_version, True)
      if not loader_versions:
        loader_versions = loader.get_loader_versions(game_version, False)
      if not loader_versions:
        raise RuntimeError(
            f"Không tìm thấy phiên bản {loader.get_name()} cho Minecraft "
            f"{game_version}."
        )

      self.after(
          0,
          lambda: self.apply_selected_loader_versions(
              request_id, loader_versions
          ),
      )
    except Exception as exc:
      error = str(exc)
      self.after(0, lambda: self.show_loader_error(request_id, error))

  def apply_selected_loader_versions(self, request_id, loader_versions):
    if request_id != self.loader_request_id:
      return

    self.loader_version_combobox.configure(
        values=loader_versions, state="readonly"
    )
    self.loader_version_combobox.set(loader_versions[0])
    self.loader_versions_ready = True
    self.update_status("Trạng thái: Sẵn sàng")

  def update_status(self, text):
    print(f"[STATUS] {text}")
    self.after(0, lambda: self.status_label.configure(text=text))

  def handle_main_action(self):
    if self.is_downloading:
      self.cancel_download = True
      self.update_status("Đã hủy quá trình tải xuống!")
      self.reset_to_launch_btn()

    elif self.game_process and self.game_process.poll() is None:
      try:
        self.game_process.terminate()
        self.update_status("Đã đóng trò chơi Minecraft!")
      except Exception as exc:
        print(f"Lỗi tắt game: {exc}")
      self.reset_to_launch_btn()

    else:
      username = self.username_entry.get().strip()
      version = self.version_combobox.get()
      loader_id = MOD_LOADER_IDS[self.loader_combobox.get()]
      loader_version = (
          self.loader_version_combobox.get() if loader_id else None
      )
      launcher_profile = (
          self.active_launcher_profile["data"]
          if self.active_launcher_profile
          else None
      )

      if not username and not self.account_data:
        messagebox.showwarning("Cảnh báo", "Vui lòng nhập tên người chơi!")
        return

      if launcher_profile is None and loader_id and (
          not self.loader_versions_ready or not loader_version
      ):
        messagebox.showwarning(
            "Cảnh báo", "Vui lòng chờ tải xong danh sách phiên bản mod loader!"
        )
        return

      self.is_downloading = True
      self.cancel_download = False

      self.main_action_button.configure(
          text="CANCEL (HỦY TẢI FILE)",
          fg_color="#f97316",
          hover_color="#ea580c",
      )

      threading.Thread(
          target=self.launch_game,
          args=(
              username,
              version,
              loader_id if launcher_profile is None else None,
              loader_version if launcher_profile is None else None,
              launcher_profile,
          ),
          daemon=True,
      ).start()

  def reset_to_launch_btn(self):
    self.is_downloading = False
    self.game_process = None
    self.after(
        0,
        lambda: self.main_action_button.configure(
            text="LAUNCH GAME", fg_color="#22c55e", hover_color="#16a34a"
        ),
    )

  def launch_game(
      self, username, version, loader_id, loader_version, launcher_profile=None
  ):
    try:
      self.update_status("Kiểm tra thư mục game...")
      os.makedirs(self.minecraft_directory, exist_ok=True)

      if self.cancel_download:
        return

      read_launcher_profiles(self.minecraft_directory)

      max_val = 100

      def set_max(val):
        nonlocal max_val
        max_val = val if val > 0 else 100

      def update_progress(val):
        if self.cancel_download:
          return
        try:
          progress_percent = min(max(val / max_val, 0), 1)
          self.after(0, lambda: self.progress_bar.set(progress_percent))
        except Exception:
          pass

      callback = {
          "setStatus": self.update_status,
          "setMax": set_max,
          "setProgress": update_progress,
          "setValue": update_progress,
      }

      profile_options = {}
      if launcher_profile:
        version, profile_options = launcher_profile_to_options(
            launcher_profile
        )
        self.update_status(
            f"Đang chuẩn bị profile {launcher_profile['name']}..."
        )
        self.install_vanilla_version(version, callback)
        installed_version = version
      elif loader_id:
        loader = minecraft_launcher_lib.mod_loader.get_mod_loader(loader_id)
        self.update_status(
            f"Đang cài {loader.get_name()} cho Minecraft {version}..."
        )
        installed_version = loader.install(
            version,
            self.minecraft_directory,
            loader_version=loader_version,
            callback=callback,
            java=self.java_path if os.path.exists(self.java_path) else None,
        )
        profile_id = register_launcher_profile(
            self.minecraft_directory,
            f"{loader_id}:{version}:{loader_version}",
            f"RCL - {loader.get_name()} {version}",
            installed_version,
        )
        self.after(
            0,
            lambda: self.refresh_launcher_profiles(
                selected_profile_id=profile_id
            ),
        )
      else:
        self.update_status(f"Đang tải các tệp phiên bản {version}...")
        self.install_vanilla_version(version, callback)
        installed_version = version

      if self.cancel_download:
        return

      self.update_status("Đang chuẩn bị tham số...")
      self.progress_bar.set(1.0)

      # Cấu hình tài khoản Online / Offline
      if self.account_data:
        options = {
            "username": self.account_data["name"],
            "uuid": self.account_data["id"],
            "token": self.account_data["access_token"],
        }
      else:
        options = {
            "username": username,
            "uuid": generate_offline_uuid(username),
            "token": "",
        }

      options = {**profile_options, **options}
      jvm_args = [
          arg for arg in profile_options.get("jvmArguments", [])
          if not arg.startswith("-Xmx")
      ]
      jvm_args.append(f"-Xmx{self.ram_allocation}")
      options["jvmArguments"] = jvm_args

      if (
          self.java_path
          and os.path.exists(self.java_path)
          and "executablePath" not in profile_options
      ):
        options["executablePath"] = self.java_path

      minecraft_command = minecraft_launcher_lib.command.get_minecraft_command(
          installed_version, self.minecraft_directory, options
      )

      if self.cancel_download:
        return

      self.is_downloading = False
      self.update_status("Minecraft đang chạy...")

      self.game_process = subprocess.Popen(minecraft_command)

      self.after(
          0,
          lambda: self.main_action_button.configure(
              text="STOP (TẮT MINECRAFT)",
              fg_color="#ef4444",
              hover_color="#dc2626",
          ),
      )

      self.game_process.wait()

      self.update_status("Đã thoát game. Trạng thái: Sẵn sàng")
      self.reset_to_launch_btn()

    except Exception as exc:
      print(f"Lỗi: {str(exc)}")
      if not self.cancel_download:
        if isinstance(
            exc,
            (requests.exceptions.ConnectionError, requests.exceptions.Timeout),
        ):
          message = (
              "Kết nối tới máy chủ tải Minecraft bị gián đoạn sau 3 lần "
              "thử lại. Hãy kiểm tra Internet/proxy rồi thử lại.\n\n"
              f"Chi tiết: {exc}"
          )
          messagebox.showerror("Lỗi kết nối", message)
        else:
          messagebox.showerror("Lỗi", f"Không thể bật game: {str(exc)}")
        self.update_status("Trạng thái: Lỗi")
      self.reset_to_launch_btn()


if __name__ == "__main__":
  app = RCLLauncher()
  app.mainloop()