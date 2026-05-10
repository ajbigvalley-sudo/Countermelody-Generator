#!/usr/bin/env python3
"""Drag-and-drop GUI for the countermelody generator.

Run with:  python3 gui.py

Drag a MIDI file onto the drop zone (or click to choose one), pick a
style, and click Generate. The output appears next to the input file
as <input>_with_counter.mid (or one file per style with --all variants).

Drag-and-drop requires the optional 'tkinterdnd2' package. Without it,
the file picker still works.
"""

import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    raise SystemExit(
        "tkinter is required for the GUI.\n"
        "  Debian/Ubuntu:  sudo apt install python3-tk\n"
        "  macOS (Homebrew):  brew install python-tk"
    )

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except ImportError:
    BaseTk = tk.Tk
    HAS_DND = False

from countermelody import STYLES, CHORD_SOURCE_LABELS, generate_outputs


SUPPORTED_EXTS = {".mid", ".midi"}


class App:
    def __init__(self, root):
        self.root = root
        root.title("Countermelody Generator")
        root.geometry("600x500")
        root.minsize(500, 460)

        self.input_path = tk.StringVar()
        self.style_var = tk.StringVar(value="harmonic")
        self.all_var = tk.BooleanVar(value=False)
        self.place_var = tk.StringVar(value="auto")

        self._build_drop_zone()
        self._build_style_picker()
        self._build_placement_picker()
        self._build_action_button()
        self._build_result_view()

    def _build_drop_zone(self):
        intro = (
            "Drop a MIDI file here, or click to choose."
            if HAS_DND else
            "Click to choose a MIDI file.\n"
            "(Install tkinterdnd2 to enable drag-and-drop.)"
        )
        self.drop = tk.Label(
            self.root, text=intro,
            relief="ridge", height=4, bg="#eef2f7", cursor="hand2",
            wraplength=540, justify="center",
        )
        self.drop.pack(fill="x", padx=20, pady=(15, 10))
        self.drop.bind("<Button-1>", lambda e: self.choose_file())
        if HAS_DND:
            self.drop.drop_target_register(DND_FILES)
            self.drop.dnd_bind("<<Drop>>", self.on_drop)

    def _build_style_picker(self):
        sf = ttk.LabelFrame(self.root, text="Style")
        sf.pack(fill="x", padx=20, pady=5)
        for name, style in STYLES.items():
            rb = ttk.Radiobutton(sf, text=name, value=name, variable=self.style_var)
            rb.pack(side="left", padx=8, pady=5)
            self._tooltip(rb, style.description)

        ttk.Checkbutton(
            self.root,
            text="Generate all 4 variants (one MIDI per style)",
            variable=self.all_var,
        ).pack(anchor="w", padx=22, pady=2)

    def _build_placement_picker(self):
        pf = ttk.LabelFrame(self.root, text="Counter line placement")
        pf.pack(fill="x", padx=20, pady=5)
        for label, val in [("Auto", "auto"),
                           ("Above melody", "above"),
                           ("Below melody", "below")]:
            ttk.Radiobutton(pf, text=label, value=val,
                            variable=self.place_var).pack(side="left", padx=8, pady=5)

    def _build_action_button(self):
        self.btn = ttk.Button(
            self.root, text="Generate Countermelody", command=self.on_generate,
        )
        self.btn.pack(pady=10)

    def _build_result_view(self):
        self.result = tk.Text(
            self.root, height=8, wrap="word", state="disabled",
            bg="#f7f7f7", relief="flat", padx=8, pady=6,
        )
        self.result.pack(fill="both", expand=True, padx=20, pady=(5, 15))

    def _tooltip(self, widget, text):
        tip = {"win": None}

        def show(_):
            if tip["win"] or not text:
                return
            x = widget.winfo_rootx() + 20
            y = widget.winfo_rooty() + widget.winfo_height() + 4
            tw = tk.Toplevel(widget)
            tw.wm_overrideredirect(True)
            tw.wm_geometry(f"+{x}+{y}")
            tk.Label(tw, text=text, background="#ffffe0", relief="solid",
                     borderwidth=1, padx=6, pady=2).pack()
            tip["win"] = tw

        def hide(_):
            if tip["win"]:
                tip["win"].destroy()
                tip["win"] = None

        widget.bind("<Enter>", show)
        widget.bind("<Leave>", hide)

    def choose_file(self):
        path = filedialog.askopenfilename(
            filetypes=[("MIDI files", "*.mid *.midi"), ("All files", "*.*")],
        )
        if path:
            self.set_input(path)

    def on_drop(self, event):
        # tkdnd wraps paths containing spaces with curly braces
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
        # Multi-file drops are space-separated; just take the first.
        first = raw.split("} {", 1)[0].strip("{}")
        self.set_input(first)

    def set_input(self, path):
        p = Path(path)
        if not p.exists():
            messagebox.showwarning("Not found", f"File not found:\n{path}")
            return
        if p.suffix.lower() not in SUPPORTED_EXTS:
            messagebox.showwarning(
                "Unsupported file",
                f"{p.suffix or '(no extension)'} files aren't supported yet.\n"
                "Use a .mid or .midi file.",
            )
            return
        self.input_path.set(str(p))
        self.drop.config(text=f"Selected:\n{p.name}\n{p.parent}")

    def set_result(self, text):
        self.result.config(state="normal")
        self.result.delete("1.0", "end")
        self.result.insert("1.0", text)
        self.result.config(state="disabled")

    def on_generate(self):
        if not self.input_path.get():
            messagebox.showwarning("No file", "Choose a melody file first.")
            return

        in_p = Path(self.input_path.get())
        styles = list(STYLES) if self.all_var.get() else [self.style_var.get()]

        place_below = None
        if self.place_var.get() == "above":
            place_below = False
        elif self.place_var.get() == "below":
            place_below = True

        out_path = in_p.parent / f"{in_p.stem}_with_counter.mid"

        self.btn.config(state="disabled", text="Generating...")
        self.set_result("Working...")

        def worker():
            try:
                result = generate_outputs(
                    str(in_p), str(out_path), styles, place_below=place_below,
                )
            except Exception as exc:
                msg = str(exc)
                self.root.after(0, lambda: messagebox.showerror("Error", msg))
                self.root.after(0, lambda: self.set_result(f"Error: {msg}"))
                self.root.after(0, lambda: self.btn.config(
                    state="normal", text="Generate Countermelody"))
                return

            src_label = CHORD_SOURCE_LABELS.get(
                result["chord_source"], result["chord_source"],
            )
            lines = [
                f"Detected key:   {result['detected_key']}",
                f"Chord context:  {src_label}",
                "",
                "Output files:",
            ]
            for p in result["outputs"]:
                lines.append(f"  {p}")
            text = "\n".join(lines)
            self.root.after(0, lambda: self.set_result(text))
            self.root.after(0, lambda: self.btn.config(
                state="normal", text="Generate Countermelody"))

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = BaseTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
