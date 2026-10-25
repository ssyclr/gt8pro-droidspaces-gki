#!/usr/bin/env python3
"""Pull data out of a kernel image.

  extract.py certs <Image> <out_dir>      embedded X.509 certificates, as DER
  extract.py btf <Image|vmlinux> <out>    the vmlinux .BTF blob
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kabi  # noqa: E402


def main(cmd, src, dst):
    with open(src, 'rb') as f:
        data = f.read()
    if cmd == 'certs':
        os.makedirs(dst, exist_ok=True)
        certs = list(kabi.find_der_certs(data))
        if not certs:
            sys.exit(f'{src}: no certificate found')
        for i, (off, der) in enumerate(certs):
            path = os.path.join(dst, f'cert{i}.der')
            with open(path, 'wb') as f:
                f.write(der)
            print(f'{path} offset={off:#x} length={len(der)}')
    elif cmd == 'btf':
        blob = kabi.find_btf_blob(data)
        with open(dst, 'wb') as f:
            f.write(blob)
        print(f'{dst}: {len(blob)} bytes')
    else:
        sys.exit(__doc__)


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    main(*sys.argv[1:])
