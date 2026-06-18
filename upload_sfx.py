"""
One-off: upload the Impact_Sounds library to Cloudinary (unsigned, via the
existing poweroflillithvid preset — no API secret needed) and write a manifest.

Source: C:/Users/User/OneDrive/Desktop/Impact_Sounds/<category>/<file>.mp3
Target: Cloudinary  audio/sfx/<slug>/<asset_id>   (video resource type; mp3)
Output: C:/tmp_diag/sfx_manifest.json  -> [{category, asset_id, filename, url}]
"""
import json, re, sys
from pathlib import Path
import requests

SRC = Path("C:/Users/User/OneDrive/Desktop/Impact_Sounds")
CLOUD = "poweroflillith"
PRESET = "poweroflillithvid"
ENDPOINT = f"https://api.cloudinary.com/v1_1/{CLOUD}/video/upload"

# folder name -> clean category slug
CAT = {
    "first0,00 sekond": "first0",
    "hook": "hook",
    "impact": "impact",
    "pop": "pop",
    "transition": "transition",
}

OUT = Path("C:/tmp_diag"); OUT.mkdir(exist_ok=True)


def asset_id(filename: str) -> str:
    base = re.sub(r"\.wav\.mp3$|\.mp3$", "", filename, flags=re.IGNORECASE)
    base = re.sub(r"[^A-Za-z0-9]+", "_", base).strip("_").lower()
    return base


def main():
    manifest = []
    seen = set()
    for folder, slug in CAT.items():
        d = SRC / folder
        if not d.exists():
            print(f"!! missing folder: {d}")
            continue
        for f in sorted(d.glob("*.mp3")):
            aid = asset_id(f.name)
            key = (slug, aid)
            if key in seen:
                print(f"  dedupe skip: {slug}/{f.name}")
                continue
            seen.add(key)
            with open(f, "rb") as fh:
                data = {
                    "upload_preset": PRESET,
                    "folder": f"audio/sfx/{slug}",
                    "public_id": aid,
                }
                try:
                    r = requests.post(ENDPOINT, data=data, files={"file": fh}, timeout=120)
                except Exception as exc:
                    print(f"  ERROR {slug}/{f.name}: {exc}")
                    continue
            if r.status_code != 200:
                # retry without public_id (some unsigned presets forbid it)
                with open(f, "rb") as fh:
                    r = requests.post(ENDPOINT, data={"upload_preset": PRESET,
                                      "folder": f"audio/sfx/{slug}"},
                                      files={"file": fh}, timeout=120)
            if r.status_code != 200:
                print(f"  FAIL {slug}/{f.name}: {r.status_code} {r.text[:200]}")
                continue
            url = r.json().get("secure_url")
            manifest.append({"category": slug, "asset_id": aid,
                             "filename": f.name, "url": url})
            print(f"  OK  {slug}/{aid} -> {url}")

    (OUT / "sfx_manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\n{len(manifest)} assets uploaded. Manifest: {OUT/'sfx_manifest.json'}")


if __name__ == "__main__":
    main()
