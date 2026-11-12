# The Exiles plugin for EDMC

The Exiles' own plugin for [Elite Dangerous Market Connector](https://github.com/EDCD/EDMarketConnector) (EDMC).
Run it while you play and the squad's [ops site](https://ops.theexiles.gg) knows what you did each tick, with
nothing for you to fill in. It also puts the squad's orders, its wars and who is in the game right inside EDMC.

It is for members of [The Exiles](https://theexiles.gg). It only talks to our own site, so it does nothing for
anyone else.

## What you get

- **A strip in EDMC's main window.** A dot that says whether you are connected, the squad's war with its day
  and score (the one in your system first), a nudge when that system needs an update, a mark when new orders are
  out, and a word when your ship or suit needs one: low fuel, in danger, interdicted, wanted.
- **Orders.** The squad's current orders, with a card for every war: the day, the score and what is at stake.
- **My tick.** What you have handed in since the last tick, sorted by the faction it was for, ours on top, with
  your squad XP beside it. Last tick shows the one before and the XP it paid. Copy puts the tick on the
  clipboard as text for Discord.
- **Squad.** Who is in the game right now, where, and what they are doing. Click a system's name to copy it for
  the galaxy map.
- **It counts for squad XP.** With the plugin running, your work is scored from your journal, whether or not you
  also run BGS-Tally.
- **Mining spots that record themselves.** Every spot you mine in the Rhino shows up in your Mining section on
  the ops site.

## What you need

- Elite Dangerous on PC with EDMC 6. The plugin is built and tested on Windows with EDMC 6.1.2. Linux is
  untested.
- To be a full member of The Exiles, with a sign in on the ops site.

It runs beside [BGS-Tally](https://github.com/aussig/BGS-Tally) and never touches it. Keep Tally if you like its
windows. The squad's database never counts the same thing twice.

## Install

1. On this page press the green **Code** button, then **Download ZIP**.
2. In EDMC open **File**, **Settings**, **Plugins** and press **Open Plugins Folder**.
3. Open the zip. Inside its one folder is a folder named `EDMC_Exiles`. Drag that folder into the plugins folder.
   It has to keep that exact name.
4. Restart EDMC. The Exiles strip shows in EDMC's main window and says **Not paired yet**.

When it is in the right place, this file exists:
`%LOCALAPPDATA%\EDMarketConnector\plugins\EDMC_Exiles\load.py`

## Pair it

Once on each PC. There is no key to copy.

1. Click **Pair** on the strip. It shows a code like `ABC-DEF`, good for ten minutes.
2. Sign in on the [ops site](https://ops.theexiles.gg), open **Cmdr**, then **Settings**, and type the code into
   the **Exiles plugin** card.
3. A few seconds later the strip says **Connected**.

Only ever type a code that your own EDMC is showing you. A code somebody else hands you would pair their PC as
you.

Playing on two PCs? Install and pair it on both. Each PC gets its own key, and the Settings tab lists them.

## What it sends

There are no switches. Once paired, the plugin sends:

- **Your journal**, as the game writes it: jumps, dockings, missions, bounties, bonds, trades, scans and the
  rest.
- **Your status** while the game runs: where you are, your fuel, and whether you are docked, landed, in
  supercruise or on foot.
- **What EDMC knows about your commander**: cargo, materials, ship locker, ship and suit loadout, ranks,
  reputation and engineers, credits, Powerplay and your plotted route.
- **A heartbeat**: once a minute while the game runs and every five minutes while only EDMC is open, so the
  squad knows whether you are in the game. It carries your commander's name, where you are, and the versions of
  the game, EDMC and the plugin.

What never leaves your PC: **chat**, both what you send and what you receive, your **friends list**, and the
game's music cues. Nothing at all is gathered or sent before you pair.

It goes over HTTPS to the squad's own database behind the ops site. Signed in members can see who is in the
game, where they are and what they are doing, and the work you hand in counts on the squad's boards. Leaders can
look at any member's activity.

Pairing gives this PC a key of its own. It is kept in EDMC's settings, where BGS-Tally keeps its key too. If
you ever share EDMC's `config.toml` file, your key is in it, so do not post that file anywhere.

## Stop, update, remove

- **Stop for now:** close EDMC. The plugin sends nothing while EDMC is closed.
- **Update:** download the zip again, close EDMC, copy the new `EDMC_Exiles` folder over the old one and start
  EDMC. You stay paired.
- **Remove:** click **Settings** on the strip, press **Remove** beside **This PC**, then **Really remove**.
  That ends this PC's key. Then close EDMC and delete the `EDMC_Exiles` folder.

## If something is off

- **Pair again** on the strip: this PC's key is no longer accepted. Click **Pair** and type the new code.
- **Offline:** the site could not be reached. Nothing is lost. Your journal lines are kept on your PC and sent
  when the connection is back.
- **No strip in EDMC:** check that the folder is named `EDMC_Exiles`, sits directly in the plugins folder and
  has `load.py` in it, then restart EDMC.
- **Anything else:** click **Settings** on the strip, press **Copy details** and paste it to a leader on Discord.
  No key is in it.

## What is in here

| File | What it does |
|---|---|
| `EDMC_Exiles/load.py` | EDMC's entry points |
| `EDMC_Exiles/exiles/core.py` | the hooks, the heartbeat, pairing and the read of the site's feed |
| `EDMC_Exiles/exiles/sender.py` | one worker thread that sends everything: batches, retries, and a spool on disk while offline |
| `EDMC_Exiles/exiles/pairing.py` | pairing by code |
| `EDMC_Exiles/exiles/status.py` | which Status entries are worth sending |
| `EDMC_Exiles/exiles/state.py` | which parts of EDMC's commander state are sent, and when |
| `EDMC_Exiles/exiles/words.py` | every word the plugin shows |
| `EDMC_Exiles/exiles/ui.py` | the strip and the Exiles window |
| `EDMC_Exiles/version.txt` | the version, which the Settings tab shows too |

The plugin talks to two addresses, both ours: `ops.theexiles.gg` for pairing, orders, wars, your tick and the
squad in the game, and `api.exdb.theexiles.gg`, where your journal goes. EDMC's own window never waits on the
network.

## License

MIT. See [LICENSE](LICENSE).
