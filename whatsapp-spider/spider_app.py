"""WhatsApp Appointment Spider - program window and daily schedule.

Run:   pythonw spider_app.py          (opens the window)
       pythonw spider_app.py --auto   (makes today's report without a window; used by the daily schedule)
"""

import datetime as dt
import json
import os
import subprocess
import sys
import threading
import traceback

import spider_core as core

FROZEN = getattr(sys, "frozen", False)  # running as the .exe
APP_DIR = os.path.dirname(sys.executable if FROZEN else os.path.abspath(__file__))
CONFIG_PATH = os.path.join(APP_DIR, "config.json")
LOG_PATH = os.path.join(APP_DIR, "spider.log")
TASK_NAME = "WhatsApp Appointment Spider"

DEFAULTS = {
    "export_folder": os.path.join(APP_DIR, "Exported chats"),
    "report_folder": os.path.join(APP_DIR, "Reports"),
    "my_name": "",
    "api_key": "",
    "model": "claude-sonnet-5-5",
    "date_order": "DMY",
    "schedule_time": "20:00",
    "auto_range_days": 1,
    "last_report": "",
}


def load_config():
    cfg = dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, encoding="utf-8") as f:
            cfg.update(json.load(f))
    except (OSError, ValueError):
        pass
    return cfg


def save_config(cfg):
    with open(CONFIG_PATH, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)


def file_log(msg):
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(f"{dt.datetime.now():%Y-%m-%d %H:%M:%S} {msg}\n")
    except OSError:
        pass


def make_report(cfg, start, end, log):
    os.makedirs(cfg["export_folder"], exist_ok=True)
    results = core.build_report(cfg["export_folder"], start, end, my_name=cfg["my_name"],
                                api_key=cfg["api_key"].strip(), model=cfg["model"].strip() or DEFAULTS["model"],
                                default_order=cfg["date_order"], log=log)
    html_path, csv_path = core.write_report(results, cfg["report_folder"], start, end)
    cfg["last_report"] = html_path
    save_config(cfg)
    log(f"Report ready: {html_path}")
    return html_path, len(results)


def run_auto():
    cfg = load_config()
    end = dt.date.today()
    start = end - dt.timedelta(days=max(1, int(cfg.get("auto_range_days", 1))) - 1)
    try:
        make_report(cfg, start, end, file_log)
    except Exception:
        file_log("Scheduled run failed:\n" + traceback.format_exc())


# ---------------------------------------------------------------- schedule (Windows Task Scheduler)

def _pythonw():
    exe = sys.executable
    cand = os.path.join(os.path.dirname(exe), "pythonw.exe")
    return cand if os.path.exists(cand) else exe


def _run(cmd):
    flags = 0x08000000 if os.name == "nt" else 0  # CREATE_NO_WINDOW
    p = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    return p.returncode, (p.stdout + p.stderr).strip()


def schedule_on(time_hhmm):
    if FROZEN:
        tr = f'"{sys.executable}" --auto'
    else:
        tr = f'"{_pythonw()}" "{os.path.join(APP_DIR, "spider_app.py")}" --auto'
    return _run(["schtasks", "/Create", "/F", "/SC", "DAILY", "/TN", TASK_NAME, "/ST", time_hhmm, "/TR", tr])


def schedule_off():
    return _run(["schtasks", "/Delete", "/F", "/TN", TASK_NAME])


