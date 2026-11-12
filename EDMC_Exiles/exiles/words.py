"""What the plugin says, in words. No tkinter here: ui.py draws what these return, and the tests read every
line a pilot sees without opening a window. Times are said as a pilot says them ("12 min ago"), never as a clock in a
time zone."""
from __future__ import annotations

import platform
import re
from datetime import datetime, timezone
from typing import Any, Optional

from .core import show_code, utc_now_iso
from .status import decode_flags

# how the link to the squad stands, for the dot beside it
GOOD, WARN, BAD, IDLE = "good", "warn", "bad", "idle"

TILES = (("bonds", "Combat bonds"), ("bounties", "Bounties"), ("missions", "Influence from missions"), ("trade", "Trade profit"),
         ("exploration", "Exploration data"))


def ago(iso: Any, now: Optional[datetime] = None) -> str:
    """A time as a pilot says it: "just now", "12 min ago", "3 h ago", "2 d ago". Nothing when there is no time."""
    try:
        then = datetime.strptime(str(iso)[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return ""
    secs = ((now or datetime.now(timezone.utc)) - then).total_seconds()
    if secs < 90:
        return "just now"
    if secs < 3600:
        return f"{int(secs // 60)} min ago"
    if secs < 86400:
        return f"{int(secs // 3600)} h ago"
    return f"{int(secs // 86400)} d ago"


def needs_pairing(core) -> bool:
    """True when only a pairing gets the plugin going: it never had one, or the squad's database or the site turned
    its key away (this PC was taken off the pilot's list, or its key was retired)."""
    return not core.paired or bool(core.sender.unauthorized) or bool(getattr(core, "feed_refused", False))


def link_state(core) -> tuple:
    """(level, words, short) for the one line that says whether the plugin is doing its job: the words for the strip,
    and the same in a word or two for the window's head."""
    p = core.pairing
    if not core.paired:
        if p and p.state == "waiting" and p.code:
            return WARN, f"Type {show_code(p.code)} on the ops site", "Pairing"
        if p and p.state == "waiting":
            return IDLE, "Asking the site for a code", "Pairing"
        return IDLE, "Not paired yet", "Not paired"
    if needs_pairing(core):
        return BAD, "Pair again: this plugin's key is no longer accepted", "Pair again"
    kept = core.sender.spooled()
    if core.sender.last_error and (kept or core.sender.waiting()):
        if kept:
            return WARN, f"Offline, {kept} batch{'' if kept == 1 else 'es'} kept until it is back", "Offline"
        return WARN, "Offline, trying again", "Offline"
    return GOOD, "Connected", "Connected"


def _wars(core) -> list:
    wars = core.feed.get("wars") if isinstance(core.feed, dict) else None
    return [w for w in (wars or []) if isinstance(w, dict) and w.get("system")]


def _same(a: Any, b: Any) -> bool:
    return bool(a) and bool(b) and str(a).casefold() == str(b).casefold()


def _civil(w: dict) -> bool:
    return str(w.get("warType") or "").lower() == "civilwar"


def _pending(w: dict) -> bool:
    return str(w.get("status") or "").lower() == "pending"


def war_words(core) -> tuple:
    """(line, nudge) for the strip, which has room for one war: the one where the pilot is, else the first that waits
    for someone to bring it the day's result, else the first. The Orders tab has a card for every war. The nudge
    shows only while that war's system waits, and tells the pilot who sits there what to do."""
    wars = _wars(core)
    if not wars:
        return None, None
    here = next((w for w in wars if _same(w.get("system"), core.system)), None)
    w = here or next((w for w in wars if w.get("tickBehind")), None) or wars[0]
    bits = [str(w["system"])]
    kind = "civil war" if _civil(w) else ""
    if _pending(w):
        bits.append(f"{kind} pending".strip())
    elif w.get("day"):
        bits.append(f"{kind} day {w['day']}".strip())
    elif kind:
        bits.append(kind)
    us, them = w.get("us"), w.get("them")
    if not _pending(w) and isinstance(us, int) and isinstance(them, int):
        bits.append(f"we lead {us} to {them}" if us > them else f"behind {us} to {them}" if us < them else f"level {us} to {them}")
    nudge = None
    if w.get("tickBehind"):
        nudge = "Jump out and back in to update it." if here else "Needs an update when you are next there."
    return ", ".join(bits), nudge


def orders_words(core) -> Optional[str]:
    """What the strip says of the orders, and only while there is something to say (0.3.0): they have changed since
    the pilot last had them open. Opening the Orders tab takes the line away."""
    return "new" if core.paired and core.orders_new else None


def alert_words(status: Any) -> Optional[str]:
    """Only what needs saying about the ship or the suit: the strip stays quiet while all is well."""
    if not isinstance(status, dict):
        return None
    d = decode_flags(status.get("Flags"), status.get("Flags2"))
    bits = [w for k, w in (("being_interdicted", "being interdicted"), ("in_danger", "in danger"), ("low_fuel", "low fuel"),
                           ("overheating", "overheating"), ("low_oxygen", "low oxygen"), ("low_health", "low health")) if d.get(k)]
    legal = status.get("LegalState")
    if isinstance(legal, str) and legal not in ("Clean", "Allied", "", "Speeding"):
        bits.append(legal.lower())
    return ", ".join(bits).capitalize() if bits else None


def war_card(w: dict) -> dict:
    """One war as the window's card says it: the system, what kind and which day, the days won, who against, and what
    is at stake in the war posts' own words."""
    pending = _pending(w)
    kind = "CIVIL WAR" if _civil(w) else "WAR"
    when = "PENDING" if pending else f"DAY {w['day']}" if w.get("day") else ""
    us, them = w.get("us"), w.get("them")
    side = w.get("side") or None
    stakes = w.get("stakes") if isinstance(w.get("stakes"), dict) else {}
    lose, win = stakes.get("lose"), stakes.get("win")
    if side:
        at_stake = [f"{side} can lose {lose}." if lose else "", f"{side} takes {win} if we win." if win else ""]
    else:
        at_stake = [f"We can lose {lose}." if lose else "", f"We can win {win}." if win else ""]
    at_stake = " ".join(s for s in at_stake if s)
    if not at_stake and w.get("stake"):          # a site that does not say which way yet
        at_stake = f"For {w['stake']}."
    return {"system": str(w.get("system") or ""), "label": f"{kind}  {when}".strip(),
            "score": None if pending or not isinstance(us, int) or not isinstance(them, int) else f"{us} : {them}",
            "against": (f"For {side}, against {w.get('enemy')}" if side else f"Against {w.get('enemy')}") if w.get("enemy") else "",
            "stake": at_stake or None,
            "nudge": "Needs an update. Jump out and back in while you are there." if w.get("tickBehind") and not pending else None,
            "pending": "Not started yet: it begins at the next tick." if pending else None}


# ----- the orders, as blocks to draw ----------------------------------------------------------------------------------------
# The site sends the orders with a few marks and nothing else: "# ", "## ", "### " headings, **bold**,
# "- " and "1. " list lines with two spaces of indent a level, "-# " small print and "> " quotes.

_HEAD = re.compile(r"^(#{1,3})\s+(\S.*)$")
_SMALL = re.compile(r"^-#\s+(\S.*)$")
_QUOTE = re.compile(r"^>\s?(.*)$")
_BULLET = re.compile(r"^(\s*)[-*\u2022]\s+(\S.*)$")
_STEP = re.compile(r"^(\s*)(\d{1,3})[.)]\s+(\S.*)$")
_WHOLE_BOLD = re.compile(r"^\*\*([^*]+)\*\*(\s*\([^)]*\))?\s*:?$")
_BOLD = re.compile(r"\*\*(?=\S)((?:(?!\*\*).)+?)(?<=\S)\*\*")      # from one pair of stars to the next, never across a pair


def bold_runs(line: str) -> list:
    """[(words, bold)] for one line: **these words** are bold. A mark with no partner is dropped, never shown."""
    line = re.sub(r"\*{3,}", "**", str(line))
    runs, at = [], 0
    for m in _BOLD.finditer(line):
        if m.start() > at:
            runs.append((line[at:m.start()], False))
        runs.append((m.group(1), True))
        at = m.end()
    if at < len(line):
        runs.append((line[at:], False))
    return [(t.replace("**", ""), b) for t, b in runs if t.replace("**", "")]


def _plain(line: str) -> str:
    return "".join(t for t, _ in bold_runs(line))


def orders_blocks(words: Any, marked: bool = True) -> list:
    """The orders as blocks: {"kind", "runs", "level", "mark", "gap"}. Kinds: title (# ), head (## , ### , or a line that
    is bold from end to end, a note in brackets may follow), text, bullet, step, small (-# ) and quote (> ). `gap` is
    True when a blank line came before. With marked False the words are plain text and their first line is the title."""
    out: list = []
    gap = False
    for raw in str(words or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = raw.rstrip()
        if not line.strip():
            gap = bool(out)
            continue
        block = {"kind": "text", "runs": None, "level": 0, "mark": "", "gap": gap}
        gap = False
        m = _HEAD.match(line)
        if m:
            block.update(kind="title" if len(m.group(1)) == 1 else "head", runs=[(_plain(m.group(2)), True)])
        elif _SMALL.match(line):
            block.update(kind="small", runs=bold_runs(_SMALL.match(line).group(1)))
        elif _QUOTE.match(line):
            block.update(kind="quote", runs=bold_runs(_QUOTE.match(line).group(1)))
        elif _BULLET.match(line):
            m = _BULLET.match(line)
            block.update(kind="bullet", level=min(2, len(m.group(1).replace("\t", "    ")) // 2), mark="\u2022", runs=bold_runs(m.group(2)))
        elif _STEP.match(line):
            m = _STEP.match(line)
            block.update(kind="step", level=min(2, len(m.group(1).replace("\t", "    ")) // 2), mark=f"{m.group(2)}.", runs=bold_runs(m.group(3)))
        elif _WHOLE_BOLD.match(line.strip()):
            m = _WHOLE_BOLD.match(line.strip())
            block.update(kind="head", runs=[(m.group(1).strip() + (m.group(2) or ""), True)])
        elif not marked and not out:
            block.update(kind="title", runs=[(_plain(line.strip()), True)])
        else:
            block.update(runs=bold_runs(line.strip()))
        if block["runs"]:
            out.append(block)
    return out


# Said on the My tick tab while it has no tile to show (0.2.7). The line it had, "Nothing for our factions yet this
# tick.", told a pilot nothing of what the tab is for, and was wrong besides: the tiles count the pilot's work for any
# faction.
TICK_EMPTY = ("Nothing handed in yet this tick. Your combat bonds, bounties, mission influence, trade profit and exploration data "
              "show here as you hand them in, by system and by the faction they were for.")


def tick_tiles(my_tick: Any) -> list:
    """[(label, number, more)] for the pilot's tick: the number large, what it is under it, and anything more the site
    said beside (the zones a pilot's bonds came from). A site since 0.4.0 sends the tiles whole, each with how much of
    it was for us, and they come back as [(label, number, more, ours)]: ours is "all for Utlagarnir", "300k cr for
    Utlagarnir" or "none for Utlagarnir"."""
    if not isinstance(my_tick, dict):
        return []
    sent = my_tick.get("tiles")
    if isinstance(sent, list):
        whole = [(_said(t.get("label")), _said(t.get("number")), _said(t.get("more")), _said(t.get("ours"))) for t in sent if isinstance(t, dict)]
        return [t for t in whole if t[0] and t[1]]
    out = []
    for key, label in TILES:
        value = my_tick.get(key)
        if value in (None, "", 0):
            continue
        m = re.match(r"^(\S+(?:\s+cr)?)\s*(.*)$", str(value), flags=re.I)
        number, more = (m.group(1), m.group(2)) if m else (str(value), "")
        if more.lower() in ("influence", "profit"):
            more = ""
        out.append((label, number, more))
    return out


# The pilot's squad XP on the My tick tab (0.3.1). The tab said only that XP is worked out two hours after the tick
# closes; it now shows what the newest scored tick paid and where the pilot stands. The site sends its own read of the
# squad's database (feed.xp); nothing is worked out here.
XP_NOTE = "XP for this tick is worked out about two hours after the tick closes."
# What the XP tiles are called. They stand in one row with the work tiles since 0.4.1, with no heading of their own over
# them, so each name says XP itself.
XP_LAST, XP_TOTAL, XP_PAID = "XP, last scored tick", "Total XP", "XP it paid"
MONTHS = ("Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec")


def tick_day(iso: Any) -> str:
    """A tick by the day it began, as the squad names its ticks: "the 10 Nov tick". Nothing when there is no time."""
    try:
        day = datetime.strptime(str(iso)[:10], "%Y-%m-%d")
    except (TypeError, ValueError):
        return ""
    return f"the {day.day} {MONTHS[day.month - 1]} tick"


def _count(value: Any) -> Optional[int]:
    return int(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def xp_tiles(xp: Any) -> list:
    """[(label, number, more)] for the pilot's squad XP: what the newest scored tick paid and which tick that was,
    then the total with the rank it makes and how far the next rank is. Empty while the site has not said."""
    if not isinstance(xp, dict):
        return []
    out = []
    last = xp.get("last") if isinstance(xp.get("last"), dict) else {}
    paid = _count(last.get("xp"))
    if paid is not None:
        out.append((XP_LAST, f"+{paid:,}", tick_day(last.get("tickTime"))))
    total = _count(xp.get("total"))
    if total is not None:
        ahead = xp.get("next") if isinstance(xp.get("next"), dict) else {}
        to_go = _count(ahead.get("remaining"))
        bits = [str(xp.get("rank") or ""), f"{to_go:,} to {ahead['name']}" if to_go is not None and ahead.get("name") else ""]
        out.append((XP_TOTAL, f"{total:,}", ", ".join(b for b in bits if b)))
    return out


# ----- the pilot's tick by system and faction (0.4.0) -------------------------------------------------------------------
# The tab's one tile used to add the influence for every faction into one number ("+6"), where BGS Tally's activity
# window, which pilots are used to, says who it was for. The site now sends the tick as Tally's window says it, by
# system and faction (feed.myTick: tiles, systems, text), and the tick before it (feed.lastTick) with what each system
# paid. Every phrase is the site's; these only see that the page is handed nothing it cannot draw.
AGAINST_US = "Against us:"
LAST_EMPTY = "Nothing was handed in that tick."
LAST_UNPAID = "This tick paid no XP."
TONES = ("ours", "against")


def _said(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _phrases(value: Any) -> str:
    return ", ".join(p for p in (_said(x) for x in (value if isinstance(value, list) else [])) if p)


def tick_systems(block: Any) -> list:
    """The cards of one tick, a system each, in the site's order (the ones with work for us first):
    [{"name", "tone", "xp", "factions": [{"name", "tone", "note", "standing", "work", "bad"}]}]. A tone is "ours",
    "against" or "": for a system, whether anything there worked against us, else whether there was work for us; for
    a faction, whose side it is on. xp is what the system paid ("+240 XP") once the tick is scored. standing is the
    faction's share and state on one line, work what the pilot did for it, bad what of it worked against us, note what
    the site says of the faction ("Our opponent in the war here."). A faction with nothing done and a system with no
    such faction are left out. Empty for a site from before 0.4.0."""
    listed = block.get("systems") if isinstance(block, dict) else None
    out = []
    for s in listed if isinstance(listed, list) else []:
        if not isinstance(s, dict) or not _said(s.get("name")):
            continue
        rows = []
        for f in s.get("factions") if isinstance(s.get("factions"), list) else []:
            if not isinstance(f, dict) or not _said(f.get("name")):
                continue
            work, bad = _phrases(f.get("work")), _phrases(f.get("bad"))
            if not work and not bad:
                continue
            rows.append({"name": _said(f["name"]), "tone": f.get("tone") if f.get("tone") in TONES else "", "note": _said(f.get("note")),
                         "standing": "  ".join(b for b in (_said(f.get("share")), _said(f.get("state"))) if b), "work": work, "bad": bad})
        if rows:
            paid = _count(s.get("xp"))
            out.append({"name": _said(s["name"]), "tone": s.get("tone") if s.get("tone") in TONES else "",
                        "xp": f"+{paid:,} XP" if paid and paid > 0 else "", "factions": rows})
    return out


OTHERS = "Other factions"


def tick_factions(block: Any) -> list:
    """The lines of tick_systems sorted by who the work was for instead of where it was done (0.5.0).
    Our own faction leads with a card of its own, then a side we back in a war, then a faction we are at war with;
    every other faction shares one last card, each under its name. A squad works for one faction, so its work stands
    together at the top, a line a system. The site still sends the lines by system (feed.myTick.systems); only their
    order on the page is made here.

    [{"title", "tone", "count", "blocks": [{"name", "rows": [{"system", "standing", "work", "bad", "note", "xp"}]}]}].
    tone is "ours", "against" or "". A card of one faction has one block with no name; the last card has a block a
    faction. xp, what a system paid once the tick is scored, stands with our line in that system."""
    ours = _said(block.get("ours")).casefold() if isinstance(block, dict) else ""
    own, sided, against, others = {}, {}, {}, {}
    for s in tick_systems(block):
        paid = s["xp"]
        for f in s["factions"]:
            row = {"system": s["name"], "standing": f["standing"], "work": f["work"], "bad": f["bad"], "note": f["note"], "xp": ""}
            if f["tone"] == "ours":
                row["xp"], paid = paid, ""
                bucket = own if f["name"].casefold() == ours else sided
            else:
                bucket = against if f["tone"] == "against" else others
            bucket.setdefault(f["name"], []).append(row)
    count = lambda rows: "one system" if len(rows) == 1 else f"{len(rows)} systems"       # noqa: E731
    by_name = lambda d: sorted(d, key=str.casefold)                                        # noqa: E731
    groups = [{"title": name, "tone": tone, "count": count(d[name]), "blocks": [{"name": "", "rows": d[name]}]}
              for d, tone in ((own, "ours"), (sided, "ours"), (against, "against")) for name in by_name(d)]
    if others:
        groups.append({"title": OTHERS, "tone": "", "count": "one faction" if len(others) == 1 else f"{len(others)} factions",
                       "blocks": [{"name": name, "rows": others[name]} for name in by_name(others)]})
    return groups


def tick_text(block: Any) -> Optional[str]:
    """The tick as the site wrote it for Discord, for the Copy chip; None when there is nothing to copy."""
    words = block.get("text") if isinstance(block, dict) else None
    return words.strip() if isinstance(words, str) and words.strip() else None


def tick_xp(feed: Any, last: bool = False) -> tuple:
    """(tiles, note) for the squad XP beside a tick's work. With the open tick: what the newest scored tick paid and
    the total, and the line that XP is worked out two hours after a tick closes. With the tick before it: what that
    tick paid once it is scored, with the active tick's bonus in it, and the total; until it is scored the same line,
    and when it was scored and paid nothing, a line that says so."""
    feed = feed if isinstance(feed, dict) else {}
    tiles = xp_tiles(feed.get("xp"))
    if not last:
        return tiles, XP_NOTE
    block = feed.get("lastTick") if isinstance(feed.get("lastTick"), dict) else {}
    x = block.get("xp") if isinstance(block.get("xp"), dict) else {}
    total = [t for t in tiles if t[0] == XP_TOTAL]
    paid = _count(x.get("paid"))
    if x.get("state") == "scored" and paid is not None:
        bonus = _count(x.get("bonus"))
        return [(XP_PAID, f"+{paid:,}", "", f"{bonus:,} of it for an active tick" if bonus else "")] + total, ""
    return total, XP_NOTE if x.get("state") == "pending" else LAST_UNPAID


def squad_words(feed: Any, here: Any = None) -> Optional[dict]:
    """Who is in the game and where, for the Squad tab (0.2.4; it was a Wing tab before, and wings stay on the site).
    The squadmates by system, as the ops site's In the game now panel groups them: the system the pilot is in first,
    then the busiest, then by name, and pilots whose system is not known yet last. The pilot themself is not listed.
    `here` is where the pilot is by the plugin's own word, which is fresher than the site's. None while the site has
    not said who is in the game.

    {"count": "5 of the squad", "as_of": the read's time or None,
     "systems": [{"system": a name or None, "count": "2 pilots", "here": bool, "pilots": [{"name", "doing"}]}]}"""
    if not isinstance(feed, dict):
        return None
    squad = feed.get("squad") if isinstance(feed.get("squad"), dict) else None
    listed = squad.get("pilots") if squad else feed.get("wing")      # "wing": a site from before the Squad tab
    if not isinstance(listed, list):
        return None
    mine = str(here or (squad or {}).get("here") or "").strip().casefold()
    groups: dict = {}
    for p in listed:
        if not isinstance(p, dict):
            continue
        system = str(p.get("system") or "").strip()
        group = groups.setdefault(system.casefold(), {"system": system, "pilots": []})
        group["pilots"].append({"name": str(p.get("name") or "A pilot"), "doing": str(p.get("doing") or "flying")})
    systems = []
    for key, group in groups.items():
        pilots = sorted(group["pilots"], key=lambda x: x["name"].casefold())
        systems.append({"system": group["system"] or None, "count": "one pilot" if len(pilots) == 1 else f"{len(pilots)} pilots",
                        "here": bool(key) and key == mine, "pilots": pilots})
    systems.sort(key=lambda s: (not s["here"], s["system"] is None, -len(s["pilots"]), (s["system"] or "").casefold()))
    total = sum(len(s["pilots"]) for s in systems)
    return {"count": f"{total} of the squad" if total else "", "as_of": (squad or {}).get("asOf") or None, "systems": systems}


# said before a pairing, where a pilot decides: what goes to the squad and what never does. There are no switches.
SENDS = ("Once paired, the plugin sends your journal, where you are, and your cargo, materials, loadout and ranks to the "
         "squad's database. Chat, the friends list and music never leave this PC.")


def pairing_words(core) -> tuple:
    """(words, button) for the pairing panel; the button is None while a code is out."""
    p = core.pairing
    waiting = bool(p and p.state == "waiting")
    if waiting and p.code:
        return "Sign in on the ops site, open Cmdr, then Settings, and type this code. It is good for ten minutes.", None
    if waiting:
        return "Asking the site for a code.", None
    if core.paired:
        if needs_pairing(core):
            return "This plugin's key is no longer accepted. Pair again to carry on.", "Pair again"
        who = f"Paired as CMDR {core.cmdr}." if core.cmdr else "Paired."
        return f"{who} Pairing again gives this plugin a new key. BGS Tally is never touched.", "Pair again"
    if p and p.state == "expired":
        return "That code ran out. Press Pair for a new one.", "Pair"
    if p and p.state == "failed":
        return "The site could not be reached. Try again in a minute.", "Pair"
    return "Press Pair, then type the code it shows on the ops site. There is no key to copy.", "Pair"


def session_words(core) -> Optional[str]:
    """What went out since EDMC started, or None when nothing has yet."""
    s = core.sender
    kept = s.spooled()
    if not (s.counts.get("events") or kept):
        return None
    line = f"{s.counts.get('events', 0):,} journal lines sent, {s.counts.get('skipped', 0):,} of them already on file"
    if kept:
        line += f". {kept} batch{'' if kept == 1 else 'es'} kept on this PC until the connection is back"
    if s.counts.get("refused"):
        line += f". {s.counts['refused']} set aside that the squad's database would not take"
    return line + "."


def version_words(core) -> tuple:
    """(words, newer) for the version line."""
    newer = bool(core.update and core.update.get("newer"))
    return f"Version {core.version}" + (f". Version {core.update['latest']} is out." if newer else ""), newer


# ----- the paired PCs, on the Settings tab (0.3.0) -------------------------------------------------------------------------
# An install names no PC (its id is sixteen random characters), so a row says what tells two apart: whether it is the
# one in front of the pilot, when it was paired and when the squad's database last heard from it.
PCS_NOTE = "Removing a PC ends its key. It can be paired again whenever you like."
PCS_NOTE_ONE = "Removing this PC unpairs the plugin here. It can be paired again whenever you like."
REMOVE, REMOVE_SURE, REMOVING = "Remove", "Really remove", "Removing"


def pcs_rows(feed: Any) -> Optional[list]:
    """[{"key", "here", "title", "paired", "heard"}] for the pilot's paired PCs, in the site's order (the one asking
    first). `paired` and `heard` are times for the page to say as "2 d ago". None while the site has not said: a site
    from before 0.3.0, or no word from it yet."""
    pcs = feed.get("pcs") if isinstance(feed, dict) else None
    if not isinstance(pcs, list):
        return None
    rows = []
    for p in pcs:
        key = p.get("key") if isinstance(p, dict) else None
        if not isinstance(key, int) or isinstance(key, bool):
            continue
        here = bool(p.get("here"))
        rows.append({"key": key, "here": here, "title": "This PC" if here else "Another PC",
                     "paired": p.get("pairedAt") or None, "heard": p.get("lastHeard") or None})
    return rows


def pc_when(paired: Any, heard: Any, now: Optional[datetime] = None) -> str:
    """One PC's two times as a pilot says them: "paired 2 d ago, heard just now"."""
    bits = [f"paired {ago(paired, now)}" if ago(paired, now) else "", f"heard {ago(heard, now)}" if ago(heard, now) else "not heard from yet"]
    return ", ".join(b for b in bits if b)


def unpair_words(core) -> Optional[str]:
    """Why the last try to take a PC off the list failed, as a line; None when there is nothing to say."""
    why = getattr(core, "unpair_error", None)
    return f"That PC could not be removed: {why}." if why else None


# ----- what a pilot copies for a leader who asks what the plugin is doing (0.3.0) -----------------------------------------
DETAILS_NOTE = "For when a leader asks what the plugin is doing. No key is in it."


def details_words(core, now: Optional[datetime] = None) -> str:
    """The plugin's state as a few plain lines for the clipboard: the versions, how the link stands, what went out
    this session and the last trouble. Never the key, the token or the install's id."""
    s = core.sender
    c = s.counts
    _, link, _ = link_state(core)
    feed = core.feed if isinstance(core.feed, dict) else {}
    game = core.game if isinstance(core.game, dict) else {}
    kept = s.spooled()
    lines = [
        f"Exiles plugin {core.version}",
        f"EDMC {core.edmc_version or 'unknown'}, {platform.system()} {platform.release()}".rstrip(),
        "Game " + (f"{game.get('version')} ({str(game.get('build') or '').strip()})" if game.get("version") else "not seen yet")
        + ("" if game.get("odyssey") is None else ", Odyssey" if game.get("odyssey") else ", Horizons"),
        f"CMDR {core.cmdr}" if core.cmdr else "CMDR not seen yet",
        f"Link: {link}",
        "In the game: " + ("yes" if core.in_game() else "no"),
        f"Sent since EDMC started: {c.get('events', 0):,} journal lines in {c.get('batches', 0):,} batches, {c.get('skipped', 0):,} already on file, "
        f"{c.get('status', 0):,} status, {c.get('state', 0):,} state, {c.get('heartbeats', 0):,} heartbeats",
        f"Kept on this PC: {kept} batch{'' if kept == 1 else 'es'}. Set aside: {c.get('refused', 0)}. Errors: {c.get('errors', 0)}",
        f"Last trouble: {s.last_error or 'none'}",
        "Site: " + (core.feed_error if core.feed_error else f"read {ago(feed.get('at'), now)}" if feed.get("at") else "not read yet"),
    ]
    pcs = pcs_rows(feed)
    if pcs is not None:
        lines.append(f"Paired PCs: {len(pcs)}")
    lines.append(f"Copied {utc_now_iso() if now is None else now.strftime('%Y-%m-%dT%H:%M:%SZ')}")
    return "\n".join(lines)
