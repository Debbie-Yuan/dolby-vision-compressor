#!/usr/bin/env python3
from __future__ import annotations

import argparse
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable


CONTAINERS = {
    b"moov",
    b"trak",
    b"mdia",
    b"minf",
    b"stbl",
    b"edts",
    b"dinf",
    b"udta",
    b"meta",
    b"ilst",
    b"tref",
    b"tapt",
}


@dataclass
class Box:
    typ: bytes
    start: int
    size: int
    header: int
    data: bytes
    children: list["Box"] = field(default_factory=list)
    meta_full: bool = False

    def payload_start(self) -> int:
        return self.header + (4 if self.meta_full else 0)

    def find_children(self, typ: bytes) -> list["Box"]:
        return [child for child in self.children if child.typ == typ]

    def first_child(self, typ: bytes) -> "Box":
        for child in self.children:
            if child.typ == typ:
                return child
        raise KeyError(f"missing child {typ!r} in {self.typ!r}")

    def rebuilt(self, children: list["Box"] | None = None, data: bytes | None = None) -> "Box":
        return Box(
            typ=self.typ,
            start=0,
            size=0,
            header=self.header,
            data=self.data if data is None else data,
            children=self.children if children is None else children,
            meta_full=self.meta_full,
        )

    def render(self) -> bytes:
        if self.children:
            inner = b"".join(child.render() for child in self.children)
            prefix = self.data[: self.payload_start()]
            blob = prefix + inner
        else:
            blob = self.data
        size = len(blob)
        if self.header == 16:
            blob = (1).to_bytes(4, "big") + self.typ + size.to_bytes(8, "big") + blob[16:]
        else:
            blob = size.to_bytes(4, "big") + self.typ + blob[8:]
        return blob


def parse_boxes(data: bytes, start: int, end: int) -> list[Box]:
    boxes: list[Box] = []
    off = start
    while off + 8 <= end:
        size = int.from_bytes(data[off : off + 4], "big")
        typ = data[off + 4 : off + 8]
        header = 8
        if size == 1:
            size = int.from_bytes(data[off + 8 : off + 16], "big")
            header = 16
        elif size == 0:
            size = end - off
        if size < 8 or off + size > end:
            break
        raw = data[off : off + size]
        meta_full = typ == b"meta"
        payload_start = header + (4 if meta_full else 0)
        children = parse_boxes(data, off + payload_start, off + size) if typ in CONTAINERS else []
        boxes.append(Box(typ=typ, start=off, size=size, header=header, data=raw, children=children, meta_full=meta_full))
        off += size
    return boxes


def load_top(path: Path) -> list[Box]:
    return parse_boxes(path.read_bytes(), 0, path.stat().st_size)


def patch_stco(box: Box, delta: int) -> Box:
    payload_start = box.payload_start()
    raw = bytearray(box.data)
    count = int.from_bytes(raw[payload_start + 4 : payload_start + 8], "big")
    pos = payload_start + 8
    for _ in range(count):
        val = int.from_bytes(raw[pos : pos + 4], "big")
        raw[pos : pos + 4] = (val + delta).to_bytes(4, "big")
        pos += 4
    return box.rebuilt(data=bytes(raw), children=[])


