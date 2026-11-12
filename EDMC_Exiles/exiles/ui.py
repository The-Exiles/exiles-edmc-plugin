"""The plugin's face in EDMC: the strip in the main window and the Exiles window. Only this file touches
tkinter, and only on the main thread: the strip drains the core's queue with after(). Every word comes from words.py.

The strip keeps EDMC's own colours and face, so EDMC's three themes repaint it. It leads with the squad's emblem, as
BGS Tally's block above it leads with its logo, and its lines start where Tally's lines start. The Exiles window wears
the ops site's look whatever EDMC's theme is: the hull, the panels, the orange, Euro Caps for names and numbers, chips
for tabs and buttons."""
from __future__ import annotations

import base64
import json
import queue
import re
import time
import tkinter as tk
import webbrowser
from pathlib import Path
from tkinter import font as tkfont
from typing import Optional

try:
    from theme import theme   # type: ignore  (EDMC's themes)
except Exception:   # pragma: no cover
    theme = None

from .core import ExilesPlugin, show_code
from .status import decode_flags
from .words import (AGAINST_US, BAD, DETAILS_NOTE, GOOD, IDLE, LAST_EMPTY, PCS_NOTE, PCS_NOTE_ONE, REMOVE, REMOVE_SURE, REMOVING, SENDS,
                    TICK_EMPTY, WARN, XP_NOTE, ago, alert_words, details_words, link_state, needs_pairing, orders_blocks, orders_words,
                    pairing_words, pc_when, pcs_rows, session_words, squad_words, tick_day, tick_factions, tick_text, tick_tiles, tick_xp,
                    unpair_words, version_words, war_card, war_words)

DRAIN_EVERY_MS = 500
LIVE_EVERY_MS = 20_000          # how often the window says its times again ("12 min ago")
COPIED_FOR = 3.0                # seconds the foot says what was copied
SURE_FOR = 6.0                  # seconds the second press of Remove has, after the first
REMEMBER_AFTER_MS = 1500        # the window's place is kept this long after it was last moved or sized
COPY_HINT = "Click a system's name to copy it for the galaxy map."
TICK_WIDE = 500                 # a page this wide gives a faction's standing a column of its own on the My tick tab
FACTION_WRAP = 170              # where a system's name wraps in a faction's card on a wide page
STANDING_WRAP = 120             # and its standing there: a long state goes onto a second line, so the work keeps its room
TICK_INDENT = 12                # how far a faction's lines stand in under its name, in the card of the other factions
FACTION_WRAP_NARROW = 150       # where a system's name wraps on a narrow page, with the standing under it


def to_clipboard(widget, words: str) -> bool:
    """Put these words on the clipboard. The one place the plugin touches it, so the tests and the drawing tools,
    which run on a PC someone is using, can stand a list in for it."""
    try:
        widget.clipboard_clear()
        widget.clipboard_append(words)
        widget.update_idletasks()
        return True
    except tk.TclError:
        return False

# ----- the strip: EDMC's own look ---------------------------------------------------------------------------------------
DOT = 10
STRIP_DOT = {GOOD: "#3fbf6f", WARN: "#e0a000", BAD: "#ff3b2e", IDLE: "#8c8c8c"}
EDMC_LABELS = ("cmdr_label", "ship_label", "suit_label", "system_label", "station_label")   # EDMC 5 and 6 name them so
EDMC_PADX = 5                   # what EDMC puts either side of every row in its main window
FIRST_WRAP = 190                # where the lines wrap at first, should the words to click not say how wide they are
LINK_GAP = 6                    # between the words to click
# The squad's emblem (assets/exiles_67.png, and exiles_97.png for a screen scaled up, both cut from the squad's logo).
# BGS Tally's logo is 100 pixels wide in a Label whose border adds 4, and nothing else in Tally's block is wider in
# that column (5.6.1, in English), so a slot of 104 puts the strip's lines exactly under Tally's lines (measured in
# a real EDMC window: both start at the same pixel); the larger emblem is 100 wide for the same reason.
ASSETS = Path(__file__).resolve().parent.parent / "assets"
MARK_SLOT = 104
MARK_GAP = 12                   # the least room an emblem wider than Tally's slot keeps around itself
LABEL_EDGE = 6                  # what a Label asks for beyond its words: two pixels of border and one of padding, each side
DOT_ROOM = 17                   # the dot and the space either side of it, before the words beside it

# ----- the window: the ops site's own colours (public/style.css :root) --------------------------------------------------
HULL, PANEL, EDGE = "#0b0a08", "#171310", "#3a2c17"
HUD, HUD_DIM, FRIENDLY = "#ff7100", "#b35400", "#00b3f7"
INK, INK_DIM = "#ffe3bd", "#a98f6d"
SIGNAL, HOSTILE, FINE = "#ffd23d", "#ff3b2e", "#3fbf6f"
WARN_TINT = "#2b2410"
BAD_TINT = "#2b1310"
LEVELS = {GOOD: FINE, WARN: SIGNAL, BAD: HOSTILE, IDLE: INK_DIM}

# weight "bold", never the family "Segoe UI Semibold": Tk draws that one slanted
F_BODY = ("Segoe UI", 10)
F_BOLD = ("Segoe UI", 10, "bold")
F_TITLE = ("Segoe UI", 12, "bold")
F_HEAD = ("Segoe UI", 11, "bold")
F_SMALL = ("Segoe UI", 9)
F_CHIP = ("Consolas", 9)
F_LABEL = ("Consolas", 8)
F_NAME = ("Euro Caps", 13)
F_MARK = ("Euro Caps", 15)
F_NUM = ("Euro Caps", 20)
F_CODE = ("Euro Caps", 26)

PAIR_FIRST = "Pair the plugin first: Settings, then Pair."


def load_mark(widget) -> Optional[tk.PhotoImage]:
    """The squad's emblem at the size for this screen, or None where it cannot be read: the strip then goes without."""
    try:
        scale = float(widget.tk.call("tk", "scaling")) * 72 / 96       # 1.0 on an ordinary screen
        # Read here and handed to Tk as data, never as a file name: the first file Tcl opens for itself in a process
        # whose standard handles are not a console's can fail with an empty error (seen under pytest's capture, and a
        # windowed EDMC has no console either), and the emblem would be missing for the whole session.
        raw = (ASSETS / ("exiles_97.png" if scale >= 1.4 else "exiles_67.png")).read_bytes()
        return tk.PhotoImage(master=widget, data=base64.b64encode(raw))
    except Exception:
        return None


