#!/usr/bin/env python3
"""Write the ABI / config report for one workflow run and decide pass/fail.

Usage: report.py <artifacts dir> <options file> <out dir>

<artifacts dir> holds the baseline/, droidspaces/ and (optionally) stock/
artifacts of the workflow. Exit status is non-zero if the droidspaces build
must not be flashed.
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import kabi  # noqa: E402

FORBIDDEN = ('CFS_BANDWIDTH', 'CGROUP_PIDS')
TRUSTED_KEYS = '"certs/stock_gki_module_signing.pem"'


def read(path, default=''):
    try:
        with open(path, encoding='utf-8') as f:
            return f.read().strip()
    except FileNotFoundError:
        return default


class Report:
    def __init__(self):
        self.lines = []
        self.failures = []
        self.warnings = []

    def p(self, text=''):
        self.lines.append(text)

    def h(self, text):
        self.lines += ['', f'## {text}', '']

    def details(self, summary, items, limit=3000):
        items = list(items)
        if not items:
            return
        self.p(f'<details><summary>{summary}（{len(items)}）</summary>')
        self.p()
        self.p('```')
        self.lines += items[:limit]
        if len(items) > limit:
            self.p(f'... 另有 {len(items) - limit} 项未列出')
        self.p('```')
        self.p('</details>')
        self.p()


def abi_section(r, art, have_stock):
    r.h('ABI：vmlinux.symvers 对比')
    official = kabi.load_symvers(f'{art}/baseline/vmlinux.symvers')
    ds = kabi.load_symvers(f'{art}/droidspaces/vmlinux.symvers')
    rows = [('官方 → droidspaces（判定依据）', official, ds, kabi.compare_symvers(official, ds))]
    if have_stock:
        stock = kabi.load_symvers(f'{art}/stock/vmlinux.symvers')
        rows += [('官方 → stock（环境对照）', official, stock, kabi.compare_symvers(official, stock)),
                 ('stock → droidspaces', stock, ds, kabi.compare_symvers(stock, ds))]
    r.p('| 对比 | 基准符号数 | 新符号数 | CRC 变化 | 删除 | 导出类型/命名空间变化 | 新增 |')
    r.p('|---|---|---|---|---|---|---|')
    for label, _, _, c in rows:
        r.p(f"| {label} | {c['base_count']} | {c['new_count']} | {len(c['crc_changed'])} | "
            f"{len(c['removed'])} | {len(c['export_changed'])} | {len(c['added'])} |")
    r.p()
    main = rows[0][3]
    if main['crc_changed'] or main['removed'] or main['export_changed']:
        r.failures.append('已有符号的 CRC / 导出属性有变化，或有符号被删除')
    if have_stock:
        control = rows[1][3]
        if any(control[k] for k in ('crc_changed', 'removed', 'export_changed', 'added')):
            r.warnings.append('stock 对照构建与官方构建的符号不一致：构建环境与官方不同')
    r.details('CRC 变化（官方 → droidspaces）',
              (f'{s}: {official[s][0]} -> {ds[s][0]}' for s in main['crc_changed']))
    r.details('被删除的符号', main['removed'])
    r.details('导出类型/命名空间变化',
              (f'{s}: {official[s][1:]} -> {ds[s][1:]}' for s in main['export_changed']))
    r.details('新增符号（可以接受）', (f'{s}  {ds[s][2]}  {ds[s][3]}'.rstrip() for s in main['added']))
    return main


def config_section(r, art, options_file, skipped, have_stock):
    r.h('内核配置（从编出的 Image 用 extract-ikconfig 提取）')
    official = kabi.load_config(f'{art}/baseline/config')
    ds = kabi.load_config(f'{art}/droidspaces/config')
    missing = set(read(f'{art}/droidspaces/kconfig_missing.txt').split())
    r.p('| 选项 | 分类 | 官方 | 本次 | 结果 |')
    r.p('|---|---|---|---|---|')
    for opt, category in kabi.load_option_list(options_file):
        before, after = official.get(opt, '-'), ds.get(opt, '-')
        if opt in skipped:
            verdict = '⏭ 本次跳过'
        elif after == 'y':
            verdict = '✅ 已开启'
        elif opt in missing:
            verdict = '➖ 该源码树的 Kconfig 里没有这个符号'
        else:
            verdict = '❌ 未生效（依赖不满足被丢弃？）'
            r.failures.append(f'CONFIG_{opt} 没有生效')
        r.p(f'| `{opt}` | {category} | {before} | {after} | {verdict} |')
    for opt in FORBIDDEN:
        before, after = official.get(opt, 'n'), ds.get(opt, 'n')
        ok = before == after and not (opt == 'CGROUP_PIDS' and after != 'n')
        r.p(f"| `{opt}` | 禁止改动 | {before} | {after} | {'✅ 与原厂一致' if ok else '❌ 被改动'} |")
        if not ok:
            r.failures.append(f'CONFIG_{opt} 与原厂不一致')
    modversions = ds.get('MODVERSIONS', 'n')
    r.p(f"| `MODVERSIONS` | 必须保持 | {official.get('MODVERSIONS', 'n')} | {modversions} | "
        f"{'✅' if modversions == 'y' else '❌'} |")
    if modversions != 'y':
        r.failures.append('CONFIG_MODVERSIONS 被关闭')
    keys = ds.get('SYSTEM_TRUSTED_KEYS', '-')
    r.p(f"| `SYSTEM_TRUSTED_KEYS` | 信任原厂证书 | {official.get('SYSTEM_TRUSTED_KEYS', '-')} | {keys} | "
        f"{'✅' if keys == TRUSTED_KEYS else '❌'} |")
    if keys != TRUSTED_KEYS:
        r.failures.append('CONFIG_SYSTEM_TRUSTED_KEYS 未按预期设置')
    r.p()
    diff = sorted(o for o in official.keys() | ds.keys() if official.get(o, '-') != ds.get(o, '-'))
    r.details('官方 → droidspaces 全部配置差异（含 Kconfig 自动派生的选项）',
              (f"{o}: {official.get(o, '-')} -> {ds.get(o, '-')}" for o in diff))
    if have_stock:
        stock = kabi.load_config(f'{art}/stock/config')
        drift = sorted(o for o in official.keys() | stock.keys() if official.get(o) != stock.get(o))
        if drift:
            r.warnings.append('stock 对照构建的配置与官方不一致')
            r.details('官方 → stock 配置差异（应为空）',
                      (f"{o}: {official.get(o, '-')} -> {stock.get(o, '-')}" for o in drift))
        else:
            r.p('stock 对照构建的配置与官方 `.config` 完全一致。')
    return diff


def cert_section(r, art):
    r.h('模块签名证书')
    with open(f'{art}/baseline/stock_gki_module_signing.der', 'rb') as f:
        stock_der = f.read()
    with open(f'{art}/droidspaces/Image', 'rb') as f:
        certs = [der for _, der in kabi.find_der_certs(f.read())]
    found = stock_der in certs
    r.p(f'droidspaces Image 内置证书 {len(certs)} 张；原厂模块签名证书'
        f"{'已包含 ✅' if found else '未包含 ❌'}。")
    r.p()
    r.p('原厂证书信息：')
    r.p()
    r.p('```')
    r.p(read(f'{art}/baseline/certs.txt'))
    r.p('```')
    if not found:
        r.failures.append('原厂模块签名证书没有打进新内核，system_dlkm 模块会被拒载')
    return found


def btf_section(r, art, have_stock):
    r.h('结构体布局（BTF，覆盖内核里所有具名结构体）')
    with open(f'{art}/baseline/vmlinux.btf', 'rb') as f:
        official = kabi.struct_layouts(kabi.parse_btf(f.read()))
    comparisons = [('droidspaces', '官方 → droidspaces')]
    if have_stock:
        comparisons.append(('stock', '官方 → stock（对照，应无变化）'))
    summary = {}
    for variant, label in comparisons:
        path = f'{art}/{variant}/vmlinux.btf'
        if not os.path.exists(path):
            r.p(f'{label}：缺少 BTF，跳过。')
            continue
        with open(path, 'rb') as f:
            new = kabi.struct_layouts(kabi.parse_btf(f.read()))
        changed = sorted(k for k in official.keys() & new.keys() if official[k] != new[k])
        padding_only, moved = [], []
        for key in changed:
            if len(official[key]) == 1 and len(new[key]) == 1:
                change = kabi.describe_layout_change(next(iter(official[key])), next(iter(new[key])))
            else:
                change = {'additions_only': False, 'size': None, 'moved': [], 'removed': [], 'added': [],
                          'note': '同名结构体有多个定义'}
            (padding_only if change['additions_only'] else moved).append((key, change))
        r.p(f'**{label}**：比较了 {len(official.keys() & new.keys())} 个结构体；'
            f'只在空隙里新增成员 {len(padding_only)} 个，已有成员移位/尺寸变化 {len(moved)} 个；'
            f'新出现 {len(new.keys() - official.keys())} 个，消失 {len(official.keys() - new.keys())} 个。')
        r.p()
        lines = []
        for key, c in padding_only:
            lines.append(f'{key}: size {c["size"][0]}B 不变')
            lines += [f'    + @{off}B {member} : {sig}' for off, member, sig in c['added']]
        r.details(f'{label}：只在空隙里新增成员的结构体', lines)
        lines = []
        for key, c in moved:
            lines.append(f'{key}: size {c["size"]} {c.get("note", "")}'.rstrip())
            lines += [f'    ~ {m}: {o} -> {n}' for m, o, n in c['moved']]
            lines += [f'    - {m}' for m in c['removed']]
            lines += [f'    + @{off}B {member} : {sig}' for off, member, sig in c['added']]
        r.details(f'{label}：已有成员移位或尺寸变化的结构体（需人工审查）', lines)
        r.details(f'{label}：新出现的结构体', sorted(new.keys() - official.keys()))
        r.details(f'{label}：消失的结构体', sorted(official.keys() - new.keys()))
        if moved and variant == 'droidspaces':
            r.warnings.append(f'{len(moved)} 个结构体的已有成员发生移位或尺寸变化，需人工审查')
        if (padding_only or moved) and variant == 'stock':
            r.warnings.append('stock 对照构建的结构体布局与官方不一致')
        summary[variant] = {'padding_only': [k for k, _ in padding_only], 'moved': [k for k, _ in moved]}
    return summary


def main(art, options_file, out):
    sys.stdout.reconfigure(encoding='utf-8')
    skipped = set(os.environ.get('SKIP_OPTIONS', '').split())
    have_stock = os.path.isdir(f'{art}/stock')
    r = Report()
    abi = abi_section(r, art, have_stock)
    config_diff = config_section(r, art, options_file, skipped, have_stock)
    cert_ok = cert_section(r, art)
    btf = btf_section(r, art, have_stock)

    gki = dict(line.split('=', 1) for line in read(f'{art}/baseline/gki-info.txt').splitlines() if '=' in line)
    head = [
        '# ABI 自检报告',
        '',
        f"- 官方构建：`{gki.get('kernel_release', '?')}`",
        f"- droidspaces 构建：`{read(f'{art}/droidspaces/linux_version.txt', '?')}`",
        f"- stock 对照构建：`{read(f'{art}/stock/linux_version.txt', '未构建')}`",
        f"- common 提交：`{read(f'{art}/droidspaces/common_head.txt', '?')}`",
        f"- check_defconfig：{'defconfig 校验确实报错，已对 kernel_aarch64 关闭' if os.path.exists(f'{art}/droidspaces/check_defconfig_disabled') else '未改动'}",
        f"- 本次跳过的推荐选项：{' '.join(sorted(skipped)) or '无'}",
        '',
        f"**结论：{'通过 ✅' if not r.failures else '不通过 ❌'}**",
    ]
    head += [f'- ❌ {f}' for f in r.failures]
    head += [f'- ⚠️ {w}' for w in r.warnings]
    os.makedirs(out, exist_ok=True)
    with open(f'{out}/abi-report.md', 'w', encoding='utf-8') as f:
        f.write('\n'.join(head + r.lines) + '\n')
    with open(f'{out}/abi-report.json', 'w', encoding='utf-8') as f:
        json.dump({'passed': not r.failures, 'failures': r.failures, 'warnings': r.warnings,
                   'abi': abi, 'config_diff': config_diff, 'stock_cert_embedded': cert_ok,
                   'btf': btf}, f, ensure_ascii=False, indent=1)
    print('\n'.join(head))
    return 1 if r.failures else 0


if __name__ == '__main__':
    if len(sys.argv) != 4:
        sys.exit(__doc__)
    sys.exit(main(*sys.argv[1:]))
