"""
Compile locale/*/LC_MESSAGES/django.po -> django.mo in pure Python.

Same result as Django's `compilemessages`, but needs no GNU gettext install (handy on Windows).
Usage: python manage.py compile_po
"""
import ast
import struct
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError


def parse_po(path):
    """Return {msgid: msgstr}; plural entries use the gettext '\\0'-joined form."""
    entries, cur, field = [], None, None

    def flush():
        if cur and "msgid" in cur:
            entries.append(cur)

    for lineno, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if line.startswith("msgid "):
                flush()
                cur, field = {"msgid": ast.literal_eval(line[6:])}, "msgid"
            elif line.startswith("msgid_plural "):
                cur["msgid_plural"], field = ast.literal_eval(line[13:]), "msgid_plural"
            elif line.startswith("msgstr["):
                idx, value = line[7:].split("]", 1)
                field = f"msgstr[{int(idx)}]"
                cur[field] = ast.literal_eval(value.strip())
            elif line.startswith("msgstr "):
                cur["msgstr"], field = ast.literal_eval(line[7:]), "msgstr"
            elif line.startswith('"'):
                cur[field] += ast.literal_eval(line)
            else:
                raise ValueError("unexpected line")
        except (ValueError, SyntaxError, TypeError, KeyError) as exc:
            raise CommandError(f"{path}:{lineno}: cannot parse ({exc}): {raw}")
    flush()

    catalog = {}
    for e in entries:
        if "msgid_plural" in e:
            forms = [e[k] for k in sorted(k for k in e if k.startswith("msgstr["))]
            if all(forms):
                catalog[e["msgid"] + "\0" + e["msgid_plural"]] = "\0".join(forms)
        elif e.get("msgstr"):
            catalog[e["msgid"]] = e["msgstr"]
    return catalog


def write_mo(catalog, path):
    keys = sorted(catalog)
    ids = [k.encode("utf-8") for k in keys]
    strs = [catalog[k].encode("utf-8") for k in keys]
    n = len(keys)
    header_size = 7 * 4
    ids_table = header_size
    strs_table = ids_table + n * 8
    data_start = strs_table + n * 8

    offsets, data = [], b""
    for s in ids:
        offsets.append((len(s), data_start + len(data)))
        data += s + b"\0"
    str_offsets = []
    for s in strs:
        str_offsets.append((len(s), data_start + len(data)))
        data += s + b"\0"

    out = struct.pack("<7I", 0x950412DE, 0, n, ids_table, strs_table, 0, 0)
    out += b"".join(struct.pack("<2I", *o) for o in offsets)
    out += b"".join(struct.pack("<2I", *o) for o in str_offsets)
    path.write_bytes(out + data)


class Command(BaseCommand):
    help = "Compile .po translation files to .mo without GNU gettext."

    def handle(self, *args, **options):
        found = False
        for locale_dir in settings.LOCALE_PATHS:
            for po in sorted(Path(locale_dir).glob("*/LC_MESSAGES/*.po")):
                found = True
                catalog = parse_po(po)
                write_mo(catalog, po.with_suffix(".mo"))
                self.stdout.write(f"{po} -> {po.with_suffix('.mo').name} ({len(catalog) - 1} strings)")
        if not found:
            raise CommandError("No .po files found in LOCALE_PATHS.")