class Strip:
    """EDMC's main window. With the squad's emblem (0.2.1) the strip is laid out as BGS Tally's block above it is: the
    emblem in a slot as wide as Tally's logo, and beside it the lines, starting where Tally's lines start: a dot and
    how the link stands, the war, what needs saying about the ship, and four words to click. Where the emblem's file
    cannot be read the lines are rows in EDMC's own manner, a name and what it says, lined up with EDMC's own rows
    above."""

    LINKS = (("orders", "Orders"), ("mytick", "My tick"), ("squad", "Squad"), ("settings", "Settings"))

    def __init__(self, parent, core: ExilesPlugin):
        self.core = core
        self.window: Optional[ExilesWindow] = None
        self._shown: Optional[tuple] = None       # what the strip last drew: a tick that changes nothing redraws nothing
        self._label_col = -1                      # what stands left of the lines: the emblem's slot, or the names
        self._slot_seen = 0                       # where the lines of the plugin block beside this one were last seen to start
        self._ticks = 0
        self.frame = tk.Frame(parent)
        self.frame.columnconfigure(1, weight=1)
        self.mark = load_mark(self.frame)
        self.mark_label: Optional[tk.Label] = None
        if self.mark is not None:
            self.mark_label = tk.Label(self.frame, image=self.mark, bd=0, padx=0, pady=0)
            self.mark_label.grid(row=0, column=0)
        lines = self.lines = tk.Frame(self.frame)
        lines.grid(row=0, column=1, sticky="we")
        lines.columnconfigure(1, weight=1)
        self.name = tk.Label(lines, text="Exiles:", anchor="w")
        self.name.grid(row=0, column=0, sticky="w")
        state = tk.Frame(lines)
        state.grid(row=0, column=1, sticky="w")
        self.dot = tk.Canvas(state, width=DOT, height=DOT, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(3, 4))
        self.dot_id = self.dot.create_oval(1, 1, DOT - 1, DOT - 1, fill=STRIP_DOT[IDLE], outline="")
        self.status = tk.Label(state, text="", anchor="w", justify="left")
        self.status.pack(side="left")
        self.war_name = tk.Label(lines, text="War:", anchor="w")
        self.war_name.grid(row=1, column=0, sticky="nw")
        self.war = tk.Label(lines, text="", anchor="w", justify="left")
        self.war.grid(row=1, column=1, sticky="w")
        self.nudge = tk.Label(lines, text="", anchor="w", justify="left")
        self.nudge.grid(row=2, column=1, sticky="w")
        # the orders have a line only while they are new to the pilot (0.3.0); opening the Orders tab takes it away
        self.orders_name = tk.Label(lines, text="Orders:", anchor="w")
        self.orders_name.grid(row=3, column=0, sticky="nw")
        self.orders_line = tk.Label(lines, text="", anchor="w", justify="left")
        self.orders_line.grid(row=3, column=1, sticky="w")
        self.alert_name = tk.Label(lines, text="Ship:", anchor="w")
        self.alert_name.grid(row=4, column=0, sticky="nw")
        self.alert = tk.Label(lines, text="", anchor="w", justify="left")
        self.alert.grid(row=4, column=1, sticky="w")
        for widget in (self.war_name, self.war, self.nudge, self.orders_name, self.orders_line, self.alert_name, self.alert):
            widget.grid_remove()
        if self.mark is not None:
            self.name.grid_remove()             # the emblem says whose lines these are
        try:        # what a Label asks for beyond its words, so a line wraps exactly where its room ends
            self._edge = 2 * sum(int(self.war.winfo_fpixels(self.war.cget(k))) for k in ("borderwidth", "padx", "highlightthickness"))
        except Exception:
            self._edge = LABEL_EDGE
        # The words to click. EDMC paints anything that wears a hand cursor in its link colour, a blue that is hard to
        # read on a dark Windows style, so a word is a hand only while the pointer is over it, and it takes the colour
        # and the face EDMC's theme gives the other lines, underlined (_dress).
        self.link_row = tk.Frame(lines)
        self.link_row.grid(row=5, column=1, sticky="w", pady=(1, 2))
        self.link_font = tkfont.Font(self.frame, font=self.status.cget("font"))
        self.link_font.configure(underline=True)
        self._dressed: Optional[tuple] = None
        self.links = {}
        for key, label in self.LINKS:
            self.links[key] = self._link(label, lambda k=key: self.open(k))
        self.pair_link = self._link("Pair", self.pair)
        # Until EDMC's window says how wide it is, the lines wrap where the four words end. EDMC keeps the width it
        # first comes up with as its floor, so the strip never makes that wider than its emblem and its words need.
        try:
            self._wrap = max(120, sum(lab.winfo_reqwidth() + LINK_GAP for lab in self.links.values()) - self._edge)
        except Exception:
            self._wrap = FIRST_WRAP
        for lab in (self.war, self.nudge, self.orders_line, self.alert):
            lab.configure(wraplength=self._wrap)
        self.status.configure(wraplength=max(100, self._wrap - DOT_ROOM))
        self.frame.bind("<Configure>", self._fit)
        if theme is not None:
            try:
                theme.update(self.frame)
            except Exception:
                pass
        self._dress()
        self._align()
        self._watch_edmc()
        self.refresh()
        self.frame.after(DRAIN_EVERY_MS, self._drain)

    def _link(self, label: str, command) -> tk.Label:
        lab = tk.Label(self.link_row, text=label, font=self.link_font)
        lab.bind("<Button-1>", lambda e: command())
        lab.bind("<Enter>", lambda e: lab.configure(cursor="hand2"))
        lab.bind("<Leave>", lambda e: lab.configure(cursor=""))
        return lab

    # ----- what the pilot does ------------------------------------------------------------------------------------------
    def pair(self) -> None:
        self.core.begin_pairing()
        self.open("settings")
        self.refresh()

    def open(self, tab: str) -> None:
        if self.window is None or not self.window.winfo_exists():
            self.window = ExilesWindow(self.frame.winfo_toplevel(), self.core, on_close=self._closed)
            self.core.window_open = True
            self.core.refresh_feed_now()
        self.window.show(tab)

    def _closed(self) -> None:
        self.window = None
        self.core.window_open = False

    def settings_changed(self) -> None:
        """EDMC's own Settings dialog was closed: its theme may have changed, and the site's address."""
        self._dress()
        self._align()
        self.refresh()
        if self.window is not None and self.window.winfo_exists():
            self.window.render(force=True)

    # ----- drawing --------------------------------------------------------------------------------------------------------
    def refresh(self) -> None:
        core = self.core
        level, words, _ = link_state(core)
        line, nudge = war_words(core)
        alert = alert_words(core.last_status)
        on_foot = bool(alert and decode_flags(core.last_status.get("Flags"), core.last_status.get("Flags2")).get("on_foot"))
        needs_pair = needs_pairing(core)
        newer = core.update.get("latest") if core.update and core.update.get("newer") else None
        orders = orders_words(core)
        want = (level, words, line, nudge, orders, alert, on_foot, core.paired, needs_pair, newer)
        if want == self._shown:
            return
        self._shown = want
        self.dot.itemconfigure(self.dot_id, fill=STRIP_DOT[level])
        self.status.configure(text=words)
        whose = "Suit" if on_foot else "Ship"
        if self.mark is not None:
            # beside the emblem a line carries its own name, as the lines of BGS Tally's block do
            self._row(f"War: {line}" if line else None, None, self.war)
            self._row(nudge if line else None, None, self.nudge)
            self._row(f"Orders: {orders}" if orders else None, None, self.orders_line)
            self._row(f"{whose}: {alert[:1].lower()}{alert[1:]}" if alert else None, None, self.alert)
        else:
            self._row(line, self.war_name, self.war)
            self._row(nudge if line else None, None, self.nudge)
            self._row(orders, self.orders_name, self.orders_line)
            self.alert_name.configure(text=f"{whose}:")
            self._row(alert, self.alert_name, self.alert)
        # Pair leads whenever a pairing is needed (never paired, or the key is no longer accepted); the three that need
        # a pairing wait until there is one
        for lab in (self.pair_link, *self.links.values()):
            lab.pack_forget()
        if needs_pair:
            self.pair_link.pack(side="left", padx=(0, LINK_GAP))
        for key, lab in self.links.items():
            if core.paired or key == "settings":
                lab.pack(side="left", padx=(0, LINK_GAP))
        self.links["settings"].configure(text=f"Update {newer}" if newer else "Settings")

    @staticmethod
    def _row(words: Optional[str], name: Optional[tk.Label], value: tk.Label) -> None:
        if words:
            value.configure(text=words)
            value.grid()
            if name is not None:
                name.grid()
        else:
            value.grid_remove()
            if name is not None:
                name.grid_remove()

    def _dress(self) -> None:
        """The words to click take the colour and the face the theme gave the other lines, underlined."""
        try:
            now = (str(self.status.cget("fg")), str(self.status.cget("font")))
            if now != self._dressed:
                self._dressed = now
                actual = tkfont.Font(self.frame, font=self.status.cget("font")).actual()
                self.link_font.configure(family=actual["family"], size=actual["size"], weight=actual["weight"], slant=actual["slant"],
                                         underline=True)
            for lab in (self.pair_link, *self.links.values()):
                if str(lab.cget("fg")) != now[0]:
                    lab.configure(fg=now[0])
        except Exception:
            pass

    def _align(self) -> None:
        """Where the lines start. With the emblem: where BGS Tally's lines start, its logo's slot along (or the
        emblem's own width where that is more). Without it: in line with EDMC's own rows above, whose label column is
        as wide as its widest label plus the padding EDMC gives every row, this frame starting where those labels
        start; outside EDMC, or in an EDMC that names its labels otherwise, as wide as the strip's own names need."""
        try:
            if self.mark is not None:
                seen = self._neighbour_lines()
                if seen and seen + self.link_row.winfo_reqwidth() > self.frame.winfo_width():
                    seen = 0        # never ask EDMC for more room on account of a neighbour's column
                self._slot_seen = seen or self._slot_seen                           # kept while EDMC sits in the tray
                col = max(self._slot_seen or MARK_SLOT, self.mark.width() + MARK_GAP)
                if col != self._label_col:
                    self._label_col = col
                    self.frame.columnconfigure(0, minsize=col)
                    self._fit()
                return
        except Exception:
            return
        width = 0
        try:
            main = self.frame.master.master           # EDMC's frame for this plugin, then its main frame
            for name in EDMC_LABELS:
                lab = main.children.get(name)
                if lab is not None and lab.winfo_manager() == "grid":
                    width = max(width, lab.winfo_reqwidth())
        except Exception:
            width = 0
        try:
            mine = max(lab.winfo_reqwidth() for lab in (self.name, self.war_name, self.orders_name, self.alert_name))
            col = max(width, mine) + 2 * EDMC_PADX
            if col != self._label_col:
                self._label_col = col
                self.lines.columnconfigure(0, minsize=col)
                self._fit()
        except Exception:
            pass

    def _neighbour_lines(self) -> int:
        """Where the lines of the nearest other plugin block start, measured from this frame's left edge, when that
        block leads with a picture over several rows as BGS Tally's does; 0 when there is none, or nothing is on the
        screen yet. It is read from the window, never assumed: Tally's first button shares its logo's column and
        makes it wider than the logo on a screen scaled up, in another language and in EDMC's transparent theme."""
        try:
            if not self.frame.winfo_ismapped():
                return 0
            mine = self.frame.master                                  # EDMC's frame for this plugin
            x0, y0 = self.frame.winfo_rootx(), self.frame.winfo_rooty()
            nearest, found = None, 0
            todo = [(w, 0) for w in mine.master.winfo_children() if w is not mine and str(w.winfo_name()).startswith("plugin_")]
            while todo:
                w, depth = todo.pop()
                kind = w.winfo_class()
                if kind in ("Label", "TLabel"):
                    if w.winfo_manager() != "grid" or not w.winfo_ismapped() or not w.cget("image"):
                        continue
                    info = w.grid_info()
                    if int(info.get("column", 0)) != 0 or int(info.get("rowspan", 1)) < 2:
                        continue
                    holder = w.master
                    x = holder.winfo_rootx() + int(holder.grid_bbox(1, int(info.get("row", 0)))[0]) - x0
                    away = abs(holder.winfo_rooty() - y0)
                    if 40 <= x <= 320 and (nearest is None or away < nearest):
                        nearest, found = away, x
                elif depth < 4 and kind in ("Frame", "TFrame"):
                    todo.extend((c, depth + 1) for c in w.winfo_children())
            return found
        except Exception:
            return 0

    def _watch_edmc(self) -> None:
        """Without the emblem, follow EDMC's own rows the moment they change: EDMC names them only once its window is
        up, names them again when the language changes, and shows the Suit row only when there is a suit."""
        if self.mark is not None:
            return
        try:
            main = self.frame.master.master
            for name in EDMC_LABELS:
                lab = main.children.get(name)
                if lab is not None:
                    for event in ("<Configure>", "<Map>", "<Unmap>"):
                        lab.bind(event, lambda e: self._align(), add="+")
        except Exception:
            pass

    def _fit(self, event=None) -> None:
        """The lines wrap where EDMC's window ends, however wide the other plugins made it."""
        try:
            room = self.frame.winfo_width() - self._label_col - self._edge
        except Exception:
            return
        if room < 120 or room == self._wrap:
            return
        self._wrap = room
        for lab in (self.war, self.nudge, self.orders_line, self.alert):
            lab.configure(wraplength=room)
        self.status.configure(wraplength=max(100, room - DOT_ROOM))

    def _drain(self) -> None:
        changed = False
        try:
            while True:
                self.core.ui_queue.get_nowait()
                changed = True
        except queue.Empty:
            pass
        try:
            self._ticks += 1
            self._dress()
            if self._ticks < 20 or self._ticks % 10 == 0:      # EDMC names its rows after the plugins are built
                self._align()
            if changed:
                # the window first: orders on show in it are read by the time the strip asks whether they are new
                if self.window is not None and self.window.winfo_exists():
                    self.window.render()
                self.refresh()
        except Exception as e:      # a drawing fault never stops the drain, and never reaches EDMC
            try:
                self.core.log.warning(f"strip: {e.__class__.__name__}: {e}")
            except Exception:
                pass
        try:
            self.frame.after(DRAIN_EVERY_MS, self._drain)
        except tk.TclError:
            pass                    # EDMC is closing


