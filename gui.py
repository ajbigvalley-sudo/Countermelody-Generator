#!/usr/bin/env python3
"""Drag-and-drop GUI for the countermelody generator.

Run with:  python3 gui.py    (or double-click launch.bat on Windows)

Drag a MIDI file onto the drop zone (or click to choose one), pick a
style, and click Generate. The output appears next to the input file
as <input>_with_counter.mid (or one file per style for "all variants").

Optional dependencies:
    tkinterdnd2  - enables drag-and-drop
    pygame       - enables in-app MIDI preview
"""

import os
import platform
import subprocess
import sys
import threading
from pathlib import Path

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except ImportError:
    raise SystemExit(
        "tkinter is required for the GUI.\n"
        "  Debian/Ubuntu:    sudo apt install python3-tk\n"
        "  macOS (Homebrew): brew install python-tk\n"
        "  Windows:          reinstall Python with the 'tcl/tk' option checked"
    )

try:
    from tkinterdnd2 import DND_FILES, TkinterDnD
    BaseTk = TkinterDnD.Tk
    HAS_DND = True
except ImportError:
    BaseTk = tk.Tk
    HAS_DND = False

try:
    import pygame
    pygame.mixer.init()
    HAS_AUDIO = True
except Exception:
    HAS_AUDIO = False

from countermelody import STYLES, CHORD_SOURCE_LABELS, generate_outputs


SUPPORTED_EXTS = {".mid", ".midi"}
ASSETS = Path(__file__).resolve().parent / "assets"
ICON_ICO = ASSETS / "icon.ico"
ICON_PNG = ASSETS / "icon.png"


def reveal_in_file_manager(path: Path) -> None:
    """Open the OS file manager pointed at the given file/folder."""
    p = Path(path)
    target = p if p.is_dir() else p.parent
    system = platform.system()
    if system == "Windows":
        if p.is_file():
            subprocess.run(["explorer", "/select,", str(p)])
        else:
            os.startfile(str(target))
    elif system == "Darwin":
        if p.is_file():
            subprocess.run(["open", "-R", str(p)])
        else:
            subprocess.run(["open", str(target)])
    else:
        subprocess.run(["xdg-open", str(target)])


class App:
    def __init__(self, root):
        self.root = root
        root.title("Countermelody Generator")
        root.geometry("640x620")
        root.minsize(540, 580)
        self._set_icon()

        self.input_path = tk.StringVar()
        self.style_var = tk.StringVar(value="harmonic")
        self.all_var = tk.BooleanVar(value=False)
        self.place_var = tk.StringVar(value="auto")

        self.last_outputs: list[Path] = []
        self.preview_path: Path | None = None
        self.is_playing = False

        self._build_drop_zone()
        self._build_style_picker()
        self._build_placement_picker()
        self._build_action_button()
        self._build_result_view()
        self._build_post_action_bar()

    def _set_icon(self):
        try:
            if platform.system() == "Windows" and ICON_ICO.exists():
                self.root.iconbitmap(default=str(ICON_ICO))
            elif ICON_PNG.exists():
                self._icon_image = tk.PhotoImage(file=str(ICON_PNG))
                self.root.iconphoto(True, self._icon_image)
        except Exception:
            pass

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
            wraplength=580, justify="center",
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
            command=self._refresh_preview_menu,
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
        self.result.pack(fill="both", expand=True, padx=20, pady=(5, 5))

    def _build_post_action_bar(self):
        bar = ttk.Frame(self.root)
        bar.pack(fill="x", padx=20, pady=(0, 15))

        self.preview_var = tk.StringVar()
        self.preview_menu = ttk.Combobox(
            bar, textvariable=self.preview_var, state="disabled", width=24,
        )
        self.preview_menu.pack(side="left", padx=(0, 6))

        self.play_btn = ttk.Button(
            bar, text="Play preview", state="disabled", command=self.on_play,
        )
        self.play_btn.pack(side="left", padx=2)

        self.stop_btn = ttk.Button(
            bar, text="Stop", state="disabled", command=self.on_stop,
        )
        self.stop_btn.pack(side="left", padx=2)

        self.open_btn = ttk.Button(
            bar, text="Open output folder", state="disabled",
            command=self.on_open_folder,
        )
        self.open_btn.pack(side="right")

        if not HAS_AUDIO:
            self._tooltip(
                self.play_btn,
                "Install pygame to enable preview:\n  pip install pygame",
            )

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
                     borderwidth=1, padx=6, pady=2, justify="left").pack()
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
        raw = event.data.strip()
        if raw.startswith("{") and raw.endswith("}"):
            raw = raw[1:-1]
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

    def _refresh_preview_menu(self):
        names = [p.name for p in self.last_outputs]
        self.preview_menu["values"] = names
        if names:
            self.preview_menu.config(state="readonly")
            self.preview_var.set(names[0])
            if HAS_AUDIO:
                self.play_btn.config(state="normal")
            self.open_btn.config(state="normal")
        else:
            self.preview_menu.config(state="disabled")
            self.play_btn.config(state="disabled")
            self.stop_btn.config(state="disabled")
            self.open_btn.config(state="disabled")

    def _selected_output(self) -> Path | None:
        name = self.preview_var.get()
        for p in self.last_outputs:
            if p.name == name:
                return p
        return self.last_outputs[0] if self.last_outputs else None

    def on_play(self):
        if not HAS_AUDIO:
            messagebox.showinfo(
                "Preview unavailable",
                "Install pygame to enable preview:\n  pip install pygame",
            )
            return
        target = self._selected_output()
        if not target or not target.exists():
            return
        try:
            pygame.mixer.music.stop()
            pygame.mixer.music.load(str(target))
            pygame.mixer.music.play()
            self.is_playing = True
            self.stop_btn.config(state="normal")
            self.play_btn.config(text="Restart")
        except Exception as exc:
            messagebox.showerror(
                "Playback error",
                f"Couldn't play this MIDI file:\n{exc}\n\n"
                "On some systems pygame needs a soundfont or a system MIDI synth "
                "to render MIDI. The file itself is fine - try opening it in a "
                "DAW or a player like MuseScore.",
            )

    def on_stop(self):
        if HAS_AUDIO:
            try:
                pygame.mixer.music.stop()
            except Exception:
                pass
        self.is_playing = False
        self.stop_btn.config(state="disabled")
        self.play_btn.config(text="Play preview")

    def on_open_folder(self):
        target = self._selected_output()
        if target:
            reveal_in_file_manager(target)

    def on_generate(self):
        if not self.input_path.get():
            messagebox.showwarning("No file", "Choose a melody file first.")
            return

        self.on_stop()

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

            def finish():
                self.set_result(text)
                self.last_outputs = [Path(p) for p in result["outputs"]]
                self._refresh_preview_menu()
                self.btn.config(state="normal", text="Generate Countermelody")

            self.root.after(0, finish)

        threading.Thread(target=worker, daemon=True).start()


def main():
    root = BaseTk()
    App(root)
    root.mainloop()


if __name__ == "__main__":
    main()
