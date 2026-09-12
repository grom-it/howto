#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
teams-hotkey.py - Sendet eine Teams-for-Linux-Tastenkombination an das
laufende Teams-Fenster, auch wenn dieses gerade NICHT den Fokus hat.

Getestet für: GNOME / Mutter unter Wayland (Ubuntu, GNOME-Standard-Session).

Funktionsweise:
  1. Über die GNOME-Shell-Erweiterung "Window Calls" (D-Bus) wird das
     Teams-Fenster (wm_class enthält "teams_for_linux") gefunden.
  2. Das Fenster wird per D-Bus "Activate" in den Vordergrund/Fokus geholt
     (funktioniert auch bei minimierten/verdeckten Fenstern).
  3. Die Tastenkombination wird per `ydotool` gesendet (arbeitet auf
     Kernel-uinput-Ebene, ist damit Wayland-Compositor-unabhängig und
     sendet an das gerade fokussierte Fenster).
  4. Optional wird danach das vorher aktive Fenster wieder aktiviert.

Voraussetzungen:
  - GNOME-Shell-Erweiterung "Window Calls" (ickyicky) installiert + aktiv
    https://extensions.gnome.org/extension/4724/window-calls/
  - ydotool + laufender ydotoold-Daemon mit Zugriff auf /dev/uinput
    Muss einmal aktiviert werden:
        systemctl enable --user --now ydotool

Aufruf:
  teams-hotkey.py raise       -> Ctrl+Shift+K  (Hand heben)
  teams-hotkey.py mute        -> Ctrl+Shift+M  (Mikro an/aus)
  teams-hotkey.py video       -> Ctrl+Shift+O  (Video an/aus)
  teams-hotkey.py leave       -> Ctrl+Shift+H  (Anruf beenden)
"""

import ast
import json
import subprocess
import sys
import time

# --- Konfiguration -----------------------------------------------------

# Teilstring, der in wm_class des Teams-Fensters vorkommt.
# Bei Flatpak-Installation (com.github.IsmaelMartinez.teams_for_linux)
# taucht i.d.R. "teams_for_linux" oder "teams-for-linux" als wm_class auf.
APP_ID_HINT = "teams_for_linux"

# Sekunden Wartezeit nach dem Aktivieren des Fensters, bevor die Taste
# gesendet wird (Teams braucht kurz, bis es den Fokus wirklich verarbeitet).
ACTIVATE_DELAY = 0.25
POST_KEY_DELAY = 0.10

# Ob nach dem Senden der Taste zum vorher aktiven Fenster zurückgeschaltet
# werden soll. Für die Basis-Erweiterung "Window Calls" gibt es keinen
# zuverlässigen "wer war vorher fokussiert"-Aufruf, daher hier bewusst
# deaktiviert (siehe Erklärung im Chat). Auf True setzen, wenn stattdessen
# "Window Calls Extended" (hseliger) installiert ist, das FocusPID() bietet.
RESTORE_PREVIOUS_FOCUS = False

# Linux-Keycodes aus input-event-codes.h (für ydotool)
KEY_LEFTCTRL = 29
KEY_LEFTSHIFT = 42
KEY_K = 37
KEY_M = 50
KEY_O = 24
KEY_H = 35

SHORTCUTS = {
    "raise": KEY_K,
    "mute": KEY_M,
    "video": KEY_O,
    "leave": KEY_H,
}

DBUS_DEST = "org.gnome.Shell"
DBUS_PATH = "/org/gnome/Shell/Extensions/Windows"
DBUS_IFACE = "org.gnome.Shell.Extensions.Windows"


# --- Hilfsfunktionen -----------------------------------------------------

def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        raise RuntimeError(
            f"Befehl fehlgeschlagen: {' '.join(cmd)}\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def gdbus_call(method: str, *args: str) -> str:
    cmd = [
        "gdbus", "call", "--session",
        "--dest", DBUS_DEST,
        "--object-path", DBUS_PATH,
        "--method", f"{DBUS_IFACE}.{method}",
        *args,
    ]
    return run(cmd)


def parse_gdbus_string_reply(raw: str) -> str:
    """gdbus gibt z.B. ('[{"id": 1, ...}]',) zurueck – das ist gueltige
    Python-Tupel-Syntax, daher per literal_eval sicher entpacken."""
    try:
        value = ast.literal_eval(raw)
        return value[0]
    except (ValueError, SyntaxError, IndexError):
        # Fallback: aeussere Klammern/Anfuehrungszeichen grob abschneiden
        return raw.strip()[2:-3]


def list_windows() -> list[dict]:
    raw = gdbus_call("List")
    json_str = parse_gdbus_string_reply(raw)
    return json.loads(json_str)


def find_teams_window(windows: list[dict]) -> dict | None:
    for w in windows:
        wm_class = (w.get("wm_class") or "").lower()
        wm_class_instance = (w.get("wm_class_instance") or "").lower()
        if APP_ID_HINT in wm_class or APP_ID_HINT in wm_class_instance:
            return w
    return None


def activate_window(window_id: int) -> None:
    gdbus_call("Activate", str(window_id))


def send_key_combo(keycode: int) -> None:
    # Reihenfolge: Ctrl runter, Shift runter, Taste runter, Taste hoch,
    # Shift hoch, Ctrl hoch.
    args = [
        f"{KEY_LEFTCTRL}:1", f"{KEY_LEFTSHIFT}:1", f"{keycode}:1",
        f"{keycode}:0", f"{KEY_LEFTSHIFT}:0", f"{KEY_LEFTCTRL}:0",
    ]
    run(["ydotool", "key", *args])


# --- Hauptprogramm -------------------------------------------------------

def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in SHORTCUTS:
        options = "|".join(SHORTCUTS)
        print(f"Nutzung: {sys.argv[0]} {{{options}}}", file=sys.stderr)
        return 1

    action = sys.argv[1]
    keycode = SHORTCUTS[action]

    try:
        windows = list_windows()
    except RuntimeError as e:
        print(
            "Fehler: Konnte die GNOME-Shell-Erweiterung 'Window Calls' "
            "nicht erreichen. Ist sie installiert und aktiviert?\n"
            f"Details: {e}",
            file=sys.stderr,
        )
        return 2

    teams_window = find_teams_window(windows)
    if teams_window is None:
        print(
            f"Kein Fenster mit wm_class ~ '{APP_ID_HINT}' gefunden. "
            "Läuft Teams for Linux gerade?",
            file=sys.stderr,
        )
        return 3

    teams_id = teams_window["id"]

    try:
        activate_window(teams_id)
        time.sleep(ACTIVATE_DELAY)
        send_key_combo(keycode)
        time.sleep(POST_KEY_DELAY)
    except RuntimeError as e:
        print(f"Fehler beim Senden der Tastenkombination: {e}", file=sys.stderr)
        return 4

    if RESTORE_PREVIOUS_FOCUS:
        # Platzhalter für Erweiterung mit FocusPID()/FocusClass() – siehe
        # Kommentar oben.
        pass

    return 0


if __name__ == "__main__":
    sys.exit(main())