# ----- small parts in the site's look -------------------------------------------------------------------------------------

def chip(parent, text: str, command=None, active: bool = False, bg: str = HULL) -> tk.Label:
    """The site's chip: mono capitals in a one pixel edge; orange when it is the one chosen or under the pointer."""
    lab = tk.Label(parent, text=text.upper(), font=F_CHIP, bg=bg, fg=HUD if active else INK_DIM, padx=10, pady=3,
                   highlightthickness=1, highlightbackground=HUD if active else EDGE, cursor="hand2" if command else "")
    lab.active = active
    lab.command = command       # what a click does: the tests press a chip without a pointer
    if command:
        lab.bind("<Button-1>", lambda e: command())
        lab.bind("<Enter>", lambda e: lab.configure(fg=HUD))
        lab.bind("<Leave>", lambda e: lab.configure(fg=HUD if lab.active else INK_DIM))
    return lab


def set_chip(lab: tk.Label, active: bool) -> None:
    lab.active = active
    lab.configure(fg=HUD if active else INK_DIM, highlightbackground=HUD if active else EDGE)


def panel(parent, accent: Optional[str] = None) -> tk.Frame:
    """The site's panel: a darker field in a one pixel edge, with a thin bar of colour on top when it leads."""
    box = tk.Frame(parent, bg=PANEL, highlightthickness=1, highlightbackground=EDGE)
    if accent:
        tk.Frame(box, bg=accent, height=2).pack(fill="x")
    return box


def section(parent, text: str, note: str = "") -> tuple:
    """A section's name in small capitals with a note beside it. Returns (row, the note's label)."""
    row = tk.Frame(parent, bg=HULL)
    tk.Label(row, text=text.upper(), font=F_LABEL, bg=HULL, fg=HUD_DIM).pack(side="left")
    said = tk.Label(row, text=note, font=F_SMALL, bg=HULL, fg=INK_DIM)
    said.pack(side="left", padx=(8, 0))
    return row, said


def dark_title_bar(win) -> None:
    """Ask Windows to draw this window's title bar dark, to go with the hull. Nothing happens where it cannot."""
    try:
        import ctypes
        hwnd = ctypes.windll.user32.GetParent(win.winfo_id())
        value = ctypes.c_int(1)
        for attribute in (20, 19):       # DWMWA_USE_IMMERSIVE_DARK_MODE, and its number before Windows 10 2004
            if ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, attribute, ctypes.byref(value), ctypes.sizeof(value)) == 0:
                break
    except Exception:
        pass


class Scroll(tk.Canvas):
    """A slim scroll bar in the site's colours: a dim thumb to drag, gone when everything fits."""

    def __init__(self, parent, text: tk.Text):
        super().__init__(parent, width=8, bg=HULL, highlightthickness=0, bd=0)
        self.text = text
        self.thumb = self.create_rectangle(2, 0, 7, 0, fill=HUD_DIM, outline="")
        text.configure(yscrollcommand=self.set)
        self.bind("<Button-1>", self._drag)
        self.bind("<B1-Motion>", self._drag)

    def set(self, first, last) -> None:
        first, last, h = float(first), float(last), max(1, self.winfo_height())
        self.coords(self.thumb, 2, first * h, 7, last * h)
        self.itemconfigure(self.thumb, state="hidden" if last - first >= 0.999 else "normal")

    def _drag(self, event) -> None:
        first, last = self.text.yview()
        self.text.yview_moveto(max(0.0, event.y / max(1, self.winfo_height()) - (last - first) / 2))


