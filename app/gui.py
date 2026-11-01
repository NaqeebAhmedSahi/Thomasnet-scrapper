from __future__ import annotations

import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

from app.chrome_tabs import chrome_profiles, focus_scraper_window, focus_window, visible_tabs
from app.config import DEFAULT_OUTPUT, DEFAULT_SEARCH, PROJECT_ROOT, Job
from app.scraper import Hooks, run, setup_logging
from app.storage import Storage

FONT = "DejaVu Sans"
BG = "#e7eef5"
NAVY = "#0c2d48"
CARD = "#ffffff"
INK = "#13293d"
MUTED = "#5d6d7e"
LINE = "#d3deea"
ACCENT = "#1456b8"
TEAL = "#0f6e68"
DANGER = "#a3262b"
AMBER = "#8a5a00"
SETTINGS_PATH = PROJECT_ROOT / ".cache" / "gui_settings.json"


class GuiHooks(Hooks):
    def __init__(self, events: queue.Queue, stop_event: threading.Event, pause_event: threading.Event):
        self.events = events
        self.stop_event = stop_event
        self.pause_event = pause_event
        self._lock = threading.Lock()
        self._profile: str | None = None
        self._tab: dict | None = None
        self._click = False
        self._signin_sent = False

    def request_profile(self, profile_dir: str) -> None:
        with self._lock:
            self._profile = profile_dir

    def request_tab(self, item: dict) -> None:
        with self._lock:
            self._tab = dict(item)

    def pull_tab(self) -> dict | None:
        with self._lock:
            value = getattr(self, "_tab", None)
            self._tab = None
            return value

    def request_signin_click(self) -> None:
        with self._lock:
            self._click = True

    def pull_profile(self) -> str | None:
        with self._lock:
            value = self._profile
            self._profile = None
            return value

    def pull_signin_click(self) -> bool:
        with self._lock:
            value = self._click
            self._click = False
            return value

    def needs_signin(self) -> None:
        if self._signin_sent:
            return
        self._signin_sent = True
        self.events.put(("signin", "Thomasnet needs a sign-up in the scraper Chrome. Finish it in that window and leave the window open."))

    def log(self, message: str) -> None:
        import logging

        logging.getLogger("thomasnet").info(message)
        self.events.put(("log", message))

    def progress(self, data: dict) -> None:
        self.events.put(("progress", data))

    def supplier(self, row: dict) -> None:
        self.events.put(("row", row))

    def stopped(self) -> bool:
        return self.stop_event.is_set()

    def wait_if_paused(self) -> None:
        while not self.pause_event.is_set():
            if self.stop_event.is_set():
                return
            time.sleep(0.15)


class ScraperApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Thomasnet Supplier Scraper")
        self.geometry("1180x900")
        self.minsize(980, 760)
        self.configure(bg=BG)
        self.events: queue.Queue = queue.Queue()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.worker: threading.Thread | None = None
        self.running = False
        self.row_urls: dict[str, str] = {}
        self.form_widgets: list[tk.Widget] = []
        self.chrome_items: list[dict] = []
        self.chrome_profiles: list[dict] = []
        self.hooks: GuiHooks | None = None
        self._build_styles()
        self._build()
        self._load_settings()
        self._load_saved_rows()
        self.after(120, self._poll)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_styles(self) -> None:
        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure(".", font=(FONT, 10), background=BG, foreground=INK)
        style.configure("TFrame", background=BG)
        style.configure("Card.TFrame", background=CARD)
        style.configure("TLabel", background=BG, foreground=INK, font=(FONT, 10))
        style.configure("Card.TLabel", background=CARD, foreground=INK, font=(FONT, 10))
        style.configure("Muted.TLabel", background=CARD, foreground=MUTED, font=(FONT, 9))
        style.configure("Hint.TLabel", background=BG, foreground=MUTED, font=(FONT, 9))
        style.configure("TEntry", fieldbackground="white", padding=6)
        style.configure("TCheckbutton", background=CARD, foreground=INK, font=(FONT, 10))
        style.configure("TNotebook", background=BG, borderwidth=0)
        style.configure("TNotebook.Tab", padding=(14, 8), font=(FONT, 10))
        style.configure("Treeview", font=(FONT, 10), rowheight=28, background="white", fieldbackground="white")
        style.configure("Treeview.Heading", font=(FONT, 10, "bold"), background="#e8f0f8", foreground=INK)
        style.configure(
            "Teal.Horizontal.TProgressbar",
            troughcolor="#d5e0ea",
            background=TEAL,
            bordercolor="#d5e0ea",
            lightcolor=TEAL,
            darkcolor=TEAL,
        )
        style.configure("Accent.TButton", background=ACCENT, foreground="white", padding=(16, 8), font=(FONT, 10, "bold"))
        style.map("Accent.TButton", background=[("active", "#0f4696"), ("disabled", "#9bb0cc")])
        style.configure("Ghost.TButton", background="#e8eef6", foreground=INK, padding=(14, 8), font=(FONT, 10))
        style.map("Ghost.TButton", background=[("active", "#d5e1f0"), ("disabled", "#eef2f6")])
        style.configure("Danger.TButton", background=DANGER, foreground="white", padding=(14, 8), font=(FONT, 10, "bold"))
        style.map("Danger.TButton", background=[("active", "#861f24"), ("disabled", "#e3b4b6")])

    def _build(self) -> None:
        header = tk.Frame(self, bg=NAVY, height=84)
        header.pack(fill="x")
        header.pack_propagate(False)
        tk.Label(
            header,
            text="Thomasnet Supplier Scraper",
            bg=NAVY,
            fg="white",
            font=(FONT, 18, "bold"),
        ).pack(anchor="w", padx=22, pady=(16, 0))
        tk.Label(
            header,
            text="Collect supplier profiles page by page, then save companies and products.",
            bg=NAVY,
            fg="#c5d5e4",
            font=(FONT, 10),
        ).pack(anchor="w", padx=22)
        self.status_pill = tk.Label(
            header,
            text="  IDLE  ",
            bg="#1d4e73",
            fg="white",
            font=(FONT, 9, "bold"),
            padx=8,
            pady=3,
        )
        self.status_pill.place(relx=1.0, x=-22, y=26, anchor="ne")

        body = ttk.Frame(self, padding=(16, 14, 16, 8))
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=1)
        body.rowconfigure(4, weight=1)

        settings = self._card(body)
        settings.grid(row=0, column=0, sticky="ew")
        self._build_settings(settings)

        controls = ttk.Frame(body)
        controls.grid(row=1, column=0, sticky="ew", pady=(10, 0))
        self._build_controls(controls)

        self.chrome_card = self._card(body)
        self.chrome_card.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self._build_chrome(self.chrome_card)

        progress_card = self._card(body)
        progress_card.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        self._build_progress(progress_card)

        notebook = ttk.Notebook(body)
        notebook.grid(row=4, column=0, sticky="nsew", pady=(10, 0))
        self._build_table(notebook)
        self._build_log(notebook)

        status = tk.Frame(self, bg="#dbe5ef", height=28)
        status.pack(fill="x", side="bottom")
        self.status_line = tk.Label(
            status,
            text="Ready. Saved files update after every supplier.",
            bg="#dbe5ef",
            fg=INK,
            font=(FONT, 9),
            anchor="w",
        )
        self.status_line.pack(fill="x", padx=14, pady=4)

    def _card(self, parent) -> tk.Frame:
        frame = tk.Frame(parent, bg=CARD, highlightbackground=LINE, highlightthickness=1, bd=0, padx=14, pady=12)
        return frame

    def _build_settings(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        tk.Label(parent, text="Search term", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w")
        self.search_var = tk.StringVar(value=DEFAULT_SEARCH)
        search = ttk.Entry(parent, textvariable=self.search_var)
        search.grid(row=1, column=0, columnspan=6, sticky="ew", pady=(2, 10))
        search.bind("<Return>", lambda _event: self._start())
        self.form_widgets.append(search)

        self.start_var = tk.StringVar(value="1")
        self.pages_var = tk.StringVar(value="")
        self.limit_var = tk.StringVar(value="")
        self.delay_var = tk.StringVar(value="2.5")
        self.output_var = tk.StringVar(value=str(DEFAULT_OUTPUT))
        self.headless_var = tk.BooleanVar(value=False)
        self.resume_var = tk.BooleanVar(value=True)

        fields = [
            (2, "Start page", self.start_var, 10),
            (3, "Max pages", self.pages_var, 12),
            (4, "Max new suppliers", self.limit_var, 16),
            (5, "Delay (seconds)", self.delay_var, 12),
        ]
        for column, label, variable, width in fields:
            tk.Label(parent, text=label, bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=2, column=column - 2, sticky="w", padx=(0, 12))
            entry = ttk.Entry(parent, textvariable=variable, width=width)
            entry.grid(row=3, column=column - 2, sticky="ew", padx=(0, 12), pady=(2, 8))
            self.form_widgets.append(entry)
        parent.columnconfigure(4, weight=1)

        tk.Label(parent, text="Output folder", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=4, column=0, sticky="w")
        output_row = tk.Frame(parent, bg=CARD)
        output_row.grid(row=5, column=0, columnspan=4, sticky="ew")
        output_row.columnconfigure(0, weight=1)
        output = ttk.Entry(output_row, textvariable=self.output_var)
        output.grid(row=0, column=0, sticky="ew")
        self.form_widgets.append(output)
        browse = ttk.Button(output_row, text="Browse", style="Ghost.TButton", command=self._browse)
        browse.grid(row=0, column=1, padx=(8, 0))
        self.form_widgets.append(browse)

        checks = tk.Frame(parent, bg=CARD)
        checks.grid(row=5, column=4, sticky="e")
        headless = ttk.Checkbutton(checks, text="Headless Chrome", variable=self.headless_var)
        headless.pack(side="left", padx=(0, 12))
        resume = ttk.Checkbutton(checks, text="Skip suppliers already saved", variable=self.resume_var)
        resume.pack(side="left")
        self.form_widgets.extend([headless, resume])
        tk.Label(
            parent,
            text="Leave max pages and max new suppliers blank to scrape the full result set. Start opens the Chrome profile you pick. Your other Chrome windows stay open.",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
        ).grid(row=6, column=0, columnspan=5, sticky="w", pady=(8, 0))

    def _build_controls(self, parent: ttk.Frame) -> None:
        self.start_button = ttk.Button(parent, text="Start", style="Accent.TButton", command=self._start)
        self.start_button.pack(side="left")
        self.pause_button = ttk.Button(parent, text="Pause", style="Ghost.TButton", command=self._toggle_pause, state="disabled")
        self.pause_button.pack(side="left", padx=(8, 0))
        self.stop_button = ttk.Button(parent, text="Stop", style="Danger.TButton", command=self._stop, state="disabled")
        self.stop_button.pack(side="left", padx=(8, 0))
        self.open_button = ttk.Button(parent, text="Open output folder", style="Ghost.TButton", command=self._open_folder)
        self.open_button.pack(side="left", padx=(8, 0))
        self.clear_button = ttk.Button(parent, text="Clear saved data", style="Ghost.TButton", command=self._clear)
        self.clear_button.pack(side="left", padx=(8, 0))

    def _build_chrome(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        tk.Label(parent, text="Chrome tabs", bg=CARD, fg=INK, font=(FONT, 11, "bold"), anchor="w").grid(
            row=0, column=0, sticky="w"
        )
        self.chrome_hint = tk.Label(
            parent,
            text="Open Chrome windows are listed here. Choosing one brings it forward and does not close the others.",
            bg=CARD,
            fg=MUTED,
            font=(FONT, 9),
            anchor="w",
        )
        self.chrome_hint.grid(row=1, column=0, sticky="ew", pady=(2, 8))

        row = tk.Frame(parent, bg=CARD)
        row.grid(row=2, column=0, sticky="ew")
        row.columnconfigure(1, weight=1)
        tk.Label(row, text="Profile", bg=CARD, fg=MUTED, font=(FONT, 9)).grid(row=0, column=0, sticky="w", padx=(0, 8))
        self.profile_var = tk.StringVar()
        self.profile_box = ttk.Combobox(row, textvariable=self.profile_var, state="readonly")
        self.profile_box.grid(row=0, column=1, sticky="ew")
        self.form_widgets.append(self.profile_box)
        self._load_profile_choices()
        refresh = ttk.Button(row, text="Refresh tabs", style="Ghost.TButton", command=self._refresh_tabs)
        refresh.grid(row=0, column=2, padx=(8, 0))

        self.tab_list = tk.Listbox(
            parent,
            height=6,
            bg="white",
            fg=INK,
            font=(FONT, 10),
            activestyle="none",
            selectbackground="#d9e8f8",
            selectforeground=INK,
            highlightthickness=1,
            highlightbackground=LINE,
            relief="flat",
            exportselection=False,
        )
        self.tab_list.grid(row=3, column=0, sticky="ew", pady=(8, 8))
        self.tab_list.bind("<Double-1>", lambda _event: self._open_selected_tab())

        actions = tk.Frame(parent, bg=CARD)
        actions.grid(row=4, column=0, sticky="w")
        ttk.Button(actions, text="Show scraper window", style="Ghost.TButton", command=self._open_selected_tab).pack(side="left")
        ttk.Button(actions, text="Sign in", style="Accent.TButton", command=self._sign_in).pack(side="left", padx=(8, 0))
        self._refresh_tabs()

    def _load_profile_choices(self) -> None:
        self.chrome_profiles = chrome_profiles()
        labels = [item["label"] for item in self.chrome_profiles]
        self.profile_box.configure(values=labels)
        if labels and not self.profile_var.get():
            self.profile_var.set(labels[0])

    def _selected_profile(self) -> dict:
        label = self.profile_var.get()
        for item in self.chrome_profiles:
            if item["label"] == label:
                return item
        return self.chrome_profiles[0] if self.chrome_profiles else {"dir": "Default", "label": "Your Chrome"}

    def _refresh_tabs(self) -> None:
        self.chrome_items = visible_tabs()
        self.tab_list.delete(0, "end")
        if not self.chrome_items:
            self.tab_list.insert("end", "No Chrome window is open")
            self.chrome_hint.configure(text="No Chrome window is open. Open Chrome, then click Refresh tabs.")
            return
        self.chrome_hint.configure(
            text="Open Chrome windows are listed here. Choosing one brings it forward and does not close the others.",
            fg=MUTED,
        )
        for item in self.chrome_items:
            self.tab_list.insert("end", item["label"])
        self.tab_list.selection_clear(0, "end")
        self.tab_list.selection_set(0)
        self.tab_list.activate(0)
        self.tab_list.see(0)

    def _selected_chrome_item(self) -> dict | None:
        selection = self.tab_list.curselection()
        if not selection:
            return None
        index = selection[0]
        if index < 0 or index >= len(self.chrome_items):
            return None
        return self.chrome_items[index]

    def _open_selected_tab(self) -> None:
        item = self._selected_chrome_item()
        if not item:
            self._refresh_tabs()
            return
        if item.get("window_id"):
            focus_window(item["window_id"])
        elif item.get("scraper"):
            focus_scraper_window()
        else:
            return
        if self.hooks is not None and self.running and item.get("scraper"):
            self.hooks.request_tab(item)
        self._append_log(f"Brought forward {item['label']}. Other Chrome windows stay open.")
        self._set_status_line(f"Brought forward {item['label']}.")

    def _sign_in(self) -> None:
        focus_scraper_window()
        item = self._selected_chrome_item()
        if self.hooks is not None and self.running:
            if item:
                self.hooks.request_tab(item)
            self.hooks.request_signin_click()
            self._append_log("Signing in inside the scraper Chrome. Other Chrome windows stay open.")
            self._set_status("SIGN IN", AMBER)
            self._set_status_line("Sign in inside the scraper Chrome window.")
            return
        self._append_log("Click Start first. Sign in happens in the scraper Chrome only.")
        self._set_status_line("Click Start. Your other Chrome windows stay open.")

    def _mark_signin(self, message: str) -> None:
        self.chrome_card.configure(highlightbackground=AMBER, highlightthickness=2)
        self.chrome_hint.configure(text=message, fg=AMBER)
        self._set_status("SIGN IN", AMBER)
        self._set_status_line(message)
        self._refresh_tabs()

    def _build_progress(self, parent: tk.Frame) -> None:
        parent.columnconfigure(0, weight=1)
        self.progress_label = tk.Label(parent, text="Waiting to start", bg=CARD, fg=INK, font=(FONT, 10, "bold"), anchor="w")
        self.progress_label.grid(row=0, column=0, sticky="ew")
        self.progress = ttk.Progressbar(parent, style="Teal.Horizontal.TProgressbar", mode="determinate", maximum=100)
        self.progress.grid(row=1, column=0, sticky="ew", pady=(8, 6))
        self.current_label = tk.Label(parent, text="Current supplier: none", bg=CARD, fg=MUTED, font=(FONT, 9), anchor="w")
        self.current_label.grid(row=2, column=0, sticky="ew")

    def _build_table(self, notebook: ttk.Notebook) -> None:
        wrapper = ttk.Frame(notebook)
        notebook.add(wrapper, text="Suppliers")
        columns = ("name", "phone", "website", "verified", "location", "type", "employees", "sales", "founded", "catalog", "page")
        self.tree = ttk.Treeview(wrapper, columns=columns, show="headings", selectmode="browse")
        headings = {
            "name": ("Company", 220),
            "phone": ("Phone", 140),
            "website": ("Website", 220),
            "verified": ("Verified", 80),
            "location": ("Location", 160),
            "type": ("Type", 130),
            "employees": ("Employees", 90),
            "sales": ("Annual sales", 110),
            "founded": ("Founded", 80),
            "catalog": ("Catalog items", 100),
            "page": ("Page", 60),
        }
        for key, (title, width) in headings.items():
            self.tree.heading(key, text=title, anchor="w")
            self.tree.column(key, width=width, anchor="w", stretch=(key == "name"))
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.tree.yview)
        xscroll = ttk.Scrollbar(wrapper, orient="horizontal", command=self.tree.xview)
        self.tree.configure(yscrollcommand=scroll.set, xscrollcommand=xscroll.set)
        scroll.pack(side="right", fill="y")
        xscroll.pack(side="bottom", fill="x")
        self.tree.pack(side="left", fill="both", expand=True)
        self.tree.tag_configure("odd", background="#f4f8fb")
        self.tree.bind("<Double-1>", self._open_selected)

    def _build_log(self, notebook: ttk.Notebook) -> None:
        wrapper = ttk.Frame(notebook)
        notebook.add(wrapper, text="Log")
        self.log = tk.Text(
            wrapper,
            bg="#101820",
            fg="#d7e3ee",
            insertbackground="white",
            font=("DejaVu Sans Mono", 10),
            wrap="word",
            padx=12,
            pady=10,
            relief="flat",
        )
        scroll = ttk.Scrollbar(wrapper, orient="vertical", command=self.log.yview)
        self.log.configure(yscrollcommand=scroll.set)
        self.log.pack(side="left", fill="both", expand=True)
        scroll.pack(side="right", fill="y")

    def _browse(self) -> None:
        chosen = filedialog.askdirectory(initialdir=self.output_var.get() or str(PROJECT_ROOT))
        if chosen:
            self.output_var.set(chosen)
            self._load_saved_rows()

    def _start(self) -> None:
        if self.worker and self.worker.is_alive():
            return
        try:
            job = self._job_from_form()
        except ValueError as exc:
            messagebox.showerror("Check the form", str(exc), parent=self)
            return
        self._save_settings()
        setup_logging()
        self.stop_event = threading.Event()
        self.pause_event = threading.Event()
        self.pause_event.set()
        self.hooks = GuiHooks(self.events, self.stop_event, self.pause_event)
        self.running = True
        self._set_running(True)
        self._set_status("RUNNING", TEAL)
        self._set_status_line(f"Running. Output folder: {job.output_dir}")
        profile = self._selected_profile()
        self._append_log(f"Job started for \"{job.search_term}\" with {profile['label']}. Other Chrome windows stay open.")
        self.worker = threading.Thread(target=self._worker, args=(job,), daemon=True)
        self.worker.start()

    def _worker(self, job: Job) -> None:
        try:
            summary = run(job, self.hooks)
            self.events.put(("done", summary))
        except Exception as exc:
            self.events.put(("error", str(exc)))

    def _toggle_pause(self) -> None:
        if not self.running:
            return
        if self.pause_event.is_set():
            self.pause_event.clear()
            self.pause_button.configure(text="Resume")
            self._set_status("PAUSED", AMBER)
            self._append_log("Paused. Click Resume to continue.")
        else:
            self.pause_event.set()
            self.pause_button.configure(text="Pause")
            self._set_status("RUNNING", TEAL)
            self._append_log("Resumed.")

    def _stop(self) -> None:
        if not self.running:
            return
        self.stop_event.set()
        self.pause_event.set()
        self.stop_button.configure(state="disabled")
        self.pause_button.configure(state="disabled")
        self._set_status("STOPPING", AMBER)
        self._append_log("Stopping after the current step. Saved rows stay on disk.")

    def _poll(self) -> None:
        try:
            while True:
                kind, payload = self.events.get_nowait()
                if kind == "log":
                    self._append_log(payload)
                elif kind == "progress":
                    self._show_progress(payload)
                elif kind == "row":
                    self._insert_row(payload)
                elif kind == "signin":
                    self._mark_signin(payload)
                elif kind == "done":
                    self._finish(payload)
                elif kind == "error":
                    self._finish({"error": payload, "stopped": False})
        except queue.Empty:
            pass
        self.after(120, self._poll)

    def _finish(self, summary: dict) -> None:
        self.running = False
        self._set_running(False)
        self.progress.stop()
        if summary.get("error"):
            self._set_status("ERROR", DANGER)
            self._set_status_line(summary["error"])
            self._append_log(summary["error"])
            return
        if summary.get("stopped"):
            self._set_status("STOPPED", AMBER)
            self._set_status_line("Stopped. You can start again and it will skip suppliers already saved.")
            return
        saved = summary.get("saved", 0)
        skipped = summary.get("skipped", 0)
        self._set_status("DONE", TEAL)
        self._set_status_line(f"Finished. {saved} new suppliers saved, {skipped} skipped.")

    def _show_progress(self, data: dict) -> None:
        page = data.get("page") or 0
        total_pages = data.get("total_pages") or 0
        total = data.get("total_results") or 0
        saved = data.get("saved") or 0
        skipped = data.get("skipped") or 0
        products = data.get("products") or 0
        page_text = f"Page {page} of {total_pages}" if total_pages else f"Page {page}"
        self.progress_label.configure(
            text=f"{page_text}    Saved {saved}    Skipped {skipped}    Catalog names {products}"
        )
        current = data.get("current") or "none"
        self.current_label.configure(text=f"Current supplier: {current}")
        if total:
            self.progress.stop()
            self.progress.configure(mode="determinate", maximum=total, value=min(saved, total))
        elif str(self.progress.cget("mode")) != "indeterminate":
            self.progress.configure(mode="indeterminate", maximum=100)
            self.progress.start(12)

    def _insert_row(self, supplier: dict, reveal: bool = True) -> None:
        company_id = str(supplier.get("company_id") or supplier.get("profile_url") or "")
        if not company_id:
            return
        catalog = len(supplier.get("categories") or []) + len(supplier.get("products_services") or []) + len(supplier.get("brands") or [])
        values = (
            supplier.get("name", ""),
            supplier.get("phone", ""),
            supplier.get("website", ""),
            supplier.get("thomas_verified", ""),
            supplier.get("location", ""),
            supplier.get("primary_company_type") or supplier.get("company_types") or "",
            supplier.get("employees", ""),
            supplier.get("annual_sales", ""),
            supplier.get("year_founded", ""),
            catalog,
            supplier.get("page_number", ""),
        )
        tag = "odd" if len(self.tree.get_children()) % 2 else ""
        if self.tree.exists(company_id):
            self.tree.item(company_id, values=values)
        else:
            self.tree.insert("", "end", iid=company_id, values=values, tags=(tag,))
        self.row_urls[company_id] = supplier.get("profile_url") or ""
        if reveal:
            self.tree.see(company_id)

    def _load_saved_rows(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.row_urls.clear()
        path = Path(self.output_var.get()) / "suppliers.json"
        if not path.exists():
            return
        try:
            rows = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        if not isinstance(rows, list):
            return
        for row in rows:
            if isinstance(row, dict):
                self._insert_row(row, reveal=False)
        children = self.tree.get_children()
        if children:
            self.tree.see(children[-1])
            self._set_status_line(f"{len(children)} saved suppliers in {path.parent}")

    def _append_log(self, message: str) -> None:
        stamp = time.strftime("%H:%M:%S")
        self.log.insert("end", f"[{stamp}] {message}\n")
        self.log.see("end")

    def _job_from_form(self) -> Job:
        search = self.search_var.get().strip()
        if not search:
            raise ValueError("Enter a search term.")
        start_text = self.start_var.get().strip() or "1"
        if not start_text.isdigit() or int(start_text) < 1:
            raise ValueError("Start page must be a whole number of 1 or more.")
        try:
            delay = float(self.delay_var.get().strip() or "2.5")
        except ValueError as exc:
            raise ValueError("Delay must be a number, such as 2.5.") from exc
        if delay < 0.5:
            raise ValueError("Use a delay of at least 0.5 seconds.")
        output = self.output_var.get().strip()
        if not output:
            raise ValueError("Choose an output folder.")
        return Job(
            search_term=search,
            start_page=int(start_text),
            max_pages=self._optional_int(self.pages_var.get(), "Max pages"),
            max_suppliers=self._optional_int(self.limit_var.get(), "Max new suppliers"),
            delay=delay,
            headless=bool(self.headless_var.get()),
            output_dir=output,
            resume=bool(self.resume_var.get()),
            chrome_profile=self._selected_profile()["dir"],
        )

    def _optional_int(self, value: str, label: str) -> int:
        value = value.strip()
        if not value:
            return 0
        if not value.isdigit() or int(value) < 1:
            raise ValueError(f"{label} must be a positive whole number, or left blank.")
        return int(value)

    def _open_folder(self) -> None:
        folder = Path(self.output_var.get().strip() or DEFAULT_OUTPUT)
        folder.mkdir(parents=True, exist_ok=True)
        try:
            if sys.platform == "win32":
                os.startfile(folder)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(folder)])
            else:
                subprocess.Popen(["xdg-open", str(folder)])
        except OSError:
            messagebox.showinfo("Output folder", str(folder), parent=self)

    def _clear(self) -> None:
        if self.running:
            return
        folder = self.output_var.get().strip()
        if not folder:
            return
        approved = messagebox.askyesno(
            "Clear saved data",
            "This deletes suppliers, products, profile links, and progress in the output folder.",
            parent=self,
        )
        if not approved:
            return
        Storage(folder).clear()
        self._load_saved_rows()
        self.progress.configure(value=0)
        self.progress_label.configure(text="Waiting to start")
        self.current_label.configure(text="Current supplier: none")
        self._set_status("IDLE", "#1d4e73")
        self._set_status_line("Saved data cleared.")
        self._append_log("Cleared the output folder.")

    def _open_selected(self, _event) -> None:
        url = self.row_urls.get(self.tree.focus(), "")
        if url:
            webbrowser.open(url)

    def _set_running(self, running: bool) -> None:
        form_state = "disabled" if running else "normal"
        for widget in self.form_widgets:
            widget.configure(state=form_state)
        self.start_button.configure(state="disabled" if running else "normal")
        self.pause_button.configure(state="normal" if running else "disabled", text="Pause")
        self.stop_button.configure(state="normal" if running else "disabled")
        self.clear_button.configure(state="disabled" if running else "normal")

    def _set_status(self, text: str, color: str) -> None:
        self.status_pill.configure(text=f"  {text}  ", bg=color)

    def _set_status_line(self, text: str) -> None:
        self.status_line.configure(text=text)

    def _load_settings(self) -> None:
        if not SETTINGS_PATH.exists():
            return
        try:
            saved = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return
        self.search_var.set(saved.get("search_term") or DEFAULT_SEARCH)
        self.start_var.set(str(saved.get("start_page") or 1))
        self.pages_var.set("" if not saved.get("max_pages") else str(saved.get("max_pages")))
        self.limit_var.set("" if not saved.get("max_suppliers") else str(saved.get("max_suppliers")))
        self.delay_var.set(str(saved.get("delay") or 2.5))
        self.output_var.set(saved.get("output_dir") or str(DEFAULT_OUTPUT))
        self.headless_var.set(bool(saved.get("headless")))
        self.resume_var.set(bool(saved.get("resume", True)))
        saved_profile = saved.get("chrome_profile") or "Default"
        for item in self.chrome_profiles:
            if item["dir"] == saved_profile:
                self.profile_var.set(item["label"])
                break

    def _save_settings(self) -> None:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "search_term": self.search_var.get().strip(),
            "start_page": self.start_var.get().strip(),
            "max_pages": self.pages_var.get().strip(),
            "max_suppliers": self.limit_var.get().strip(),
            "delay": self.delay_var.get().strip(),
            "output_dir": self.output_var.get().strip(),
            "headless": bool(self.headless_var.get()),
            "resume": bool(self.resume_var.get()),
            "chrome_profile": self._selected_profile()["dir"],
        }
        SETTINGS_PATH.write_text(json.dumps(payload, indent=2), encoding="utf-8")

    def _on_close(self) -> None:
        if self.running:
            approved = messagebox.askyesno(
                "Scraper is running",
                "Stop the scraper and close? Suppliers already saved will stay in the output folder.",
                parent=self,
            )
            if not approved:
                return
            self.stop_event.set()
            self.pause_event.set()
        self.destroy()


def main() -> None:
    app = ScraperApp()
    app.mainloop()
