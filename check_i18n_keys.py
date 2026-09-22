"""Static i18n key check: every T(lang, 'key') used in the workbench must be
defined in i18n.js (en pack) or ui-strings.js (12-language chrome), else the
UI would render the raw key. Also report per-language pack coverage."""
import re
import pathlib

src = pathlib.Path("frontend/src")

used = set()
for f in list(src.glob("pages/*.jsx")) + [src / "main.jsx"]:
    used |= set(re.findall(r"T\(\w+,\s*'([^']+)'", f.read_text(encoding="utf8")))

i18n = (src / "i18n.js").read_text(encoding="utf8")
uis = (src / "ui-strings.js").read_text(encoding="utf8")
defined = set(re.findall(r"(\w+):\s*'", i18n)) | set(re.findall(r"(\w+):\s*'", uis))

missing = sorted(used - defined)
print(f"used keys: {len(used)}")
print("missing:", missing if missing else "NONE")

# English coverage: every used key must exist in the `const en = {...}` pack
# or in ui-strings.js (the fallback target for every language).
en_block = re.search(r"const en = \{(.*?)\n\};", i18n, re.S)
en_defined = set(re.findall(r"(\w+):", en_block.group(1))) if en_block else set()
en_defined |= set(re.findall(r"(\w+):\s*'", uis))
en_missing = sorted(used - en_defined)
print("missing in EN fallback:", en_missing if en_missing else "NONE")

# per-pack coverage inside i18n.js (rough): count keys per lang block
blocks = re.findall(r"\n  ([a-z]{2,3}): \{(.*?)\n  \},", i18n, re.S)
counts = {name: len(set(re.findall(r"(\w+):", body))) for name, body in blocks}
print("i18n pack key counts:", counts)
en_keys = counts.get("en", 0)
gaps = {k: v for k, v in counts.items() if k != "en" and v < en_keys}
print("packs with fewer keys than en:", gaps if gaps else "none (English fallback covers)")