class Page(tk.Frame):
    """One scrolling column in the site's look. It is a Text used as the scroller: cards, tiles and rows are embedded
    in it and the orders are its own words, so everything on a tab scrolls as one page under one slim bar."""

    def __init__(self, parent):
        super().__init__(parent, bg=HULL)
        self.text = tk.Text(self, wrap="word", bg=HULL, fg=INK, font=F_BODY, relief="flat", bd=0, highlightthickness=0, padx=0, pady=0,
                            spacing1=1, spacing3=3, cursor="arrow", insertwidth=0, takefocus=0, width=1, height=1,
                            selectbackground=EDGE, selectforeground=INK, inactiveselectbackground=EDGE, state="disabled")
        self.bar = Scroll(self, self.text)
        self.bar.pack(side="right", fill="y", padx=(6, 0))      # clear of the cards' own edge
        self.text.pack(side="left", fill="both", expand=True)
        self.width = 530
        self.on_resize = None        # called after the page changed width, for a tab that lays out by it
        self.rows: list = []         # the embedded rows, kept as wide as the page
        self.fits: list = []         # (label, margin): labels that wrap to the page's width
        t = self.text
        t.tag_configure("row", spacing1=0, spacing3=0)
        t.tag_configure("title", font=F_TITLE, foreground=HUD, spacing1=2, spacing3=4)
        t.tag_configure("head", font=F_HEAD, foreground=HUD, spacing1=12, spacing3=3)
        t.tag_configure("small", font=F_SMALL, foreground=INK_DIM)
        t.tag_configure("quote", foreground=INK_DIM, lmargin1=12, lmargin2=12)
        for level in range(3):
            left = 4 + 18 * level
            t.tag_configure(f"bullet{level}", lmargin1=left, lmargin2=left + 14, tabs=(left + 14,))
            t.tag_configure(f"step{level}", lmargin1=left, lmargin2=left + 22, tabs=(left + 22,))
        t.tag_configure("bold", font=F_BOLD)
        t.tag_configure("gap", spacing1=9)
        t.bind("<Configure>", self._resized)

    def clear(self) -> None:
        self.text.configure(state="normal")
        self.text.delete("1.0", "end")
        for child in self.text.winfo_children():
            child.destroy()
        self.rows, self.fits = [], []

    def done(self, top: float = 0.0) -> None:
        self.text.configure(state="disabled")
        if top:
            # Tk measures a Text's lines in the background, and a page of several cards is several lines of unknown
            # height: until they are measured, "the same place" lands somewhere else (the Squad tab, a card a system)
            self.text.update_idletasks()
            try:
                self.text.tk.call(self.text._w, "sync")
            except tk.TclError:
                pass
        self.text.yview_moveto(top)

    def top(self) -> float:
        return float(self.text.yview()[0])

    def row(self, after: int = 8) -> tk.Frame:
        """A new embedded row as wide as the page, with `after` pixels under it. Pack into what comes back."""
        holder = tk.Frame(self.text, bg=HULL)
        holder.strut = tk.Frame(holder, bg=HULL, height=1, width=self.width)
        holder.strut.pack(side="top")
        self.rows.append(holder)
        at = self.text.index("end-1c")
        self.text.window_create(at, window=holder)
        self.text.insert("end", "\n")
        tag = f"row{after}"
        self.text.tag_configure(tag, spacing1=0, spacing3=after)
        self.text.tag_add(tag, at, "end-1c")
        return holder

    def write(self, words: str, *tags: str) -> None:
        self.text.insert("end", words, tags)

    def fit(self, label, margin: int = 0) -> None:
        """This label wraps `margin` pixels short of the page's width, now and whenever the window is resized."""
        self.fits.append((label, margin))
        label.configure(wraplength=max(120, self.width - margin))

    def _resized(self, event=None) -> None:
        w = self.text.winfo_width() - 2
        if w < 200 or abs(w - self.width) < 2:
            return
        self.width = w
        for holder in self.rows:
            holder.strut.configure(width=w)
        for label, margin in self.fits:
            try:
                label.configure(wraplength=max(120, w - margin))
            except tk.TclError:
                pass
        if self.on_resize is not None:
            self.on_resize()


