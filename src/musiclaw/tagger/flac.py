from __future__ import annotations

from mutagen.flac import FLAC, Picture


def write_flac_tags(path, tags: dict[str, str], cover_data: bytes | None = None) -> None:
    audio = FLAC(path)
    for key, value in tags.items():
        audio[key] = [str(value)]
    if cover_data:
        image = Picture()
        image.type = 3
        image.mime = "image/jpeg"
        image.data = cover_data
        audio.clear_pictures()
        audio.add_picture(image)
    audio.save()
