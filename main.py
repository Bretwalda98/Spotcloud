#!/usr/bin/env python3
"""
SpotDL GUI (Android / Windows, No‑FFmpeg, m4a)
==============================================
* **Spotify** playlists → SpotDL → YouTube.
* **SoundCloud** sets handled through **yt‑dlp** – Python API → `python -m yt_dlp`
  → `yt-dlp(.exe)` fallback chain.
* Detects malformed URLs early and gives actionable guidance.
* Reports any tracks that could not be matched on YouTube.

Quick‑start
-----------
```bash
pip install spotdl yt-dlp            # tkinter is built‑in on Windows/macOS
python spotdl_gui_soundcloud_support.py
```
Run tests/headless mode:
```bash
python -m unittest spotdl_gui_soundcloud_support.py
```
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import threading
import queue
from typing import List, Dict, Any

# ──────────────────────────────────────────────────────────────────────────────
# Optional GUI deps (safe to import‑fail on CI/headless)
# ──────────────────────────────────────────────────────────────────────────────
try:
    import tkinter as tk  # type: ignore
    from tkinter import ttk, filedialog, messagebox  # type: ignore
    HAS_TK = True
except ModuleNotFoundError:
    HAS_TK = False

APP = "SpotCloud 0.2 Early Access Release"

# Modern, cross‑platform font family (falls back gracefully if missing)
FONT_FAMILY = "Segoe UI"
FONT_L, FONT_E = (FONT_FAMILY, 16, "bold"), (FONT_FAMILY, 12)

DEFAULT_OUT = "/sdcard/Download/spotdl_output"
os.makedirs(DEFAULT_OUT, exist_ok=True)

# ──────────────────────────────────────────────────────────────────────────────
# Helpers – no side‑effects, unit‑test friendly
# ──────────────────────────────────────────────────────────────────────────────

def build_spotdl_cmd(query: str, out_dir: str, bitrate: str = "320k", *, user_auth: bool = False) -> List[str]:
    """Generate a ready‑to‑run SpotDL command for *one* search/URL."""
    cmd = [
        sys.executable,
        "-m", "spotdl", "download", query,
        "--output", out_dir,
        "--format", "m4a",
        "--bitrate", bitrate,
    ]
    if user_auth:
        cmd.append("--user-auth")
    return cmd


def _extract_sc_queries_from_ydl_json(data: Dict[str, Any]) -> List[str]:
    """Return `["Artist – Title", …]` from a yt‑dlp info‑dict."""
    queries: List[str] = []
    for entry in data.get("entries", []) or []:
        title = (entry.get("title") or "").strip()
        uploader = (entry.get("uploader") or "").strip()
        if title:
            queries.append(f"{uploader} - {title}" if uploader else title)
    return queries


def _sanitize_sc_url(url: str) -> str:
    """Strip URL parameters/fragments that confuse yt‑dlp."""
    return url.split("?")[0].split("#")[0]


def get_soundcloud_queries(sc_url: str) -> List[str]:
    """Return YouTube‑searchable queries for every track in a SoundCloud *set*.

    Strategy tiers (stop at the first that yields ≥1 track):
    1. **yt‑dlp Python API** – try *flat* (fast) then *full* extraction.
    2. **`python -m yt_dlp`** CLI – same dual attempt.
    3. **Executable** `yt-dlp(.exe)` – same dual attempt.

    Each tier tries `extract_flat=True` first (cheap) and, if that produces no
    tracks, retries with the default full extraction to cope with SoundCloud
    edge‑cases where the flat API returns an empty list.
    """
    sc_url = _sanitize_sc_url(sc_url.strip())

    if not sc_url.startswith("http"):
        raise ValueError(
            "The URL does not appear to be a full SoundCloud link. "
            "Please paste the entire playlist URL, including https://soundcloud.com/…"
        )

    def _extract_with_ydl(ydl_cmd: List[str], flat: bool) -> List[str]:
        """Helper that runs yt‑dlp via subprocess and returns queries."""
        cmd = ydl_cmd + (["--flat-playlist"] if flat else []) + ["-J", sc_url]
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise RuntimeError(proc.stderr.strip() or proc.stdout.strip())
        return _extract_sc_queries_from_ydl_json(json.loads(proc.stdout))

    def _python_api(flat: bool) -> List[str]:
        from yt_dlp import YoutubeDL  # type: ignore
        ydl_opts = {"skip_download": True, "quiet": True}
        ydl_opts["extract_flat"] = "in_playlist" if flat else False
        with YoutubeDL(ydl_opts) as ydl:
            data = ydl.extract_info(sc_url, download=False)
        return _extract_sc_queries_from_ydl_json(data)

    errors: List[str] = []

    # ── Tier 1: Python API ───────────────────────────────────────────────────
    try:
        q = _python_api(flat=True)
        if not q:
            q = _python_api(flat=False)
        if q:
            return q
        errors.append("Python API returned no entries even after full extraction")
    except Exception as exc:
        errors.append(f"Python API error: {exc}")

    # ── Tier 2: python -m yt_dlp ────────────────────────────────────────────
    try:
        q = _extract_with_ydl([sys.executable, "-m", "yt_dlp"], flat=True)
        if not q:
            q = _extract_with_ydl([sys.executable, "-m", "yt_dlp"], flat=False)
        if q:
            return q
        errors.append("python -m yt_dlp returned no entries even after full extraction")
    except Exception as exc:
        errors.append(f"python -m yt_dlp error: {exc}")

    # ── Tier 3: bare executable ─────────────────────────────────────────────
    exe = shutil.which("yt-dlp") or shutil.which("yt-dlp.exe")
    if exe:
        try:
            q = _extract_with_ydl([exe], flat=True)
            if not q:
                q = _extract_with_ydl([exe], flat=False)
            if q:
                return q
            errors.append("yt-dlp executable returned no entries even after full extraction")
        except Exception as exc:
            errors.append(f"Executable error: {exc}")
    else:
        errors.append("yt-dlp executable not found on PATH")

    # No luck – consolidate collected errors and raise one RuntimeError
    full_msg = (
        "yt-dlp could not extract the SoundCloud set." + ".".join(errors)
    )
    raise RuntimeError(full_msg)

# ──────────────────────────────────────────────────────────────────────────────
# GUI layer – only when Tk present & script executed directly
# ──────────────────────────────────────────────────────────────────────────────
if HAS_TK and __name__ == "__main__":
    # ── Modern look & feel ---------------------------------------------------
    root = tk.Tk()
    root.title(APP)
    root.geometry("600x900")
    root.minsize(560, 820)

    # Dark theme palette
    BG, FG, ACCENT = "#1e1e1e", "#eaeaea", "#1db954"  # Spotify green accent
    root.configure(bg=BG)

    style = ttk.Style(root)
    try:
        style.theme_use("clam")  # Neutral, themeable ttk baseline
    except tk.TclError:
        pass  # fall back to default on some minimal Tk builds

    # Base styles -----------------------------------------------------------
    style.configure(".", background=BG, foreground=FG, font=(FONT_FAMILY, 11))
    style.configure("TFrame", background=BG)

    style.configure("TLabel", background=BG, foreground=FG, font=(FONT_FAMILY, 12))
    style.configure("Header.TLabel", font=(FONT_FAMILY, 16, "bold"), foreground="#ffffff", background=BG)

    style.configure(
        "TEntry",
        fieldbackground="#2b2b2b",
        foreground=FG,
        borderwidth=0,
        relief="flat",
        padding=4,
    )
    style.map("TEntry", fieldbackground=[("focus", "#393939")])

    style.configure(
        "Accent.TButton",
        background=ACCENT,
        foreground="#ffffff",
        font=(FONT_FAMILY, 12, "bold"),
        padding=(10, 6),
        borderwidth=0,
    )
    style.map(
        "Accent.TButton",
        background=[("active", "#1ed760"), ("disabled", "#3e3e3e")],
    )

    style.configure("TRadiobutton", background=BG, foreground=FG, font=(FONT_FAMILY, 12))
    style.configure("TCheckbutton", background=BG, foreground=FG, font=(FONT_FAMILY, 12))

    # GUI --------------------------------------------------------------------
    wrap = ttk.Frame(root, padding=16)
    wrap.pack(fill="both", expand=True)

    ttk.Label(wrap, text="Service:", font=FONT_L).pack(anchor="w")
    service_var = tk.StringVar(value="Spotify")
    svc = ttk.Frame(wrap)
    svc.pack(anchor="w", pady=4)
    ttk.Radiobutton(svc, text="Spotify", variable=service_var, value="Spotify").pack(side="left")
    ttk.Radiobutton(svc, text="SoundCloud", variable=service_var, value="SoundCloud").pack(side="left", padx=12)

    ttk.Label(wrap, text="Playlist URL:", font=FONT_L).pack(anchor="w", pady=(10, 0))
    url_var = tk.StringVar()
    ttk.Entry(wrap, textvariable=url_var, font=FONT_E).pack(fill="x", pady=6)

    ttk.Label(wrap, text="Output Folder:", font=FONT_L).pack(anchor="w", pady=(10, 0))
    row = ttk.Frame(wrap)
    row.pack(fill="x", pady=4)
    out_var = tk.StringVar(value=DEFAULT_OUT)
    ttk.Entry(row, textvariable=out_var, font=FONT_E).pack(side="left", fill="x", expand=True)
    ttk.Button(
        row,
        text="Browse",
        command=lambda: out_var.set(filedialog.askdirectory() or out_var.get()),
    ).pack(side="left", padx=6)

    ttk.Label(wrap, text="Bitrate:", font=FONT_L).pack(anchor="w", pady=(10, 0))
    bit_var = tk.StringVar(value="320k")
    ttk.Entry(wrap, textvariable=bit_var, width=8, font=FONT_E).pack(anchor="w")

    auth_var = tk.BooleanVar()
    ttk.Checkbutton(
        wrap,
        text="Use my Spotify account (for private playlists)",
        variable=auth_var,
    ).pack(anchor="w", pady=(4, 8))

    ttk.Label(wrap, text="Log:", font=FONT_L).pack(anchor="w")
    log_frame = ttk.Frame(wrap)
    log_frame.pack(fill="both", expand=True)

    log_txt = tk.Text(
        log_frame,
        wrap="word",
        font=("Consolas", 11),
        bg="#141414",
        fg=FG,
        insertbackground=FG,
        relief="flat",
        borderwidth=0,
        height=12,
    )
    ys = ttk.Scrollbar(log_frame, command=log_txt.yview)
    log_txt.configure(yscrollcommand=ys.set)
    ys.pack(side="right", fill="y")
    log_txt.pack(side="left", fill="both", expand=True)
    log_txt.bind("<Key>", lambda e: "break")

    progress = ttk.Progressbar(wrap, mode="indeterminate")
    progress.pack(fill="x", pady=(4, 0))

    q: queue.Queue[str] = queue.Queue()

    def log(msg: str):
        q.put(msg)

    def poll():
        try:
            while True:
                ln = q.get_nowait()
                log_txt.configure(state="normal")
                log_txt.insert("end", ln + "\n")
                log_txt.see("end")
                log_txt.configure(state="disabled")
        except queue.Empty:
            pass
        root.after(120, poll)

    # Downloaders -------------------------------------------------------------
    def run_spotdl(url: str, out_dir: str, service: str, use_auth: bool):
        try:
            os.makedirs(out_dir, exist_ok=True)
        except PermissionError:
            messagebox.showerror("Permission", "Cannot write to selected folder.")
            root.after(0, progress.stop)
            return
        if service == "Spotify":
            _do_spotify(url, out_dir, use_auth)
        else:
            _do_soundcloud(url, out_dir)
        log("✅ Finished")
        root.after(0, progress.stop)
        messagebox.showinfo("Done", "Playlist download completed!")

    def _do_spotify(purl: str, out_dir: str, use_auth: bool):
        cmd = build_spotdl_cmd(purl, out_dir, bit_var.get(), user_auth=use_auth)
        log("🏃 " + " ".join(cmd))
        _stream(cmd)

    def _do_soundcloud(scurl: str, out_dir: str):
        try:
            queries = get_soundcloud_queries(scurl)
        except Exception as exc:
            messagebox.showerror("SoundCloud", f"Error reading playlist:\n{exc}")
            return
        if not queries:
            messagebox.showwarning("SoundCloud", "No tracks found in this SoundCloud set.")
            return
        log(f"ℹ️  Found {len(queries)} tracks in SoundCloud playlist")
        missing: List[str] = []
        for idx, qtxt in enumerate(queries, 1):
            log(f"\n── {idx}/{len(queries)}: {qtxt} ──")
            ok = _stream(build_spotdl_cmd(qtxt, out_dir, bit_var.get()), success=True)
            if not ok:
                missing.append(qtxt)
        if missing:
            log("\n🚫 Could not download the following tracks:\n" + "\n".join(missing))

    def _stream(cmd: List[str], *, success: bool = False) -> bool:
        try:
            p = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
        except FileNotFoundError:
            messagebox.showerror("SpotDL missing", "Run `pip install spotdl`.")
            return False
        for line in p.stdout:
            log(line.rstrip())
        p.wait()
        return (p.returncode == 0) if success else True

    def validate_and_go():
        url = url_var.get().strip()
        svc = service_var.get()
        if svc == "Spotify" and "open.spotify.com/playlist/" not in url:
            messagebox.showerror("URL", "Enter a valid Spotify playlist URL.")
            return
        if svc == "SoundCloud" and "soundcloud.com/" not in url:
            messagebox.showerror("URL", "Enter a valid SoundCloud playlist URL.")
            return
        progress.start()
        threading.Thread(
            target=run_spotdl,
            args=(url, out_var.get().strip() or DEFAULT_OUT, svc, auth_var.get()),
            daemon=True,
        ).start()

    ttk.Button(
        wrap,
        text="⬇ Download Playlist ⬇",
        command=validate_and_go,
        style="Accent.TButton",
    ).pack(fill="x", ipady=10, pady=(6, 12))

    poll()
    root.mainloop()

# ──────────────────────────────────────────────────────────────────────────────
# Unit‑tests
# ──────────────────────────────────────────────────────────────────────────────
import unittest

SAMPLE_YDL_JSON = {"entries": [{"title": "Foo", "uploader": "Bar"}, {"title": "Baz"}]}


class TestHelpers(unittest.TestCase):
    def test_build(self):
        c = build_spotdl_cmd("query", "/tmp", "128k", user_auth=True)
        self.assertIn("--user-auth", c)
        self.assertIn("128k", c)

    def test_extract(self):
        self.assertEqual(_extract_sc_queries_from_ydl_json(SAMPLE_YDL_JSON), ["Bar - Foo", "Baz"])

    def test_sanitize(self):
        u = "https://soundcloud.com/user/playlist?utm_source=clip#frag"
        self.assertEqual(_sanitize_sc_url(u), "https://soundcloud.com/user/playlist")

    def test_bad_url(self):
        with self.assertRaises(ValueError):
            get_soundcloud_queries("m_source=clipboard&utm_medium=text")


if __name__ == "__main__":
    unittest.main()