def schedule_status():
    if os.name != "nt":
        return "Schedule only works on Windows."
    code, out = _run(["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if code != 0:
        return "Daily schedule is off."
    nxt = ""
    for line in out.splitlines():
        if ":" in line and ("Next Run" in line or "Volgende" in line):
            nxt = line.split(":", 1)[1].strip()
    return f"Daily schedule is on. Next run: {nxt}" if nxt else "Daily schedule is on."


# ---------------------------------------------------------------- window

def run_gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    cfg = load_config()
    root = tk.Tk()
    root.title("WhatsApp Appointment Spider")
    root.minsize(640, 560)
    pad = {"padx": 8, "pady": 4}

    v_export = tk.StringVar(value=cfg["export_folder"])
    v_report = tk.StringVar(value=cfg["report_folder"])
    v_range = tk.StringVar(value="today")
    v_from = tk.StringVar(value=dt.date.today().strftime("%d-%m-%Y"))
    v_to = tk.StringVar(value=dt.date.today().strftime("%d-%m-%Y"))
    v_name = tk.StringVar(value=cfg["my_name"])
    v_key = tk.StringVar(value=cfg["api_key"])
    v_time = tk.StringVar(value=cfg["schedule_time"])
    v_days = tk.StringVar(value=str(cfg.get("auto_range_days", 1)))
    v_status = tk.StringVar(value=schedule_status())

    def collect():
        cfg.update(export_folder=v_export.get().strip(), report_folder=v_report.get().strip(),
                   my_name=v_name.get().strip(), api_key=v_key.get().strip(),
                   schedule_time=v_time.get().strip())
        try:
            cfg["auto_range_days"] = max(1, int(v_days.get()))
        except ValueError:
            cfg["auto_range_days"] = 1
        save_config(cfg)

    def browse(var):
        d = filedialog.askdirectory(initialdir=var.get() or APP_DIR)
        if d:
            var.set(os.path.normpath(d))

    def open_path(p):
        if p and os.path.exists(p):
            os.startfile(p) if os.name == "nt" else None
        else:
            messagebox.showinfo("Not found", f"Not found yet:\n{p}")

    # Folders
    f1 = ttk.LabelFrame(root, text="Folders")
    f1.pack(fill="x", **pad)
    for r, (label, var) in enumerate([("Exported chats", v_export), ("Reports", v_report)]):
        ttk.Label(f1, text=label).grid(row=r, column=0, sticky="w", **pad)
        ttk.Entry(f1, textvariable=var).grid(row=r, column=1, sticky="ew", **pad)
        ttk.Button(f1, text="Browse", command=lambda v=var: browse(v)).grid(row=r, column=2, **pad)
        ttk.Button(f1, text="Open", command=lambda v=var: (os.makedirs(v.get(), exist_ok=True), open_path(v.get())))\
            .grid(row=r, column=3, **pad)
    f1.columnconfigure(1, weight=1)

    # Range
    f2 = ttk.LabelFrame(root, text="Which days")
    f2.pack(fill="x", **pad)
    ttk.Radiobutton(f2, text="Today", variable=v_range, value="today").grid(row=0, column=0, sticky="w", **pad)
    ttk.Radiobutton(f2, text="Yesterday", variable=v_range, value="yesterday").grid(row=0, column=1, sticky="w", **pad)
    ttk.Radiobutton(f2, text="From", variable=v_range, value="custom").grid(row=0, column=2, sticky="w", **pad)
    ttk.Entry(f2, textvariable=v_from, width=12).grid(row=0, column=3, **pad)
    ttk.Label(f2, text="to").grid(row=0, column=4)
    ttk.Entry(f2, textvariable=v_to, width=12).grid(row=0, column=5, **pad)
    ttk.Label(f2, text="(dd-mm-yyyy)").grid(row=0, column=6, sticky="w")

    # Options
    f3 = ttk.LabelFrame(root, text="Options")
    f3.pack(fill="x", **pad)
    ttk.Label(f3, text="Your name in WhatsApp").grid(row=0, column=0, sticky="w", **pad)
    ttk.Entry(f3, textvariable=v_name).grid(row=0, column=1, sticky="ew", **pad)
    ttk.Label(f3, text="optional, found automatically").grid(row=0, column=2, sticky="w")
    ttk.Label(f3, text="Claude API key").grid(row=1, column=0, sticky="w", **pad)
    ttk.Entry(f3, textvariable=v_key, show="•").grid(row=1, column=1, sticky="ew", **pad)
    ttk.Label(f3, text="optional, for smart summaries").grid(row=1, column=2, sticky="w")
    f3.columnconfigure(1, weight=1)

    # Actions
    f4 = ttk.Frame(root)
    f4.pack(fill="x", **pad)
    btn = ttk.Button(f4, text="Make report now")
    btn.pack(side="left", **pad)
    ttk.Button(f4, text="Open last report", command=lambda: open_path(cfg.get("last_report"))).pack(side="left", **pad)

    # Schedule
    f5 = ttk.LabelFrame(root, text="Daily schedule")
    f5.pack(fill="x", **pad)
    ttk.Label(f5, text="Every day at").grid(row=0, column=0, **pad)
    ttk.Entry(f5, textvariable=v_time, width=7).grid(row=0, column=1, **pad)
    ttk.Label(f5, text="covering the last").grid(row=0, column=2)
    ttk.Entry(f5, textvariable=v_days, width=4).grid(row=0, column=3, **pad)
    ttk.Label(f5, text="day(s)").grid(row=0, column=4)

    def turn_on():
        collect()
        t = cfg["schedule_time"]
        try:
            dt.datetime.strptime(t, "%H:%M")
        except ValueError:
            messagebox.showerror("Time", "Use a time like 20:00")
            return
        code, out = schedule_on(t)
        v_status.set(schedule_status() if code == 0 else f"Could not turn on: {out}")

    def turn_off():
        schedule_off()
        v_status.set(schedule_status())

    ttk.Button(f5, text="Turn on", command=turn_on).grid(row=0, column=5, **pad)
    ttk.Button(f5, text="Turn off", command=turn_off).grid(row=0, column=6, **pad)
    ttk.Label(f5, textvariable=v_status).grid(row=1, column=0, columnspan=7, sticky="w", **pad)

    # Log
    logbox = tk.Text(root, height=10, wrap="word")
    logbox.pack(fill="both", expand=True, **pad)

    def log(msg):
        root.after(0, lambda: (logbox.insert("end", msg + "\n"), logbox.see("end")))

    def parse_d(s):
        return dt.datetime.strptime(s.strip(), "%d-%m-%Y").date()

    def go():
        collect()
        today = dt.date.today()
        if v_range.get() == "today":
            start = end = today
        elif v_range.get() == "yesterday":
            start = end = today - dt.timedelta(days=1)
        else:
            try:
                start, end = parse_d(v_from.get()), parse_d(v_to.get())
            except ValueError:
                messagebox.showerror("Dates", "Write dates like 06-11-2026")
                return
            if end < start:
                start, end = end, start
        btn.state(["disabled"])
        logbox.delete("1.0", "end")

        def work():
            try:
                path, n = make_report(cfg, start, end, log)
                root.after(0, lambda: open_path(path))
            except Exception as e:
                log(f"Something went wrong: {e}")
                file_log(traceback.format_exc())
            finally:
                root.after(0, lambda: btn.state(["!disabled"]))
        threading.Thread(target=work, daemon=True).start()

    btn.configure(command=go)
    log("Put your exported WhatsApp chats (.txt or .zip) in the 'Exported chats' folder, then click 'Make report now'.")
    root.protocol("WM_DELETE_WINDOW", lambda: (collect(), root.destroy()))
    root.mainloop()


if __name__ == "__main__":
    if "--auto" in sys.argv:
        run_auto()
    else:
        run_gui()
