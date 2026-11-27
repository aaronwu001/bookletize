"""
Small desktop window for bookletize (tkinter, ships with Python).

Run with:  bookletize-gui      or      python -m bookletize.gui
"""
from __future__ import annotations

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from pypdf import PdfReader

from . import __version__
from .core import MM, PAPER, BookletError, format_summary, make_booklets

HINT = "One booklet per line. Leave empty for the whole PDF.\nExample:  262-269, 273-287, 291-312"


class App(ttk.Frame):
    def __init__(self, root: tk.Tk):
        super().__init__(root, padding=12)
        self.root = root
        self.events: queue.Queue = queue.Queue()
        self.outputs: list[str] = []
        self.page_count = 0

        root.title(f"bookletize {__version__}")
        root.minsize(620, 560)
        self.grid(sticky="nsew")
        root.columnconfigure(0, weight=1)
        root.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        # ---- input file ----
        ttk.Label(self, text="PDF file").grid(row=0, column=0, sticky="w")
        self.input_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.input_var).grid(row=0, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Browse...", command=self.pick_input).grid(row=0, column=2)
        self.info_var = tk.StringVar(value="")
        ttk.Label(self, textvariable=self.info_var, foreground="gray").grid(
            row=1, column=1, sticky="w", padx=6)

        # ---- booklets ----
        ttk.Label(self, text="Booklets").grid(row=2, column=0, sticky="nw", pady=(10, 0))
        box = ttk.Frame(self)
        box.grid(row=2, column=1, columnspan=2, sticky="nsew", padx=6, pady=(10, 0))
        box.columnconfigure(0, weight=1)
        self.ranges = tk.Text(box, height=6, width=50, wrap="none", font=("Consolas", 10))
        self.ranges.grid(row=0, column=0, sticky="nsew")
        ttk.Label(box, text=HINT, foreground="gray", justify="left").grid(
            row=1, column=0, sticky="w", pady=(4, 0))

        # ---- options ----
        opts = ttk.LabelFrame(self, text="Options", padding=8)
        opts.grid(row=3, column=0, columnspan=3, sticky="ew", pady=10)

        self.merge_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Merge all booklets into one PDF",
                        variable=self.merge_var).grid(row=0, column=0, columnspan=4, sticky="w")

        ttk.Label(opts, text="Paper").grid(row=1, column=0, sticky="w", pady=(6, 0))
        self.paper_var = tk.StringVar(value="a4")
        ttk.Combobox(opts, textvariable=self.paper_var, values=list(PAPER), width=8,
                     state="readonly").grid(row=1, column=1, sticky="w", pady=(6, 0), padx=(4, 16))

        ttk.Label(opts, text="Margin (mm)").grid(row=1, column=2, sticky="w", pady=(6, 0))
        self.margin_var = tk.StringVar(value="0")
        ttk.Spinbox(opts, from_=0, to=30, textvariable=self.margin_var, width=6).grid(
            row=1, column=3, sticky="w", pady=(6, 0), padx=4)

        ttk.Label(opts, text="Blank pages at start").grid(row=2, column=0, sticky="w", pady=(6, 0))
        self.blank_var = tk.StringVar(value="0")
        ttk.Spinbox(opts, from_=0, to=8, textvariable=self.blank_var, width=6).grid(
            row=2, column=1, sticky="w", pady=(6, 0), padx=(4, 16))

        ttk.Label(opts, text="Signature (pages, 0 = off)").grid(row=2, column=2, sticky="w", pady=(6, 0))
        self.sig_var = tk.StringVar(value="0")
        ttk.Spinbox(opts, from_=0, to=200, increment=4, textvariable=self.sig_var, width=6).grid(
            row=2, column=3, sticky="w", pady=(6, 0), padx=4)

        # ---- output ----
        ttk.Label(self, text="Save as").grid(row=4, column=0, sticky="w")
        self.output_var = tk.StringVar()
        ttk.Entry(self, textvariable=self.output_var).grid(row=4, column=1, sticky="ew", padx=6)
        ttk.Button(self, text="Choose...", command=self.pick_output).grid(row=4, column=2)
        ttk.Label(self, text="Leave empty to save next to the input as <name>_booklet.pdf",
                  foreground="gray").grid(row=5, column=1, sticky="w", padx=6)

        # ---- actions ----
        actions = ttk.Frame(self)
        actions.grid(row=6, column=0, columnspan=3, sticky="ew", pady=(10, 4))
        actions.columnconfigure(1, weight=1)
        self.run_btn = ttk.Button(actions, text="Make booklet", command=self.run)
        self.run_btn.grid(row=0, column=0)
        self.progress = ttk.Progressbar(actions, mode="determinate")
        self.progress.grid(row=0, column=1, sticky="ew", padx=8)
        self.open_btn = ttk.Button(actions, text="Open folder", command=self.open_folder,
                                   state="disabled")
        self.open_btn.grid(row=0, column=2)

        # ---- log ----
        self.log = tk.Text(self, height=10, font=("Consolas", 9), state="disabled", wrap="none")
        self.log.grid(row=7, column=0, columnspan=3, sticky="nsew")
        self.rowconfigure(7, weight=1)
        self.write_log("Print the result double-sided, FLIP ON SHORT EDGE, actual size (100%).")

        self.after(100, self.poll)

    # ------------------------------------------------------------------ #
    def write_log(self, text: str, clear: bool = False) -> None:
        self.log.configure(state="normal")
        if clear:
            self.log.delete("1.0", "end")
        self.log.insert("end", text + "\n")
        self.log.see("end")
        self.log.configure(state="disabled")

    def pick_input(self) -> None:
        path = filedialog.askopenfilename(filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if path:
            self.input_var.set(path)
            try:
                self.page_count = len(PdfReader(path).pages)
                self.info_var.set(f"{self.page_count} pages")
            except Exception as e:  # noqa: BLE001
                self.page_count = 0
                self.info_var.set(f"Cannot read PDF: {e}")

    def pick_output(self) -> None:
        start = self.input_var.get()
        path = filedialog.asksaveasfilename(
            defaultextension=".pdf",
            filetypes=[("PDF files", "*.pdf")],
            initialdir=os.path.dirname(start) if start else None,
            initialfile=(os.path.splitext(os.path.basename(start))[0] + "_booklet.pdf") if start else "",
        )
        if path:
            self.output_var.set(path)

    def open_folder(self) -> None:
        if not self.outputs:
            return
        folder = os.path.dirname(os.path.abspath(self.outputs[0]))
        if sys.platform.startswith("win"):
            os.startfile(folder)  # type: ignore[attr-defined]
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])

    def collect(self) -> dict:
        path = self.input_var.get().strip()
        if not path:
            raise BookletError("Choose a PDF file first")
        if not os.path.isfile(path):
            raise BookletError(f"File not found: {path}")
        try:
            margin = float(self.margin_var.get() or 0)
            blank = int(self.blank_var.get() or 0)
            sig = int(self.sig_var.get() or 0)
        except ValueError:
            raise BookletError("Margin, blank pages and signature must be numbers") from None
        lines = [ln.strip() for ln in self.ranges.get("1.0", "end").splitlines() if ln.strip()]
        return dict(
            inputs=[path],
            booklets=lines if len(lines) > 1 else [],
            page_range=lines[0] if len(lines) == 1 else "",
            merge=self.merge_var.get(),
            signature=sig,
            blank_start=blank,
            paper=self.paper_var.get(),
            margin=margin * MM,
            output=self.output_var.get().strip() or None,
        )

    def run(self) -> None:
        try:
            kwargs = self.collect()
        except BookletError as e:
            messagebox.showerror("bookletize", str(e))
            return
        self.run_btn.configure(state="disabled")
        self.open_btn.configure(state="disabled")
        self.progress["value"] = 0
        self.write_log("Working...", clear=True)

        def work():
            try:
                res = make_booklets(
                    progress=lambda n, d, t: self.events.put(("progress", n, d, t)), **kwargs)
                self.events.put(("done", res))
            except Exception as e:  # noqa: BLE001
                self.events.put(("error", e))

        threading.Thread(target=work, daemon=True).start()

    def poll(self) -> None:
        try:
            while True:
                ev = self.events.get_nowait()
                if ev[0] == "progress":
                    _, n, done, total = ev
                    self.progress["maximum"] = total
                    self.progress["value"] = done
                    self.info_var.set(f"Book {n}: side {done}/{total}")
                elif ev[0] == "done":
                    res = ev[1]
                    self.outputs = res.outputs
                    self.progress["value"] = self.progress["maximum"]
                    self.write_log(format_summary(res), clear=True)
                    self.info_var.set(f"{self.page_count} pages" if self.page_count else "")
                    self.run_btn.configure(state="normal")
                    self.open_btn.configure(state="normal")
                elif ev[0] == "error":
                    self.write_log(f"Error: {ev[1]}", clear=True)
                    self.run_btn.configure(state="normal")
                    messagebox.showerror("bookletize", str(ev[1]))
        except queue.Empty:
            pass
        self.after(100, self.poll)


def main() -> None:
    root = tk.Tk()
    try:
        ttk.Style().theme_use("vista" if sys.platform.startswith("win") else "clam")
    except tk.TclError:
        pass
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
