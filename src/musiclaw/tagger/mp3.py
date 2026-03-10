from __future__ import annotations

from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, ID3, ID3NoHeaderError, TXXX


EASY_MAP = {
    "title": "title",
    "artist": "artist",
    "album": "album",
    "albumartist": "albumartist",
    "tracknumber": "tracknumber",
    "discnumber": "discnumber",
    "date": "date",
    "genre": "genre",
}


def write_mp3_tags(path, tags: dict[str, str], cover_data: bytes | None = None) -> None:
    try:
        easy = EasyID3(path)
    except Exception:
        easy = EasyID3()
    for key, value in tags.items():
        easy_key = EASY_MAP.get(key)
        if easy_key:
            easy[easy_key] = [str(value)]
    easy.save(path)

    try:
        id3 = ID3(path)
    except ID3NoHeaderError:
        id3 = ID3()
    for key, value in tags.items():
        if key in EASY_MAP:
            continue
        id3.add(TXXX(encoding=3, desc=key, text=str(value)))
    if cover_data:
        id3.delall("APIC")
        id3.add(APIC(encoding=3, mime="image/jpeg", type=3, desc="Cover", data=cover_data))
    id3.save(path)