def scale_timing_table(box: Box, factor: int) -> Box:
    payload_start = box.payload_start()
    raw = bytearray(box.data)
    count = int.from_bytes(raw[payload_start + 4 : payload_start + 8], "big")
    pos = payload_start + 8
    for _ in range(count):
        pos += 4  # sample_count
        val = int.from_bytes(raw[pos : pos + 4], "big", signed=False)
        if val % factor:
            raise RuntimeError(f"{box.typ.decode()} entry {val} not divisible by {factor}")
        raw[pos : pos + 4] = (val // factor).to_bytes(4, "big")
        pos += 4
    return box.rebuilt(data=bytes(raw), children=[])


def patch_mdhd_timescale_duration(box: Box, timescale: int, duration: int) -> Box:
    payload_start = box.payload_start()
    raw = bytearray(box.data)
    version = raw[payload_start]
    if version != 0:
        raise RuntimeError("only mdhd version 0 supported")
    raw[payload_start + 12 : payload_start + 16] = timescale.to_bytes(4, "big")
    raw[payload_start + 16 : payload_start + 20] = duration.to_bytes(4, "big")
    return box.rebuilt(data=bytes(raw), children=[])


def patch_tkhd_dimensions(box: Box, width: int, height: int) -> Box:
    payload_start = box.payload_start()
    raw = bytearray(box.data)
    version = raw[payload_start]
    if version != 0:
        raise RuntimeError("only tkhd version 0 supported")
    raw[payload_start + 76 : payload_start + 80] = (width << 16).to_bytes(4, "big")
    raw[payload_start + 80 : payload_start + 84] = (height << 16).to_bytes(4, "big")
    return box.rebuilt(data=bytes(raw), children=[])


def patch_tapt_dimension_box(box: Box, width: int, height: int) -> Box:
    payload_start = box.payload_start()
    raw = bytearray(box.data)
    raw[payload_start + 4 : payload_start + 8] = (width << 16).to_bytes(4, "big")
    raw[payload_start + 8 : payload_start + 12] = (height << 16).to_bytes(4, "big")
    return box.rebuilt(data=bytes(raw), children=[])


def patch_tapt(box: Box, width: int, height: int) -> Box:
    children = []
    for child in box.children:
        if child.typ in {b"clef", b"prof", b"enof"}:
            children.append(patch_tapt_dimension_box(child, width, height))
        else:
            children.append(clone(child))
    return box.rebuilt(children=children)


def sample_entry_dimensions(entry: bytes) -> tuple[int, int]:
    return int.from_bytes(entry[32:34], "big"), int.from_bytes(entry[34:36], "big")


def clone(box: Box) -> Box:
    if box.typ == b"stsd":
        return box.rebuilt(children=[], data=box.data)
    return box.rebuilt(children=[clone(child) for child in box.children], data=box.data)


def find_tracks(moov: Box) -> list[Box]:
    return moov.find_children(b"trak")


def visual_entry_hybrid(orig_entry: bytes, reb_entry: bytes) -> bytes:
    width, height = sample_entry_dimensions(reb_entry)
    hvc_orig = orig_entry.find(b"hvcC")
    dvv_orig = orig_entry.find(b"dvvC")
    colr_orig = orig_entry.find(b"colr")
    hvc_reb = reb_entry.find(b"hvcC")
    dvv_reb = reb_entry.find(b"dvvC")
    colr_reb = reb_entry.find(b"colr")
    if min(hvc_orig, dvv_orig, colr_orig, hvc_reb, dvv_reb, colr_reb) < 0:
        raise RuntimeError("failed to locate visual sample-entry child boxes")
    orig_hvc_size = int.from_bytes(orig_entry[hvc_orig - 4 : hvc_orig], "big")
    orig_dvv_size = int.from_bytes(orig_entry[dvv_orig - 4 : dvv_orig], "big")
    orig_colr_size = int.from_bytes(orig_entry[colr_orig - 4 : colr_orig], "big")
    reb_hvc_size = int.from_bytes(reb_entry[hvc_reb - 4 : hvc_reb], "big")
    reb_dvv_size = int.from_bytes(reb_entry[dvv_reb - 4 : dvv_reb], "big")

    prefix = bytearray(orig_entry[: hvc_orig - 4])
    prefix[32:34] = width.to_bytes(2, "big")
    prefix[34:36] = height.to_bytes(2, "big")
    reb_hvc = reb_entry[hvc_reb - 4 : hvc_reb - 4 + reb_hvc_size]
    reb_dvv = reb_entry[dvv_reb - 4 : dvv_reb - 4 + reb_dvv_size]
    orig_colr = orig_entry[colr_orig - 4 : colr_orig - 4 + orig_colr_size]
    suffix = orig_entry[colr_orig - 4 + orig_colr_size :]

    hybrid = bytes(prefix) + reb_hvc + reb_dvv + orig_colr + suffix
    return len(hybrid).to_bytes(4, "big") + hybrid[4:]


def visual_entry_hybrid_no_dv(orig_entry: bytes, reb_entry: bytes) -> bytes:
    width, height = sample_entry_dimensions(reb_entry)
    hvc_orig = orig_entry.find(b"hvcC")
    colr_orig = orig_entry.find(b"colr")
    hvc_reb = reb_entry.find(b"hvcC")
    colr_reb = reb_entry.find(b"colr")
    if min(hvc_orig, colr_orig, hvc_reb, colr_reb) < 0:
        raise RuntimeError("failed to locate HLG visual sample-entry child boxes")
    reb_hvc_size = int.from_bytes(reb_entry[hvc_reb - 4 : hvc_reb], "big")
    prefix = bytearray(orig_entry[: hvc_orig - 4])
    prefix[32:34] = width.to_bytes(2, "big")
    prefix[34:36] = height.to_bytes(2, "big")
    reb_hvc = reb_entry[hvc_reb - 4 : hvc_reb - 4 + reb_hvc_size]
    # Keep the original color box and Apple suffix only when no DV box is present in rebuilt entry.
    orig_colr = orig_entry[colr_orig - 4 :]
    hybrid = bytes(prefix) + reb_hvc + orig_colr
    return len(hybrid).to_bytes(4, "big") + hybrid[4:]


def swap_stsd_entry(stsd: Box, entry: bytes) -> Box:
    payload_start = stsd.payload_start()
    prefix = bytearray(stsd.data[: payload_start + 8])
    prefix[payload_start + 4 : payload_start + 8] = (1).to_bytes(4, "big")
    raw = bytes(prefix) + entry
    return stsd.rebuilt(data=raw, children=[])


def replace_boxes_in_order(orig_stbl: Box, replacements: dict[bytes, list[Box]]) -> Box:
    used_counts: dict[bytes, int] = {}
    new_children: list[Box] = []
    for child in orig_stbl.children:
        typ = child.typ
        if typ in replacements:
            idx = used_counts.get(typ, 0)
            pool = replacements[typ]
            if idx < len(pool):
                new_children.append(pool[idx])
                used_counts[typ] = idx + 1
                continue
        new_children.append(clone(child))
    for typ, pool in replacements.items():
        idx = used_counts.get(typ, 0)
        if idx < len(pool):
            new_children.extend(pool[idx:])
    return orig_stbl.rebuilt(children=new_children)


def get_stbl(track: Box) -> Box:
    return track.first_child(b"mdia").first_child(b"minf").first_child(b"stbl")


def set_stbl(track: Box, new_stbl: Box) -> Box:
    mdia = clone(track.first_child(b"mdia"))
    minf = clone(mdia.first_child(b"minf"))
    minf_children = [new_stbl if child.typ == b"stbl" else clone(child) for child in minf.children]
    minf = minf.rebuilt(children=minf_children)
    mdia_children = [minf if child.typ == b"minf" else clone(child) for child in mdia.children]
    mdia = mdia.rebuilt(children=mdia_children)
    trak_children = [mdia if child.typ == b"mdia" else clone(child) for child in track.children]
    return track.rebuilt(children=trak_children)


def replace_mdia_boxes(track: Box, replacements: dict[bytes, Box]) -> Box:
    mdia = clone(track.first_child(b"mdia"))
    mdia_children = [clone(replacements.get(child.typ, child)) for child in mdia.children]
    mdia = mdia.rebuilt(children=mdia_children)
    trak_children = [mdia if child.typ == b"mdia" else clone(child) for child in track.children]
    return track.rebuilt(children=trak_children)


def build(orig_path: Path, rebuilt_path: Path, out_path: Path) -> None:
    orig_top = load_top(orig_path)
    reb_top = load_top(rebuilt_path)

    orig_ftyp = next(box for box in orig_top if box.typ == b"ftyp")
    orig_wide = next(box for box in orig_top if box.typ == b"wide")
    orig_moov = next(box for box in orig_top if box.typ == b"moov")
    reb_mdat = next(box for box in reb_top if box.typ == b"mdat")
    reb_moov = next(box for box in reb_top if box.typ == b"moov")

    new_payload_start = len(orig_ftyp.render()) + len(orig_wide.render()) + 8
    reb_payload_start = reb_mdat.start + reb_mdat.header
    delta = new_payload_start - reb_payload_start

    orig_tracks = find_tracks(orig_moov)
    reb_tracks = find_tracks(reb_moov)
    new_tracks: list[Box] = []

    # Track 1: original track structure + rebuilt timing/sample tables.
    orig_stbl = get_stbl(orig_tracks[0])
    reb_stbl = get_stbl(reb_tracks[0])
    orig_entry = orig_stbl.first_child(b"stsd").data[16:]
    reb_entry = reb_stbl.first_child(b"stsd").data[16:]
    reb_has_dvv = b"dvvC" in reb_entry or b"dvcC" in reb_entry
    if reb_has_dvv:
        track1_entry = visual_entry_hybrid(orig_entry, reb_entry)
    else:
        track1_entry = visual_entry_hybrid_no_dv(orig_entry, reb_entry)
    track1_stsd = swap_stsd_entry(orig_stbl.first_child(b"stsd"), track1_entry)
    reb_width, reb_height = sample_entry_dimensions(reb_entry)
    scaled_stts = scale_timing_table(reb_stbl.first_child(b"stts"), 10)
    scaled_ctts = scale_timing_table(reb_stbl.first_child(b"ctts"), 10)
    track1_repls = {
        b"stsd": [track1_stsd],
        b"sgpd": [clone(box) for box in reb_stbl.find_children(b"sgpd")],
        b"sbgp": [clone(box) for box in reb_stbl.find_children(b"sbgp")],
        b"stts": [scaled_stts],
        b"ctts": [scaled_ctts],
        b"stss": [clone(reb_stbl.first_child(b"stss"))],
        b"stsc": [clone(reb_stbl.first_child(b"stsc"))],
        b"stsz": [clone(reb_stbl.first_child(b"stsz"))],
        b"stco": [patch_stco(reb_stbl.first_child(b"stco"), delta)],
    }
    track1 = set_stbl(orig_tracks[0], replace_boxes_in_order(orig_stbl, track1_repls))
    track1_mdhd = patch_mdhd_timescale_duration(clone(orig_tracks[0].first_child(b"mdia").first_child(b"mdhd")), 600, 11900)
    track1 = replace_mdia_boxes(track1, {b"mdhd": track1_mdhd})
    track1_children = []
    for child in track1.children:
        if child.typ == b"tkhd":
            track1_children.append(patch_tkhd_dimensions(child, reb_width, reb_height))
        elif child.typ == b"tapt":
            track1_children.append(patch_tapt(child, reb_width, reb_height))
        else:
            track1_children.append(clone(child))
    track1 = track1.rebuilt(children=track1_children)
    new_tracks.append(track1)

    # Tracks 2..N: preserve original track structure and sample-entry metadata, but use rebuilt tables for rebuilt mdat.
    passthrough_types = [b"sgpd", b"sbgp", b"stts", b"ctts", b"stss", b"stsc", b"stsz", b"stco"]
    for idx in range(1, len(orig_tracks)):
        orig_track = orig_tracks[idx]
        reb_track = reb_tracks[idx]
        orig_stbl = get_stbl(orig_track)
        reb_stbl = get_stbl(reb_track)
        repls: dict[bytes, list[Box]] = {b"stsd": [clone(orig_stbl.first_child(b"stsd"))]}
        for typ in passthrough_types:
            boxes = reb_stbl.find_children(typ)
            if not boxes:
                continue
            if typ == b"stco":
                repls[typ] = [patch_stco(box, delta) for box in boxes]
            else:
                repls[typ] = [clone(box) for box in boxes]
        new_tracks.append(set_stbl(orig_track, replace_boxes_in_order(orig_stbl, repls)))

    new_moov_children: list[Box] = []
    track_iter = iter(new_tracks)
    for child in orig_moov.children:
        if child.typ == b"trak":
            new_moov_children.append(next(track_iter))
        else:
            new_moov_children.append(clone(child))
    new_moov = orig_moov.rebuilt(children=new_moov_children)

    out_path.write_bytes(orig_ftyp.render() + orig_wide.render() + reb_mdat.render() + new_moov.render())
    print(out_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build a hybrid Apple-style MOV from an original MOV and a rebuilt/remuxed MOV.")
    parser.add_argument("--original", required=True, type=Path, help="Source Apple MOV to preserve metadata from")
    parser.add_argument("--rebuilt", required=True, type=Path, help="Rebuilt MOV containing compressed media payload/tables")
    parser.add_argument("--output", required=True, type=Path, help="Output hybrid MOV path")
    args = parser.parse_args()
    build(args.original, args.rebuilt, args.output)