class ExilesWindow(tk.Toplevel):
    TABS = (("orders", "Orders"), ("mytick", "My tick"), ("squad", "Squad"), ("settings", "Settings"))
    SIZE = (580, 560)
    LEAST = (460, 360)

    def __init__(self, parent, core: ExilesPlugin, on_close=None):
        super().__init__(parent)
        self.core = core
        self._on_close = on_close
        self.tab = "orders"
        self._drawn: Optional[tuple] = None    # (tab, signature) of what the page shows: it is drawn again only when that changes
        self._agos: list = []                  # (label, time, words with {} for the time): said again as time passes
        self._session: Optional[tk.Label] = None
        self._asked: Optional[int] = None      # Refresh was pressed: how many times the site had been asked by then
        self._job = None
        self._titled = False
        self._opened = False                   # a tab was shown at the pilot's asking: the orders on show count as read
        self._copied: Optional[tuple] = None   # (what the foot says was copied, the clock until which it says so)
        self._pcs: list = []                   # (label, paired, heard) of the paired PCs: said again as time passes
        self._sure: Optional[int] = None       # the paired PC whose Remove was pressed once
        self._sure_until = 0.0
        self._placed: Optional[str] = None     # the place last kept in EDMC's config
        self._tick_last = False                # the My tick tab shows the tick before the open one
        self._move_job = None
        self.code_label: Optional[tk.Label] = None
        self.title("The Exiles")
        self.geometry(self._place())
        self.minsize(*self.LEAST)
        self.configure(bg=HULL)
        self.protocol("WM_DELETE_WINDOW", self.close)
        try:        # a pilot who keeps EDMC above the game gets this window above the game too, not opened behind it
            self.attributes("-topmost", bool(parent.attributes("-topmost")))
        except Exception:
            pass
        # the head: the squad's name, how the link is, who is flying
        head = tk.Frame(self, bg=HULL)
        head.pack(fill="x", padx=14, pady=(12, 8))
        tk.Label(head, text="THE EXILES", font=F_MARK, bg=HULL, fg=HUD).pack(side="left")
        self.who = tk.Label(head, text="", font=F_SMALL, bg=HULL, fg=INK_DIM)
        self.who.pack(side="right")
        self.state_words = tk.Label(head, text="", font=F_SMALL, bg=HULL, fg=INK)
        self.state_words.pack(side="right", padx=(0, 12))
        self.dot = tk.Canvas(head, width=DOT, height=DOT, bg=HULL, highlightthickness=0, bd=0)
        self.dot.pack(side="right", padx=(0, 5))
        self.dot_id = self.dot.create_oval(1, 1, DOT - 1, DOT - 1, fill=INK_DIM, outline="")
        tk.Frame(self, bg=EDGE, height=1).pack(fill="x", padx=14)
        # the tabs, as the site's chips
        bar = tk.Frame(self, bg=HULL)
        bar.pack(fill="x", padx=14, pady=(10, 8))
        self.chips = {}
        for key, label in self.TABS:
            c = chip(bar, label, command=lambda k=key: self.show(k))
            c.pack(side="left", padx=(0, 6))
            self.chips[key] = c
        # the foot first, so the page takes what is left
        foot = tk.Frame(self, bg=HULL)
        foot.pack(side="bottom", fill="x", padx=14, pady=(6, 10))
        self.foot_label = tk.Label(foot, text="", font=F_SMALL, bg=HULL, fg=INK_DIM)
        self.foot_label.pack(side="left")
        chip(foot, "Open ops site", command=lambda: webbrowser.open(self.core.site_url + "/#cmdr")).pack(side="right")
        chip(foot, "Refresh", command=self._refresh).pack(side="right", padx=(0, 6))
        tk.Frame(self, bg=EDGE, height=1).pack(side="bottom", fill="x", padx=14)
        self.page = Page(self)
        self.page.pack(fill="both", expand=True, padx=(14, 2), pady=(0, 2))
        self.page.on_resize = self._page_resized
        self.bind("<Map>", self._mapped)
        self.bind("<Configure>", self._moved)
        self.bind("<MouseWheel>", self._wheel)
        self.bind("<Button-4>", lambda e: self.page.text.yview_scroll(-40, "pixels"))
        self.bind("<Button-5>", lambda e: self.page.text.yview_scroll(40, "pixels"))
        self.render()
        self._job = self.after(LIVE_EVERY_MS, self._live_tick)

    # ----- the window itself ----------------------------------------------------------------------------------------------
    def _mapped(self, event) -> None:
        if event.widget is self and not self._titled:
            self._titled = True
            dark_title_bar(self)

    def _screens(self) -> tuple:
        """(left, top, right, bottom) of all the pilot's screens together on Windows, of the main one elsewhere."""
        try:
            import ctypes
            metric = ctypes.windll.user32.GetSystemMetrics
            x, y, w, h = metric(76), metric(77), metric(78), metric(79)      # the virtual screen
            if w > 0 and h > 0:
                return x, y, x + w, y + h
        except Exception:
            pass
        return 0, 0, self.winfo_screenwidth(), self.winfo_screenheight()

    def _place(self) -> str:
        """Where the window opens (0.3.0): where the pilot last left it and as large, when its head is still on one of
        their screens; else its own size, where the system puts it. A screen unplugged never leaves it out of reach."""
        w, h = self.SIZE
        m = re.match(r"^(\d+)x(\d+)\+(-?\d+)\+(-?\d+)$", self.core.settings.get("window") or "")
        if not m:
            return f"{w}x{h}"
        kw, kh, x, y = (int(v) for v in m.groups())
        left, top, right, bottom = self._screens()
        if self.LEAST[0] <= kw <= max(self.LEAST[0], right - left) and self.LEAST[1] <= kh <= max(self.LEAST[1], bottom - top):
            w, h = kw, kh
        if x + w - 80 >= left and x + 80 <= right and top - 8 <= y <= bottom - 60:
            return f"{w}x{h}+{x}+{y}"
        return f"{w}x{h}"

    def _moved(self, event) -> None:
        """The window was moved or sized: its place is kept once it has rested a moment."""
        if event.widget is not self:
            return
        try:
            if self._move_job is not None:
                self.after_cancel(self._move_job)
            self._move_job = self.after(REMEMBER_AFTER_MS, self._remember)
        except tk.TclError:
            pass

    def _remember(self) -> None:
        """Keep where the window is and how large, for the next time it opens; never while it is minimised or hidden."""
        self._move_job = None
        try:
            if self.state() != "normal" or not self.winfo_viewable():
                return
            place = self.geometry()
        except tk.TclError:
            return
        if place != self._placed and re.match(r"^\d+x\d+\+-?\d+\+-?\d+$", place):
            self._placed = place
            self.core.settings.set("window", place)

    def _copy(self, words: str, said: Optional[str] = None) -> None:
        """Put these words on the clipboard (a system's name for the galaxy map's search, the details for a leader) and
        say so at the foot for a moment."""
        if not to_clipboard(self, words):
            return
        self._copied = (said or f"Copied {words}", time.monotonic() + COPIED_FOR)
        self._live()
        try:
            self.after(int(COPIED_FOR * 1000) + 100, self._live_quietly)
        except tk.TclError:
            pass

    def _live_quietly(self) -> None:
        try:
            self._live()
        except tk.TclError:
            pass

    def _page_resized(self) -> None:
        """The page changed width: the My tick tab lays its cards out again where that took it from wide to narrow or
        back (the signature says which it is). Done once Tk is idle, never inside the resize itself."""
        if self.tab != "mytick":
            return
        try:
            self.after_idle(self._render_quietly)
        except tk.TclError:
            pass

    def _render_quietly(self) -> None:
        try:
            self.render()
        except tk.TclError:
            pass

    def _wheel(self, event) -> None:
        """The wheel turns the page wherever the pointer is on it; over the page's own words Tk already does."""
        if event.widget is not self.page.text:
            self.page.text.yview_scroll(-int(event.delta / 3), "pixels")

    def _refresh(self) -> None:
        if self.core.paired:
            self._asked = self.core.feed_reads
            self.core.refresh_feed_now()
        self._live()

    def _live_tick(self) -> None:
        try:
            self._live()
            self._job = self.after(LIVE_EVERY_MS, self._live_tick)
        except tk.TclError:
            pass

    def show(self, tab: str) -> None:
        tab = "squad" if tab == "wing" else tab     # the tab's name before 0.2.4
        if tab in dict(self.TABS):
            self.tab = tab
        self.deiconify()
        self.lift()
        self._opened = True
        self.render()

    def close(self) -> None:
        self._remember()
        if self._on_close:
            self._on_close()
        self.destroy()

    def destroy(self) -> None:
        for job in (self._job, self._move_job):     # a timer left running would call into a window that is gone
            try:
                if job is not None:
                    self.after_cancel(job)
            except Exception:
                pass
        super().destroy()

    # ----- drawing --------------------------------------------------------------------------------------------------------
    def _signature(self, feed: dict) -> str:
        """Everything the open tab shows, but the clock: the page is drawn again only when this changes, so reading the
        orders is never interrupted by the journal lines and Status entries that pass through every second in flight."""
        core = self.core
        if self.tab == "settings":
            p = core.pairing
            what = [core.paired, core.cmdr, bool(core.sender.unauthorized), p.state if p else None, p.code if p else None,
                    session_words(core) is not None, core.sender.last_error, list(version_words(core)),
                    feed.get("pcs"), core.unpairing, core.unpair_error, self._sure]
        else:
            part = {"orders": [feed.get("orders"), feed.get("wars")],
                    "mytick": [feed.get("myTick"), feed.get("xp"), feed.get("lastTick"), self._tick_last, self.page.width >= TICK_WIDE],
                    "squad": [feed.get("squad"), feed.get("wing"), core.system]}[self.tab]
            what = [core.paired, bool(feed), None if feed else core.feed_error, part]
        return json.dumps(what, sort_keys=True, default=str)

    def render(self, force: bool = False) -> None:
        core = self.core
        feed = core.feed if isinstance(core.feed, dict) else {}
        level, _, short = link_state(core)
        self.dot.itemconfigure(self.dot_id, fill=LEVELS[level])
        self.state_words.configure(text=short)
        self.who.configure(text=f"CMDR {core.cmdr}" if core.cmdr else "")
        for key, c in self.chips.items():
            set_chip(c, key == self.tab)
        sig = (self.tab, self._signature(feed))
        if force or sig != self._drawn:
            top = self.page.top() if self._drawn and self._drawn[0] == self.tab else 0.0
            self.page.clear()
            self._agos, self._session, self.code_label = [], None, None
            self._pcs = []
            {"orders": self._orders, "mytick": self._my_tick, "squad": self._squad, "settings": self._settings}[self.tab](feed)
            self.page.done(top)
            self._drawn = sig
        if self.tab == "orders" and self._opened and feed:
            core.orders_read()          # the orders are in front of the pilot: the strip's "new" goes
        self._live()

    def _live(self) -> None:
        """What changes without the page changing: the times, the session's counts and the foot."""
        core = self.core
        feed = core.feed if isinstance(core.feed, dict) else {}
        for label, iso, words in self._agos:
            try:
                label.configure(text=words.format(ago(iso)))
            except tk.TclError:
                pass
        if self._session is not None:
            try:
                self._session.configure(text=session_words(core) or "")
            except tk.TclError:
                pass
        for label, paired, heard in self._pcs:
            try:
                label.configure(text=pc_when(paired, heard))
            except tk.TclError:
                pass
        if self._copied is not None and time.monotonic() >= self._copied[1]:
            self._copied = None
        if self._asked is not None and core.feed_reads != self._asked:
            self._asked = None                  # the site was asked again since Refresh was pressed, answered or not
        if self._copied is not None:
            self.foot_label.configure(text=self._copied[0], fg=INK)
        elif self._asked is not None:
            self.foot_label.configure(text="Asking the site", fg=INK_DIM)
        elif core.feed_error:
            self.foot_label.configure(text=core.feed_error[:1].upper() + core.feed_error[1:], fg=SIGNAL)
        else:
            self.foot_label.configure(text=f"From the site {ago(feed.get('at'))}" if feed.get("at") else "", fg=INK_DIM)

    def _empty(self, words: str) -> None:
        lab = tk.Label(self.page.row(), text=words, font=F_BODY, bg=HULL, fg=INK_DIM, justify="left", anchor="w")
        lab.pack(anchor="w", pady=(8, 0))
        self.page.fit(lab, 4)

    def _waiting(self, feed: dict, words: str) -> bool:
        """True, with a line said, while a tab has nothing to draw yet: no pairing, or no word from the site."""
        if not self.core.paired:
            self._empty(PAIR_FIRST)
            return True
        if not feed:
            err = self.core.feed_error
            self._empty(err[:1].upper() + err[1:] if err else words)
            return True
        return False

    # ----- Orders -----------------------------------------------------------------------------------------------------------
    def _war_card(self, w: dict) -> None:
        c = war_card(w)
        box = panel(self.page.row(), accent=HUD)
        box.pack(fill="x")
        top = tk.Frame(box, bg=PANEL)
        top.pack(fill="x", padx=12, pady=(8, 0))
        tk.Label(top, text=c["label"], font=F_LABEL, bg=PANEL, fg=HUD_DIM).pack(side="right", anchor="n", pady=(4, 0))
        name = tk.Label(top, text=c["system"], font=F_NAME, bg=PANEL, fg=INK, justify="left", anchor="w", cursor="hand2")
        name.bind("<Button-1>", lambda e, s=c["system"]: self._copy(s))
        name.bind("<Enter>", lambda e, lab=name: lab.configure(fg=HUD))
        name.bind("<Leave>", lambda e, lab=name: lab.configure(fg=INK))
        name.pack(side="left")
        self.page.fit(name, 150)
        mid = tk.Frame(box, bg=PANEL)
        mid.pack(fill="x", padx=12, pady=(2, 8))
        if c["score"]:
            tk.Label(mid, text=c["score"], font=F_NUM, bg=PANEL, fg=HUD).pack(side="left", padx=(0, 12))
        said = tk.Frame(mid, bg=PANEL)
        said.pack(side="left")
        for words, face, colour in ((c["against"], F_BODY, INK), (c["pending"], F_SMALL, INK_DIM), (c["stake"], F_SMALL, INK_DIM)):
            if words:
                lab = tk.Label(said, text=words, font=face, bg=PANEL, fg=colour, justify="left", anchor="w")
                lab.pack(anchor="w")
                self.page.fit(lab, 130 if c["score"] else 40)
        if c["nudge"]:
            # as the site marks a row that needs a look: a bar on the left edge and a tint, never coloured words alone
            note = tk.Frame(box, bg=WARN_TINT)
            note.pack(fill="x")
            tk.Frame(note, bg=SIGNAL, width=3).pack(side="left", fill="y")
            lab = tk.Label(note, text=c["nudge"], font=F_SMALL, bg=WARN_TINT, fg=INK, padx=9, pady=4, justify="left", anchor="w")
            lab.pack(side="left")
            self.page.fit(lab, 40)

    def _orders(self, feed: dict) -> None:
        if self._waiting(feed, "Reading the orders from the ops site."):
            return
        for w in feed.get("wars") or []:
            if isinstance(w, dict) and w.get("system"):
                self._war_card(w)
        orders = feed.get("orders") if isinstance(feed.get("orders"), dict) else {}
        head, said = section(self.page.row(after=2), "Orders")
        head.pack(fill="x", pady=(2, 0))
        if orders.get("postedAt"):
            self._agos.append((said, orders["postedAt"], "posted {}"))
        blocks = orders_blocks(orders.get("md"), marked=True) if orders.get("md") else orders_blocks(orders.get("text"), marked=False)
        if not blocks:
            return self._empty("No orders posted right now.")
        write = self.page.write
        for i, b in enumerate(blocks):
            kind = b["kind"]
            line = (f"{kind}{b['level']}",) if kind in ("bullet", "step") else () if kind == "text" else (kind,)
            # a blank line in the orders is a little air; a heading brings its own, above and below
            if b["gap"] and i and kind not in ("title", "head") and blocks[i - 1]["kind"] not in ("title", "head"):
                line += ("gap",)
            if b["mark"]:
                write(b["mark"] + "\t", *line)
            for words, bold in b["runs"]:
                write(words, *(line + (("bold",) if bold and kind not in ("title", "head") else ())))
            write("\n", *line)

    # ----- My tick ----------------------------------------------------------------------------------------------------------
    def _my_tick(self, feed: dict) -> None:
        """The pilot's tick by faction and system, as BGS Tally's window says it and better where it can (0.4.0: until
        then one tile added the influence for every faction together, and a pilot needs to know who it was for): the
        totals as tiles, each saying how much of it was for us, and the squad XP in the same row; under them the work
        sorted by who it was for, our faction's card first. Two chips show the open tick or the one before it, which
        says what each system paid once it is scored, and Copy puts the tick on the clipboard as text for Discord.
        Every word is the site's.

        The cards were a system each until 0.5.0. A squad works for one faction: sorted by system its work was spread
        over a card a system, and a mission runner's tick was eight cards where it is two now.

        The squad XP stood under the cards until 0.4.1, where a pilot with many systems scrolled past them all to see
        it. Its two tiles now close the row of work tiles, and stay side by side whatever the number of those."""
        if self._waiting(feed, "Reading your tick from the ops site."):
            return
        t = feed.get("myTick") if isinstance(feed.get("myTick"), dict) else None
        if not t:
            return self._empty("Your work this tick shows here once the site has it.")
        before = feed.get("lastTick") if isinstance(feed.get("lastTick"), dict) else None
        last = self._tick_last and before is not None
        block = before if last else t
        words = tick_text(block)
        wide = self.page.width >= TICK_WIDE
        chipped = before is not None or bool(words)
        top = tk.Frame(self.page.row(after=4 if chipped and not wide else 6), bg=HULL)
        top.pack(fill="x", pady=(2, 0))
        head, said = section(top, "Last tick" if last else "Since the tick", tick_day(block.get("tickTime")) if last else "")
        head.pack(side="left")
        if not last and t.get("since"):
            self._agos.append((said, t["since"], "{}"))
        if chipped:
            # the chips stand at the head's right on a wide page, and on a line of their own under it on a narrow one
            chips = tk.Frame(top if wide else self.page.row(after=6), bg=HULL)
            if wide:
                chips.pack(side="right")
            else:
                chips.pack(anchor="w")
            if before is not None:
                chip(chips, "This tick", command=lambda: self._tick(False), active=not last).pack(side="left", padx=(0, 6))
                chip(chips, "Last tick", command=lambda: self._tick(True), active=last).pack(side="left", padx=(0, 6))
            if words:
                chip(chips, "Copy", command=lambda: self._copy(words, "Copied the tick, ready to paste")).pack(side="left")
        tiles = tick_tiles(block)
        groups = tick_factions(block)
        earned, note = tick_xp(feed, last)
        nothing = not tiles and not groups
        if nothing:
            self._empty(LAST_EMPTY if last else TICK_EMPTY)     # what the tab is for comes first, then the XP as ever
        if tiles or earned:
            # three abreast, so the cards under them start on the first screen; the XP closes the row
            self._tiles(tiles, across=3, together=earned, above=8 if nothing else 0)
        if note and (tiles or groups or earned):
            lab = tk.Label(self.page.row(after=4), text=note, font=F_SMALL, bg=HULL, fg=INK_DIM, justify="left", anchor="w")
            lab.pack(anchor="w")
            self.page.fit(lab, 4)
        if groups:
            self._factions(groups)

    def _tick(self, last: bool) -> None:
        """Show the open tick or the one before it, from the top of the page."""
        if last == self._tick_last:
            return
        self._tick_last = last
        self._drawn = None
        self.render()

    def _tiles(self, tiles: list, across: int = 2, together: list = (), above: int = 0) -> None:
        """Tiles side by side, `across` of them on a wide page and two on a narrow one: the number large, what it is
        under it in small capitals with anything more beside that, and on a line below, where the site said it, how
        much of it was for us. Where a tile is too slim to hold the more beside the name (three abreast, or a narrow
        page) it goes under the name too: two tiles with a rank and the way to the next one beside "TOTAL" are wider
        than the smallest window (0.4.0; the live check measures every row at that size).

        `together` are tiles that close the row and are never parted (the two of the squad XP, 0.4.1): they fill the
        end of the last row when they fit there whole, and start a row of their own when they do not. `above` is the
        air over the tiles, for when words stand before them."""
        narrow = self.page.width < TICK_WIDE
        across = 2 if narrow else max(2, across)
        slim = narrow or across > 2
        room = max(100, (self.page.width - 8 * (across - 1)) // across - 28)       # a tile's own width, less its edges
        tiles, kept = list(tiles), list(together)
        free = (across - len(tiles) % across) % across                             # the cells left in the last row
        if len(kept) > 1 and 0 < free < len(kept):
            tiles += [None] * free                                                 # an empty cell: no tile is drawn in it
        tiles += kept
        grid = tk.Frame(self.page.row(after=2), bg=HULL)
        grid.pack(fill="x", pady=(above, 0))
        for column in range(across):
            grid.columnconfigure(column, weight=1, uniform="tile")
        for i, tile in enumerate(tiles):
            if tile is None:
                continue
            label, number, more = tile[:3]
            ours = tile[3] if len(tile) > 3 else ""
            below = ([more] if more and slim else []) + ([ours] if ours else [])
            box = panel(grid)
            box.grid(row=i // across, column=i % across, sticky="nswe", padx=(0, 8) if i % across < across - 1 else 0, pady=(0, 8))
            tk.Label(box, text=number, font=F_NUM, bg=PANEL, fg=HUD).pack(anchor="w", padx=12, pady=(6, 0))
            under = tk.Frame(box, bg=PANEL)
            under.pack(anchor="w", padx=12, pady=(0, 1 if below else 8))
            tk.Label(under, text=label.upper(), font=F_LABEL, bg=PANEL, fg=INK_DIM).pack(side="left")
            if more and not slim:
                tk.Label(under, text=more, font=F_SMALL, bg=PANEL, fg=INK).pack(side="left", padx=(8, 0))
            for n, words in enumerate(below):
                tk.Label(box, text=words, font=F_SMALL, bg=PANEL, fg=INK, justify="left", anchor="w", wraplength=room).pack(
                    anchor="w", padx=12, pady=(0, 8 if n == len(below) - 1 else 0))

    def _factions(self, groups: list) -> None:
        """The tick sorted by who the work was for (0.5.0): our faction's card first with a line a system, then a side
        we back, then a faction we are at war with, then every other faction in one last card, each under its name. A
        line is the system, which a click copies for the galaxy map, where the faction stands there, and what the
        pilot did. What worked against us is said in words on a line of its own, marked as the site marks a row that
        needs a look: a bar on its left edge and a tint, never coloured words alone. What a system paid once the tick
        is scored stands under our line there. On a wide page a faction's standing has a column of its own, as in BGS
        Tally's table; on a narrow one it sits under the system, so the work keeps its room."""
        wide = self.page.width >= TICK_WIDE
        head, _ = section(self.page.row(after=4), "By faction", "click a system to copy it")
        head.pack(fill="x", pady=(6, 0))
        tables, names, stands, wraps = [], [], [], []
        for g in groups:
            ours, against = g["tone"] == "ours", g["tone"] == "against"
            box = panel(self.page.row(), accent=HUD if ours else HOSTILE if against else None)
            box.pack(fill="x")
            top = tk.Frame(box, bg=PANEL)
            top.pack(fill="x", padx=12, pady=(7, 5))
            tk.Label(top, text=g["count"], font=F_SMALL, bg=PANEL, fg=INK_DIM).pack(side="right")
            title = tk.Label(top, text=g["title"], font=F_BOLD, bg=PANEL, fg=HUD if ours else HOSTILE if against else INK_DIM,
                             justify="left", anchor="w")
            title.pack(side="left")
            self.page.fit(title, 150)
            tk.Frame(box, bg=EDGE, height=1).pack(fill="x", padx=12)
            for n, block in enumerate(g["blocks"]):
                indent = TICK_INDENT if block["name"] else 0
                if block["name"]:
                    whose = tk.Label(box, text=block["name"], font=F_BOLD, bg=PANEL, fg=INK, justify="left", anchor="w")
                    whose.pack(anchor="w", padx=12, pady=(6 if n else 5, 0))
                    self.page.fit(whose, 40)
                rows = tk.Frame(box, bg=PANEL)
                rows.pack(fill="x", padx=(12 + indent, 12), pady=(1 if block["name"] else 5, 7 if n == len(g["blocks"]) - 1 else 0))
                rows.columnconfigure(2 if wide else 1, weight=1)
                tables.append((rows, indent))
                for i, r in enumerate(block["rows"]):
                    where = rows if wide else tk.Frame(rows, bg=PANEL)
                    name = tk.Label(where, text=r["system"], font=F_BODY, bg=PANEL, fg=FRIENDLY, cursor="hand2", justify="left", anchor="w",
                                    wraplength=FACTION_WRAP if wide else FACTION_WRAP_NARROW)
                    name.bind("<Button-1>", lambda e, s=r["system"]: self._copy(s))
                    name.bind("<Enter>", lambda e, lab=name: lab.configure(fg=INK))
                    name.bind("<Leave>", lambda e, lab=name: lab.configure(fg=FRIENDLY))
                    stand = tk.Label(where, text=r["standing"], font=F_SMALL, bg=PANEL, fg=INK_DIM, justify="left", anchor="w",
                                     wraplength=STANDING_WRAP if wide else FACTION_WRAP_NARROW)
                    if wide:
                        name.grid(row=i, column=0, sticky="nw", pady=2)
                        stand.grid(row=i, column=1, sticky="nw", padx=(14, 0), pady=(3, 2))
                    else:
                        where.grid(row=i, column=0, sticky="nw", pady=2)
                        name.pack(anchor="w")
                        if r["standing"]:
                            stand.pack(anchor="w")
                    did = tk.Frame(rows, bg=PANEL)
                    did.grid(row=i, column=2 if wide else 1, sticky="nw", padx=(14, 0), pady=2)
                    if r["work"]:
                        lab = tk.Label(did, text=r["work"], font=F_BODY, bg=PANEL, fg=INK, justify="left", anchor="w")
                        lab.pack(anchor="w")
                        wraps.append((lab, 0))
                    if r["bad"]:
                        note = tk.Frame(did, bg=BAD_TINT)
                        note.pack(anchor="w", pady=(2, 1))
                        tk.Frame(note, bg=HOSTILE, width=3).pack(side="left", fill="y")
                        lab = tk.Label(note, text=f"{AGAINST_US} {r['bad']}", font=F_SMALL, bg=BAD_TINT, fg=INK, padx=7, pady=2, justify="left", anchor="w")
                        lab.pack(side="left")
                        wraps.append((lab, 20))
                    if r["note"]:
                        lab = tk.Label(did, text=r["note"], font=F_SMALL, bg=PANEL, fg=INK_DIM, justify="left", anchor="w")
                        lab.pack(anchor="w")
                        wraps.append((lab, 0))
                    if r["xp"]:
                        tk.Label(did, text=r["xp"], font=F_BOLD, bg=PANEL, fg=HUD).pack(anchor="w")
                    names.append((name, indent))
                    stands.append(stand)
        if not names:
            return
        # one column of systems for the whole tab and, on a wide page, one of standings: the work starts at the same
        # place in every card, under a faction's name too, and wraps short of the page's edge
        widest = max(lab.winfo_reqwidth() + indent for lab, indent in names)
        stood = max(lab.winfo_reqwidth() for lab in stands)
        taken = widest + 14 + stood if wide else max(widest, stood + TICK_INDENT)
        for rows, indent in tables:
            rows.columnconfigure(0, minsize=(widest if wide else taken) - indent)
            if wide:
                rows.columnconfigure(1, minsize=stood + 14)
        for lab, less in wraps:
            self.page.fit(lab, taken + 44 + less)          # the columns before it, the gap and the panel's own edges

    # ----- Squad ------------------------------------------------------------------------------------------------------------
    def _squad(self, feed: dict) -> None:
        """Who is in the game and where, as the ops site's In the game now panel shows it (0.2.4): a block a system, the
        pilot's own first, and under each the squadmates there with what each is doing. Wings stay on the site."""
        if self._waiting(feed, "Reading who is in the game."):
            return
        squad = squad_words(feed, self.core.system)
        if squad is None:
            return self._empty("Who is in the game shows here.")
        said = squad["count"]
        when = ago(squad["as_of"])
        head, note = section(self.page.row(after=6), "In the game now", f"{said}, as of {when}" if said and when else said)
        head.pack(fill="x", pady=(2, 0))
        if said and when:
            self._agos.append((note, squad["as_of"], said + ", as of {}"))
        if not squad["systems"]:
            return self._empty("Nobody else is in the game right now.")
        hint = tk.Label(self.page.row(after=6), text=COPY_HINT, font=F_SMALL, bg=HULL, fg=INK_DIM, justify="left", anchor="w")
        hint.pack(anchor="w")
        self.page.fit(hint, 4)
        tables, names, doings = [], [], []
        for group in squad["systems"]:
            box = panel(self.page.row(), accent=HUD if group["here"] else None)
            box.pack(fill="x")
            top = tk.Frame(box, bg=PANEL)
            top.pack(fill="x", padx=12, pady=(7, 5))
            count = group["count"] + (", you are here" if group["here"] else "")
            tk.Label(top, text=count, font=F_SMALL, bg=PANEL, fg=INK_DIM).pack(side="right")
            if group["system"]:
                name = tk.Label(top, text=group["system"], font=F_BOLD, bg=PANEL, fg=FRIENDLY, cursor="hand2", justify="left", anchor="w")
                name.bind("<Button-1>", lambda e, s=group["system"]: self._copy(s))
                name.bind("<Enter>", lambda e, lab=name: lab.configure(fg=INK))
                name.bind("<Leave>", lambda e, lab=name: lab.configure(fg=FRIENDLY))
            else:
                name = tk.Label(top, text="System not known yet", font=F_BOLD, bg=PANEL, fg=INK_DIM, justify="left", anchor="w")
            name.pack(side="left")
            self.page.fit(name, 150)
            tk.Frame(box, bg=EDGE, height=1).pack(fill="x", padx=12)
            rows = tk.Frame(box, bg=PANEL)
            rows.pack(fill="x", padx=12, pady=(5, 7))
            rows.columnconfigure(1, weight=1)
            tables.append(rows)
            for i, p in enumerate(group["pilots"]):
                who = tk.Label(rows, text=p["name"], font=F_BOLD, bg=PANEL, fg=INK, justify="left", anchor="w", wraplength=200)
                who.grid(row=i, column=0, sticky="nw", pady=1)
                doing = tk.Label(rows, text=p["doing"], font=F_SMALL, bg=PANEL, fg=INK_DIM, justify="left", anchor="w")
                doing.grid(row=i, column=1, sticky="w", padx=(16, 0), pady=1)
                names.append(who)
                doings.append(doing)
        # one column of names for the whole tab, as wide as the longest: what each pilot is doing starts at the same
        # place in every block, and wraps short of the page's edge
        widest = max(lab.winfo_reqwidth() for lab in names)
        for rows in tables:
            rows.columnconfigure(0, minsize=widest)
        for doing in doings:
            self.page.fit(doing, widest + 46)           # the names' column, the gap and the panel's own edges

    # ----- Settings ---------------------------------------------------------------------------------------------------------
    def _settings(self, feed: dict) -> None:
        core = self.core
        section(self.page.row(after=4), "Pairing")[0].pack(fill="x", pady=(2, 0))
        box = panel(self.page.row(after=10))
        box.pack(fill="x")
        p = core.pairing
        words, button = pairing_words(core)
        lab = tk.Label(box, text=words, font=F_BODY, bg=PANEL, fg=INK, justify="left", anchor="w")
        lab.pack(anchor="w", padx=12, pady=(8, 4))
        self.page.fit(lab, 30)
        waiting = bool(p and p.state == "waiting")
        if waiting and p.code:
            self.code_label = tk.Label(box, text=show_code(p.code), font=F_CODE, bg=PANEL, fg=HUD)
            self.code_label.pack(anchor="w", padx=12)
        row = tk.Frame(box, bg=PANEL)
        row.pack(anchor="w", padx=12, pady=(2, 10))
        if button:
            chip(row, button, command=self._pair, bg=PANEL).pack(side="left")
        if waiting:
            chip(row, "Cancel", command=self._cancel, bg=PANEL).pack(side="left")
        if not core.paired:
            # Before a pairing, what goes to the squad and what never does. No switches: a paired plugin sends it all.
            note = tk.Label(box, text=SENDS, font=F_SMALL, bg=PANEL, fg=INK_DIM, justify="left", anchor="w")
            note.pack(anchor="w", padx=12, pady=(0, 10))
            self.page.fit(note, 30)
        rows = pcs_rows(feed) if core.paired else None
        if rows:
            # The pilot's paired PCs (0.3.0). An install names no PC, so a row says which one this is and when each was
            # paired and last heard. Remove takes two presses: a slip of the pointer never ends a pairing.
            section(self.page.row(after=4), "Paired PCs")[0].pack(fill="x", pady=(2, 0))
            box = panel(self.page.row(after=4))
            box.pack(fill="x")
            for i, r in enumerate(rows):
                if i:
                    tk.Frame(box, bg=EDGE, height=1).pack(fill="x", padx=12)
                line = tk.Frame(box, bg=PANEL)
                line.pack(fill="x", padx=12, pady=6)
                busy, sure = core.unpairing == r["key"], self._sure == r["key"]
                words = REMOVING if busy else REMOVE_SURE if sure else REMOVE
                press = None if core.unpairing is not None else (lambda k=r["key"]: self._remove(k))
                chip(line, words, command=press, active=sure or busy, bg=PANEL).pack(side="right")
                tk.Label(line, text=r["title"], font=F_BOLD, bg=PANEL, fg=INK).pack(side="left")
                when = tk.Label(line, text=pc_when(r["paired"], r["heard"]), font=F_SMALL, bg=PANEL, fg=INK_DIM)
                when.pack(side="left", padx=(12, 0))
                self._pcs.append((when, r["paired"], r["heard"]))
            failed = unpair_words(core)
            note = tk.Label(self.page.row(after=10), text=failed or (PCS_NOTE_ONE if len(rows) == 1 and rows[0]["here"] else PCS_NOTE),
                            font=F_SMALL, bg=HULL, fg=SIGNAL if failed else INK_DIM, justify="left", anchor="w")
            note.pack(anchor="w")
            self.page.fit(note, 4)
        said = session_words(core)
        if said:
            section(self.page.row(after=2), "This session")[0].pack(fill="x", pady=(10, 0))
            self._session = tk.Label(self.page.row(after=2), text=said, font=F_SMALL, bg=HULL, fg=INK_DIM, justify="left", anchor="w")
            self._session.pack(anchor="w")
            self.page.fit(self._session, 4)
        if core.sender.last_error:
            lab = tk.Label(self.page.row(after=2), text=f"Last trouble: {core.sender.last_error}.", font=F_SMALL, bg=HULL, fg=SIGNAL,
                           justify="left", anchor="w")
            lab.pack(anchor="w")
            self.page.fit(lab, 4)
        foot = tk.Frame(self.page.row(), bg=HULL)
        foot.pack(fill="x", pady=(10, 0))
        version, newer = version_words(core)
        tk.Label(foot, text=version, font=F_SMALL, bg=HULL, fg=SIGNAL if newer else INK_DIM).pack(side="left")
        if newer:
            chip(foot, "Get the update", command=self._open_update).pack(side="left", padx=(10, 0))
        # what a pilot pastes to a leader who asks what the plugin is doing (0.3.0); no key is in it
        chip(foot, "Copy details", command=self._copy_details).pack(side="right")
        note = tk.Label(self.page.row(), text=DETAILS_NOTE, font=F_SMALL, bg=HULL, fg=INK_DIM, justify="left", anchor="w")
        note.pack(anchor="w", pady=(4, 0))
        self.page.fit(note, 4)

    def _copy_details(self) -> None:
        self._copy(details_words(self.core), "Copied the details")

    def _remove(self, key: int) -> None:
        """Remove a paired PC: the first press asks, a second within SURE_FOR seconds does it."""
        now = time.monotonic()
        if self._sure == key and now < self._sure_until:
            self._sure = None
            self.core.unpair(key)
        else:
            self._sure, self._sure_until = key, now + SURE_FOR
            try:
                self.after(int(SURE_FOR * 1000) + 50, self._sure_over)
            except tk.TclError:
                pass
        self.render(force=True)

    def _sure_over(self) -> None:
        if self._sure is not None and time.monotonic() >= self._sure_until:
            self._sure = None
            try:
                self.render(force=True)
            except tk.TclError:
                pass

    def _pair(self) -> None:
        self.core.begin_pairing()
        self.render()

    def _cancel(self) -> None:
        self.core.cancel_pairing()
        self.render()

    def _open_update(self) -> None:
        webbrowser.open((self.core.update or {}).get("url") or (self.core.site_url + "/plugin/"))
