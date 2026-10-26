"""
jarvis_contacts.py - Jarvis v3 contacts book.

Contacts live in ~/.openjarvis/contacts.json:

    {
      "Mom": {"phone": "+1 555 010 2030", "email": "mom@example.com"},
      "Pallavi": {"phone": "+91 98...", "email": "", "channel": "mail"}
    }

Lookups are case-insensitive and allow partial matches ("mom" finds
"Mom", "pal" finds "Pallavi"). The file is re-read on every lookup, so
edits take effect immediately and tests can use a temporary HOME.
An optional "channel": "mail" marks a contact as preferring the Mail app;
v3 email still defaults to a prefilled Gmail compose (never auto-send).
"""

import json
import os

CONTACTS_FILE = "~/.openjarvis/contacts.json"


def contacts_path():
    return os.path.expanduser(CONTACTS_FILE)


def load_contacts():
    """Return {name: {"phone":..., "email":...}}; {} on any problem."""
    try:
        path = contacts_path()
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                out = {}
                for name, entry in data.items():
                    if isinstance(entry, dict):
                        out[str(name)] = {
                            "phone": str(entry.get("phone", "") or ""),
                            "email": str(entry.get("email", "") or ""),
                            "channel": str(entry.get("channel", "") or ""),
                        }
                    elif isinstance(entry, str):
                        # Allow shorthand: "Name": "+1 555..." (a phone).
                        out[str(name)] = {"phone": entry, "email": "", "channel": ""}
                return out
    except Exception:
        pass
    return {}


def resolve(name):
    """
    Find a contact by name (case-insensitive, partial allowed).
    Returns (canonical_name, entry_dict) or None.
    """
    if not name:
        return None
    target = str(name).strip().lower()
    if not target:
        return None
    contacts = load_contacts()
    # Exact match first.
    for cname, entry in contacts.items():
        if cname.lower() == target:
            return cname, entry
    # Partial match: query inside the name, or name inside the query
    # (spoken names sometimes carry extra words).
    for cname, entry in contacts.items():
        low = cname.lower()
        if target in low or low in target:
            return cname, entry
    return None


def digits(phone):
    """Just the digits of a phone string (for wa.me / tel style URLs)."""
    return "".join(ch for ch in str(phone or "") if ch.isdigit())
