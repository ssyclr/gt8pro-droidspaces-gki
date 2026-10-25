"""Helpers for checking a GKI kernel build against the official build.

Standard library only, so it runs on a bare runner.
"""
import re
import struct

# --------------------------------------------------------------- symvers


def load_symvers(path):
    """Return {symbol: (crc, module, export_type, namespace)}."""
    syms = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            fields = line.rstrip('\n').split('\t')
            if len(fields) < 4:
                continue
            crc, name, module, kind = fields[:4]
            namespace = fields[4] if len(fields) > 4 else ''
            syms[name] = (crc.lower(), module, kind, namespace)
    return syms


def compare_symvers(base, new):
    common = base.keys() & new.keys()
    return {
        'base_count': len(base),
        'new_count': len(new),
        'crc_changed': sorted(s for s in common if base[s][0] != new[s][0]),
        # EXPORT_SYMBOL -> _GPL or a namespace change breaks modules too.
        'export_changed': sorted(s for s in common if base[s][1:] != new[s][1:]),
        'removed': sorted(base.keys() - new.keys()),
        'added': sorted(new.keys() - base.keys()),
    }


# ---------------------------------------------------------------- config

_CONFIG_RE = re.compile(r'^(?:CONFIG_(\w+)=(.*)|# CONFIG_(\w+) is not set)$')


def load_config(path):
    """Return {option: value}; options that are not set map to 'n'."""
    opts = {}
    with open(path, encoding='utf-8') as f:
        for line in f:
            m = _CONFIG_RE.match(line.rstrip('\n'))
            if m and m.group(1):
                opts[m.group(1)] = m.group(2)
            elif m:
                opts[m.group(3)] = 'n'
    return opts


def load_option_list(path):
    """Return [(option, category)] from config/droidspaces_options.txt."""
    opts = []
    with open(path, encoding='utf-8') as f:
        for line in f:
            line = line.split('#', 1)[0].split()
            if line:
                opts.append((line[0], line[1]))
    return opts


# ---------------------------------------------------------- certificates

# SEQUENCE { SEQUENCE (tbsCertificate) { [0] { INTEGER 2 } ...
_CERT_RE = re.compile(rb'\x30\x82(..)\x30\x82(..)\xa0\x03\x02\x01\x02', re.S)


def find_der_certs(data):
    """Yield (offset, der) for every X.509 certificate in a kernel image."""
    for m in _CERT_RE.finditer(data):
        outer = int.from_bytes(m.group(1), 'big') + 4
        inner = int.from_bytes(m.group(2), 'big') + 4
        if inner < outer < 16384:
            yield m.start(), data[m.start():m.start() + outer]


# ------------------------------------------------------------------- BTF

# magic 0xeb9f, version 1, flags 0, hdr_len 24
_BTF_HEADER = b'\x9f\xeb\x01\x00\x18\x00\x00\x00'
_MODIFIERS = (8, 9, 10, 11, 18)  # typedef, volatile, const, restrict, type_tag
_FIXED_TRAILER = {1: 4, 3: 12, 14: 4, 17: 4}  # int, array, var, decl_tag
_VLEN_TRAILER = {4: 12, 5: 12, 6: 8, 13: 8, 15: 12, 19: 12}


def find_btf_blob(data):
    """Return the vmlinux .BTF blob from an ELF file or a raw Image."""
    start = 0
    while True:
        off = data.find(_BTF_HEADER, start)
        if off < 0:
            raise ValueError('no vmlinux BTF found')
        type_off, type_len, str_off, str_len = struct.unpack_from('<IIII', data, off + 8)
        end = off + 24 + str_off + str_len
        if type_off == 0 and str_off == type_len and type_len > 1 << 20 and end <= len(data):
            return data[off:end]
        start = off + 1


def parse_btf(blob):
    _, _, _, hdr_len, type_off, type_len, str_off, _ = struct.unpack_from('<HBBIIIII', blob, 0)
    strings = blob[hdr_len + str_off:]

    def name_at(off):
        return strings[off:strings.index(b'\0', off)].decode()

    types = [None]  # type id 0 is void
    p = hdr_len + type_off
    end = p + type_len
    while p < end:
        name_off, info, size_or_type = struct.unpack_from('<III', blob, p)
        p += 12
        kind, vlen, kflag = (info >> 24) & 0x1f, info & 0xffff, info >> 31
        t = {'kind': kind, 'name': name_at(name_off), 'ref': size_or_type}
        if kind in (4, 5):
            t['members'] = []
            for i in range(vlen):
                mname, mtype, moff = struct.unpack_from('<III', blob, p + 12 * i)
                bitfield = moff >> 24 if kflag else 0
                bitoff = moff & 0xffffff if kflag else moff
                t['members'].append((name_at(mname), mtype, bitoff, bitfield))
        elif kind == 3:
            t['array'] = struct.unpack_from('<III', blob, p)
        p += _FIXED_TRAILER.get(kind, 0) + _VLEN_TRAILER.get(kind, 0) * vlen
        types.append(t)
    return types


def _strip(types, tid):
    while tid and types[tid]['kind'] in _MODIFIERS:
        tid = types[tid]['ref']
    return tid


def _type_sig(types, tid):
    tid = _strip(types, tid)
    if not tid:
        return 'void'
    t = types[tid]
    kind = t['kind']
    if kind == 2:
        return 'ptr'
    if kind == 3:
        elem, _, nelems = t['array']
        return f'{_type_sig(types, elem)}[{nelems}]'
    if kind in (4, 5):
        tag = 'struct' if kind == 4 else 'union'
        return f"{tag} {t['name'] or '<anon>'} ({t['ref']}B)"
    if kind in (1, 6, 16, 19):
        return f"{t['name']} ({t['ref']}B)"
    if kind == 7:
        return f"fwd {t['name']}"
    return f'kind{kind}'


def _flatten(types, tid, base, prefix):
    """(path, bit offset, bitfield size, type) for each member, descending
    into anonymous struct/union members so their contents are compared too."""
    out = []
    for name, mtype, off, bitfield in types[tid]['members']:
        inner = _strip(types, mtype)
        path = prefix + (name or f'<anon@{(base + off) // 8}>')
        out.append((path, base + off, bitfield, _type_sig(types, mtype)))
        if not name and inner and types[inner]['kind'] in (4, 5) and not types[inner]['name']:
            out += _flatten(types, inner, base + off, path + '.')
    return out


def struct_layouts(types):
    """Return {'struct name': {(size, members), ...}} for named structs/unions."""
    layouts = {}
    for tid, t in enumerate(types):
        if t and t['kind'] in (4, 5) and t['name']:
            key = ('struct ' if t['kind'] == 4 else 'union ') + t['name']
            layouts.setdefault(key, set()).add((t['ref'], tuple(_flatten(types, tid, 0, ''))))
    return layouts


def describe_layout_change(old, new):
    """Explain how one struct layout differs from another."""
    (old_size, old_members), (new_size, new_members) = old, new
    old_map = {m[0]: m[1:] for m in old_members}
    new_map = {m[0]: m[1:] for m in new_members}
    common = old_map.keys() & new_map.keys()
    moved = sorted((p, old_map[p], new_map[p]) for p in common if old_map[p] != new_map[p])
    removed = sorted(old_map.keys() - new_map.keys())
    added = sorted((new_map[p][0] // 8, p, new_map[p][2]) for p in new_map.keys() - old_map.keys())
    return {
        'size': (old_size, new_size),
        'moved': moved,
        'removed': removed,
        'added': added,
        # Same size and nothing existing moved: new members sit in padding.
        'additions_only': old_size == new_size and not moved and not removed,
    }
