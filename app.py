import json
import os
import queue
import re
import threading
import webbrowser
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk
from tkinter.scrolledtext import ScrolledText

ANSI_TOKEN_RE = re.compile(r"(\x1b\[[0-?]*[ -/]*[@-~])")
URL_RE = re.compile(r"https?://[^\s<>'\"()]+")
ANSI_BASIC_COLORS = {
    30: "#000000",
    31: "#aa0000",
    32: "#008800",
    33: "#aa7700",
    34: "#0044aa",
    35: "#880088",
    36: "#008888",
    37: "#666666",
    90: "#555555",
    91: "#ff5555",
    92: "#22aa22",
    93: "#cc9900",
    94: "#3366ff",
    95: "#cc55cc",
    96: "#00aaaa",
    97: "#111111",
}


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("MVT GUI Wrapper")
        self.geometry("1120x760")
        self.minsize(980, 680)

        self.log_queue = queue.Queue()
        self.worker_thread = None
        self.is_running = False
        self.last_output_dir = None
        self.ansi_state = {"fg": None, "bold": False, "underline": False, "dim": False}
        self.ansi_tags = {}
        self.link_tags = {}
        self.settings_path = Path(__file__).with_name("mvt_gui_settings.json")

        self._build_state()
        self._build_ui()
        self._load_preferences()
        self.protocol("WM_DELETE_WINDOW", self._on_close)
        self.after(100, self._drain_log_queue)

    def _build_state(self):
        self.platform_var = tk.StringVar(value="ios")
        self.workflow_var = tk.StringVar(value="backup")
        self.iocs_var = tk.StringVar()
        self.input_path_var = tk.StringVar()
        self.output_dir_var = tk.StringVar()
        self.remember_inputs_var = tk.BooleanVar(value=False)

        self.status_var = tk.StringVar(value="Idle")
        self.current_cmd_var = tk.StringVar(value="No command running")
        self.summary_var = tk.StringVar(value="Run summary will appear here.")
        self.progress_var = tk.DoubleVar(value=0.0)

    def _build_ui(self):
        root = ttk.Frame(self, padding=12)
        root.pack(fill=tk.BOTH, expand=True)
        root.columnconfigure(0, weight=1)
        root.rowconfigure(1, weight=1)
        root.rowconfigure(2, weight=2)

        top = ttk.LabelFrame(root, text="Run Configuration", padding=10)
        top.grid(row=0, column=0, sticky="nsew")
        for i in range(3):
            top.columnconfigure(i, weight=1)

        self._build_platform_section(top)
        self._build_paths_section(top)
        self._build_controls_section(top)

        middle = ttk.LabelFrame(root, text="Execution", padding=10)
        middle.grid(row=1, column=0, sticky="nsew", pady=(10, 0))
        middle.columnconfigure(0, weight=1)
        middle.rowconfigure(2, weight=1)

        ttk.Label(middle, textvariable=self.status_var).grid(row=0, column=0, sticky="w")
        ttk.Label(middle, textvariable=self.current_cmd_var, foreground="#1d4ed8").grid(
            row=1, column=0, sticky="w", pady=(5, 6)
        )
        self.progress_bar = ttk.Progressbar(
            middle, maximum=100, variable=self.progress_var, mode="determinate"
        )
        self.progress_bar.grid(row=2, column=0, sticky="ew")

        self.command_list = tk.Listbox(middle, height=6)
        self.command_list.grid(row=3, column=0, sticky="nsew", pady=(10, 8))
        self.command_list.insert(tk.END, "No commands queued")

        logs = ttk.LabelFrame(root, text="Console Logs / Output", padding=10)
        logs.grid(row=2, column=0, sticky="nsew", pady=(10, 0))
        logs.columnconfigure(0, weight=1)
        logs.rowconfigure(0, weight=1)

        self.log_text = ScrolledText(logs, wrap=tk.WORD, state=tk.DISABLED, height=16)
        self.log_text.grid(row=0, column=0, sticky="nsew")
        self.log_base_font = tkfont.nametofont(self.log_text.cget("font"))

        summary_frame = ttk.Frame(root)
        summary_frame.grid(row=3, column=0, sticky="ew", pady=(10, 0))
        summary_frame.columnconfigure(0, weight=1)

        ttk.Label(summary_frame, textvariable=self.summary_var).grid(row=0, column=0, sticky="w")
        self.open_results_btn = ttk.Button(
            summary_frame, text="Open Results Folder", command=self._open_results_dir, state=tk.DISABLED
        )
        self.open_results_btn.grid(row=0, column=1, sticky="e")

        self._update_workflow_options()
        self._update_input_label()

    def _build_platform_section(self, parent):
        section = ttk.Frame(parent)
        section.grid(row=0, column=0, sticky="nsew", padx=(0, 12))
        section.columnconfigure(1, weight=1)

        ttk.Label(section, text="Target Platform").grid(row=0, column=0, sticky="w")
        platforms = ttk.Frame(section)
        platforms.grid(row=0, column=1, sticky="w")
        ttk.Radiobutton(
            platforms, text="iOS", value="ios", variable=self.platform_var, command=self._on_platform_change
        ).pack(side=tk.LEFT, padx=(0, 12))
        ttk.Radiobutton(
            platforms, text="Android", value="android", variable=self.platform_var, command=self._on_platform_change
        ).pack(side=tk.LEFT)

        ttk.Label(section, text="Workflow").grid(row=1, column=0, sticky="w", pady=(10, 0))
        self.workflow_combo = ttk.Combobox(
            section, textvariable=self.workflow_var, state="readonly", width=25
        )
        self.workflow_combo.grid(row=1, column=1, sticky="w", pady=(10, 0))
        self.workflow_combo.bind("<<ComboboxSelected>>", lambda _: self._update_input_label())

    def _build_paths_section(self, parent):
        section = ttk.Frame(parent)
        section.grid(row=0, column=1, sticky="nsew", padx=(0, 12))
        section.columnconfigure(1, weight=1)

        self.input_label = ttk.Label(section, text="Input")
        self.input_label.grid(row=0, column=0, sticky="w")
        ttk.Entry(section, textvariable=self.input_path_var).grid(row=0, column=1, sticky="ew", padx=8)
        ttk.Button(section, text="Browse", command=self._browse_input).grid(row=0, column=2, sticky="e")

        ttk.Label(section, text="IOC File (optional/required by check)").grid(row=1, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(section, textvariable=self.iocs_var).grid(row=1, column=1, sticky="ew", padx=8, pady=(10, 0))
        ttk.Button(section, text="Browse", command=self._browse_iocs).grid(row=1, column=2, sticky="e", pady=(10, 0))

        ttk.Label(section, text="Output Directory").grid(row=2, column=0, sticky="w", pady=(10, 0))
        ttk.Entry(section, textvariable=self.output_dir_var).grid(row=2, column=1, sticky="ew", padx=8, pady=(10, 0))
        ttk.Button(section, text="Browse", command=self._browse_output).grid(row=2, column=2, sticky="e", pady=(10, 0))

        ttk.Checkbutton(
            section,
            text="Remember these inputs for next launch",
            variable=self.remember_inputs_var,
            command=self._save_preferences,
        ).grid(row=3, column=1, sticky="w", pady=(10, 0))

    def _build_controls_section(self, parent):
        section = ttk.Frame(parent)
        section.grid(row=0, column=2, sticky="nsew")
        section.columnconfigure(0, weight=1)

        self.run_btn = ttk.Button(section, text="Run MVT", command=self._start_run)
        self.run_btn.grid(row=0, column=0, sticky="ew")
        ttk.Button(section, text="Clear Logs", command=self._clear_logs).grid(row=1, column=0, sticky="ew", pady=(8, 0))

    def _on_platform_change(self):
        self._update_workflow_options()
        self._update_input_label()

    def _update_workflow_options(self):
        if self.platform_var.get() == "ios":
            options = ["backup", "filesystem"]
        else:
            options = ["backup", "filesystem", "adb"]

        self.workflow_combo["values"] = options
        if self.workflow_var.get() not in options:
            self.workflow_var.set(options[0])

    def _update_input_label(self):
        workflow = self.workflow_var.get()
        label_map = {
            "backup": "Backup Path",
            "filesystem": "Filesystem Dump Path",
            "adb": "ADB Output / Work Folder",
        }
        self.input_label.config(text=label_map.get(workflow, "Input Path"))

    def _browse_input(self):
        selected = filedialog.askdirectory(title="Select Input Folder")
        if selected:
            self.input_path_var.set(selected)

    def _browse_iocs(self):
        selected = filedialog.askopenfilename(
            title="Select IOC File",
            filetypes=[("JSON files", "*.json"), ("STIX files", "*.stix2"), ("All files", "*.*")],
        )
        if selected:
            self.iocs_var.set(selected)

    def _browse_output(self):
        selected = filedialog.askdirectory(title="Select Output Directory")
        if selected:
            self.output_dir_var.set(selected)

    def _build_commands(self):
        platform = self.platform_var.get()
        workflow = self.workflow_var.get()
        input_path = self.input_path_var.get().strip()
        output_dir = self.output_dir_var.get().strip()
        iocs = self.iocs_var.get().strip()

        if not input_path:
            raise ValueError("Please select an input path.")
        if not output_dir:
            raise ValueError("Please select an output directory.")

        if not Path(input_path).exists():
            raise ValueError(f"Input path does not exist: {input_path}")

        output_target = Path(output_dir) / f"mvt_{platform}_{workflow}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        output_target.mkdir(parents=True, exist_ok=True)

        workflow_command_map = {
            "ios": {
                "backup": "check-backup",
                "filesystem": "check-fs",
            },
            "android": {
                "backup": "check-backup",
                "filesystem": "check-androidqf",
                "adb": "check-adb",
            },
        }

        command_name = workflow_command_map.get(platform, {}).get(workflow)
        if not command_name:
            raise ValueError(f"Unsupported platform/workflow combination: {platform}/{workflow}")

        if platform == "ios":
            cmd_args = [command_name, input_path, "--output", str(output_target)]
            display = ["mvt-ios"] + cmd_args
        else:
            if workflow == "adb":
                # check-adb acquires from connected device and does not take target path.
                cmd_args = [command_name, "--output", str(output_target)]
                display = ["mvt-android"] + cmd_args
            else:
                cmd_args = [command_name, input_path, "--output", str(output_target)]
                display = ["mvt-android"] + cmd_args

        if iocs:
            if not Path(iocs).exists():
                raise ValueError(f"IOC file does not exist: {iocs}")
            cmd_args.extend(["--iocs", iocs])
            display.extend(["--iocs", iocs])

        return [{"platform": platform, "args": cmd_args, "display": display}], str(output_target)

    def _start_run(self):
        if self.is_running:
            messagebox.showwarning("Run in progress", "A run is already in progress.")
            return

        try:
            commands, output_dir = self._build_commands()
        except Exception as exc:
            messagebox.showerror("Invalid configuration", str(exc))
            return

        self.last_output_dir = output_dir
        self._save_preferences()
        self.progress_var.set(0)
        self.command_list.delete(0, tk.END)
        for cmd in commands:
            self.command_list.insert(tk.END, "Queued: " + " ".join(cmd["display"]))

        self.open_results_btn.config(state=tk.DISABLED)
        self.is_running = True
        self.run_btn.config(state=tk.DISABLED)
        self.status_var.set("Running MVT in background...")
        self.current_cmd_var.set("Preparing execution")
        self.summary_var.set("Run started. Waiting for results...")
        self._append_log(f"Starting run with {len(commands)} command(s)\n")

        self.worker_thread = threading.Thread(
            target=self._run_commands_worker, args=(commands, output_dir), daemon=True
        )
        self.worker_thread.start()

    def _run_commands_worker(self, commands, output_dir):
        overall_success = True
        summaries = []

        for idx, cmd in enumerate(commands, start=1):
            self.log_queue.put(("status", f"Running command {idx}/{len(commands)}"))
            self.log_queue.put(("command", " ".join(cmd["display"])))
            self.log_queue.put(("line", f"\n$ {' '.join(cmd['display'])}\n"))

            return_code = self._run_mvt_library_command(cmd, idx, len(commands))
            self.log_queue.put(("progress", (idx / len(commands)) * 100))

            if return_code == 0:
                self.log_queue.put(("line", f"SUCCESS: command {idx} finished.\n"))
                summaries.append(f"Command {idx}: success")
            else:
                overall_success = False
                self.log_queue.put(("line", f"ERROR: command {idx} failed with exit code {return_code}.\n"))
                summaries.append(f"Command {idx}: failed ({return_code})")

        final_status = "success" if overall_success else "error"
        summary_text = (
            f"Platform: {self.platform_var.get()}, Workflow: {self.workflow_var.get()}, "
            f"Commands: {len(commands)}, Result: {'Success' if overall_success else 'Completed with errors'}, "
            f"Output: {output_dir}, Details: {', '.join(summaries)}"
        )
        self.log_queue.put(("done", final_status, summary_text, output_dir))

    def _run_mvt_library_command(self, command_obj, idx, total):
        class QueueWriter:
            def __init__(self, outer):
                self.outer = outer
                self._buffer = ""

            def write(self, data):
                if not data:
                    return 0
                self._buffer += data
                while "\n" in self._buffer:
                    line, self._buffer = self._buffer.split("\n", 1)
                    self.outer.log_queue.put(("line", line + "\n"))
                    pct_match = re.search(r"(\d{1,3})%", line)
                    if pct_match:
                        pct = min(100, max(0, int(pct_match.group(1))))
                        scaled = ((idx - 1) / total) * 100 + (pct / total)
                        self.outer.log_queue.put(("progress", scaled))
                return len(data)

            def flush(self):
                if self._buffer:
                    self.outer.log_queue.put(("line", self._buffer))
                    self._buffer = ""

        writer = QueueWriter(self)
        args = command_obj["args"]
        platform = command_obj["platform"]

        try:
            if platform == "ios":
                from mvt.ios.cli import cli as mvt_cli
                prog_name = "mvt-ios"
            else:
                from mvt.android.cli import cli as mvt_cli
                prog_name = "mvt-android"

            with redirect_stdout(writer), redirect_stderr(writer):
                mvt_cli.main(args=args, prog_name=prog_name, standalone_mode=False)
            writer.flush()
            return 0
        except SystemExit as exc:
            writer.flush()
            return int(exc.code) if isinstance(exc.code, int) else 1
        except Exception as exc:
            writer.flush()
            self.log_queue.put(("line", f"ERROR: MVT library call failed: {exc}\n"))
            return 1

    def _drain_log_queue(self):
        try:
            while True:
                item = self.log_queue.get_nowait()
                kind = item[0]
                if kind == "line":
                    self._append_log(item[1])
                elif kind == "status":
                    self.status_var.set(item[1])
                elif kind == "command":
                    self.current_cmd_var.set(item[1])
                elif kind == "progress":
                    self.progress_var.set(item[1])
                elif kind == "done":
                    self._handle_run_finished(item[1], item[2], item[3])
        except queue.Empty:
            pass
        finally:
            self.after(100, self._drain_log_queue)

    def _handle_run_finished(self, status, summary_text, output_dir):
        self.is_running = False
        self.run_btn.config(state=tk.NORMAL)
        self.current_cmd_var.set("No command running")
        self.summary_var.set(summary_text)

        if status == "success":
            self.status_var.set("Run completed successfully")
            self._append_log(f"\nAll commands completed. Results stored in: {output_dir}\n")
            self.open_results_btn.config(state=tk.NORMAL)
            messagebox.showinfo("MVT finished", f"Completed successfully.\nResults:\n{output_dir}")
        else:
            self.status_var.set("Run completed with errors")
            self._append_log(f"\nRun finished with errors. Check logs. Output folder: {output_dir}\n")
            if Path(output_dir).exists():
                self.open_results_btn.config(state=tk.NORMAL)
            else:
                self.open_results_btn.config(state=tk.DISABLED)
            messagebox.showwarning("MVT finished with errors", f"Some commands failed.\nOutput:\n{output_dir}")

    def _append_log(self, text):
        self.log_text.config(state=tk.NORMAL)
        self._insert_ansi_text(text)
        self.log_text.see(tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _insert_ansi_text(self, text):
        parts = ANSI_TOKEN_RE.split(text)
        for part in parts:
            if not part:
                continue
            if part.startswith("\x1b["):
                self._apply_ansi_token(part)
                continue
            self._insert_text_with_links(part)

    def _insert_text_with_links(self, text):
        style_tag = self._get_or_create_ansi_tag()
        cursor = 0
        for match in URL_RE.finditer(text):
            start, end = match.span()
            if start > cursor:
                self.log_text.insert(tk.END, text[cursor:start], (style_tag,))

            url = match.group(0)
            link_tag = self._get_or_create_link_tag(url)
            self.log_text.insert(tk.END, url, (style_tag, link_tag))
            cursor = end

        if cursor < len(text):
            self.log_text.insert(tk.END, text[cursor:], (style_tag,))

    def _get_or_create_link_tag(self, url):
        if url in self.link_tags:
            return self.link_tags[url]

        tag_name = f"link_{len(self.link_tags)}"
        self.log_text.tag_configure(tag_name, underline=1)
        self.log_text.tag_bind(tag_name, "<Enter>", lambda _e: self.log_text.config(cursor="hand2"))
        self.log_text.tag_bind(tag_name, "<Leave>", lambda _e: self.log_text.config(cursor="xterm"))
        self.log_text.tag_bind(tag_name, "<Button-1>", lambda _e, link=url: self._confirm_open_link(link))
        self.link_tags[url] = tag_name
        return tag_name

    def _confirm_open_link(self, link):
        should_open = messagebox.askokcancel(
            "Confirm Redirect",
            f"You are about to be redirected to {link}",
        )
        if should_open:
            webbrowser.open(link)

    def _apply_ansi_token(self, token):
        # Only style tokens ending in "m" affect text rendering.
        if not token.endswith("m"):
            return
        body = token[2:-1]
        codes = [0] if body == "" else []
        if body:
            for raw in body.split(";"):
                if raw.isdigit():
                    codes.append(int(raw))

        for code in codes:
            if code == 0:
                self.ansi_state = {"fg": None, "bold": False, "underline": False, "dim": False}
            elif code == 1:
                self.ansi_state["bold"] = True
            elif code == 2:
                self.ansi_state["dim"] = True
            elif code == 4:
                self.ansi_state["underline"] = True
            elif code == 22:
                self.ansi_state["bold"] = False
                self.ansi_state["dim"] = False
            elif code == 24:
                self.ansi_state["underline"] = False
            elif code == 39:
                self.ansi_state["fg"] = None
            elif code in ANSI_BASIC_COLORS:
                self.ansi_state["fg"] = ANSI_BASIC_COLORS[code]

    def _get_or_create_ansi_tag(self):
        key = (
            self.ansi_state["fg"],
            self.ansi_state["bold"],
            self.ansi_state["underline"],
            self.ansi_state["dim"],
        )
        if key in self.ansi_tags:
            return self.ansi_tags[key]

        tag_name = f"ansi_{len(self.ansi_tags)}"
        color = self.ansi_state["fg"]
        if self.ansi_state["dim"] and color is None:
            color = "#777777"

        font = tkfont.Font(self.log_text, self.log_base_font)
        if self.ansi_state["bold"]:
            font.configure(weight="bold")
        if self.ansi_state["underline"]:
            font.configure(underline=1)

        config = {"font": font}
        if color is not None:
            config["foreground"] = color

        self.log_text.tag_configure(tag_name, **config)
        self.ansi_tags[key] = tag_name
        return tag_name

    def _clear_logs(self):
        self.log_text.config(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.config(state=tk.DISABLED)

    def _open_results_dir(self):
        if not self.last_output_dir:
            messagebox.showinfo("No output", "No results directory is available yet.")
            return
        path = Path(self.last_output_dir)
        if not path.exists():
            messagebox.showerror("Not found", f"Results directory no longer exists:\n{path}")
            return
        os.startfile(str(path))

    def _load_preferences(self):
        if not self.settings_path.exists():
            return

        try:
            with self.settings_path.open("r", encoding="utf-8") as handle:
                data = json.load(handle)
        except Exception:
            return

        remember = bool(data.get("remember_inputs", False))
        self.remember_inputs_var.set(remember)
        if not remember:
            return

        self.platform_var.set(data.get("platform", self.platform_var.get()))
        self._update_workflow_options()
        self.workflow_var.set(data.get("workflow", self.workflow_var.get()))
        self._update_input_label()

        self.input_path_var.set(data.get("input_path", ""))
        self.output_dir_var.set(data.get("output_dir", ""))
        self.iocs_var.set(data.get("iocs_path", ""))

    def _save_preferences(self):
        payload = {"remember_inputs": bool(self.remember_inputs_var.get())}
        if self.remember_inputs_var.get():
            payload.update(
                {
                    "platform": self.platform_var.get(),
                    "workflow": self.workflow_var.get(),
                    "input_path": self.input_path_var.get().strip(),
                    "output_dir": self.output_dir_var.get().strip(),
                    "iocs_path": self.iocs_var.get().strip(),
                }
            )
        try:
            with self.settings_path.open("w", encoding="utf-8") as handle:
                json.dump(payload, handle, indent=2)
        except Exception:
            # Persistence should never block app usage.
            pass

    def _on_close(self):
        self._save_preferences()
        self.destroy()


if __name__ == "__main__":
    app = App()
    app.mainloop()
