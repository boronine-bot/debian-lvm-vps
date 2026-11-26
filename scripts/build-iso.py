#!/usr/bin/env python3
"""Remaster a Debian amd64 netinst ISO so it installs itself.

The result boots the text installer with the preseed on the disc. It does
not start a virtual machine and it does not talk to Ginernet.
"""

import argparse
import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

APPEND = (
    "auto=true priority=critical locale=en_US.UTF-8 keymap=us "
    "preseed/file=/cdrom/preseed/preseed.cfg --- quiet"
)

ROOT = Path(__file__).resolve().parent.parent
XORRISO = os.environ.get("XORRISO", "xorriso")


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def boot_arguments(source: Path) -> list[str]:
    """Replay the source ISO's boot layout with a new volume id."""
    result = subprocess.run(
        [XORRISO, "-indev", str(source), "-report_el_torito", "as_mkisofs"],
        check=True, capture_output=True, text=True,
    )
    arguments = []
    for line in result.stdout.splitlines():
        if line.startswith("-V "):
            arguments.extend(["-V", "Debian 13 amd64 LVM"])
            continue
        if "interval:local_fs" in line:
            line = line.replace(str(source), "SOURCE")
        arguments.extend(split_xorriso(line))
    return [argument.replace("SOURCE", str(source)) for argument in arguments]


def split_xorriso(line: str) -> list[str]:
    arguments = []
    token = []
    quoted = False
    for character in line.strip():
        if character == "'":
            quoted = not quoted
        elif character == " " and not quoted:
            if token:
                arguments.append("".join(token))
                token = []
        else:
            token.append(character)
    if quoted:
        raise ValueError(f"unterminated quote from xorriso: {line}")
    if token:
        arguments.append("".join(token))
    return arguments


def boot_paths(tree: Path) -> tuple[str, str]:
    """Kernel and initrd paths differ between netinst and the netboot mini image."""
    candidates = (("/linux", "/initrd.gz"), ("/install.amd/vmlinuz", "/install.amd/initrd.gz"))
    for kernel, initrd in candidates:
        if (tree / kernel[1:]).is_file() and (tree / initrd[1:]).is_file():
            return kernel, initrd
    raise ValueError("Debian installer kernel and initrd were not found on the ISO")


def patch_isolinux(tree: Path, kernel: str, initrd: str) -> None:
    config = tree / "isolinux.cfg"
    if not config.exists():
        config = tree / "isolinux/isolinux.cfg"
    if not config.exists():
        raise ValueError("Debian isolinux configuration was not found on the ISO")
    config.write_text(
        "# D-I config version 2.0\n"
        "path\n"
        "prompt 0\n"
        "timeout 1\n"
        "default auto\n"
        "label auto\n"
        f"    kernel {kernel}\n"
        f"    append initrd={initrd} {APPEND}\n"
    )


def patch_grub(tree: Path, kernel: str, initrd: str) -> None:
    config = tree / "boot/grub/grub.cfg"
    original = config.read_text()
    marker = "insmod play\n" if "insmod play\n" in original else "set theme="
    preamble, separator, _ = original.partition(marker)
    if not separator:
        raise ValueError("Debian GRUB configuration has no recognizable menu marker")
    config.write_text(
        preamble
        + "set default=0\n"
        + "set timeout=1\n"
        + "menuentry 'Automated Debian install' {\n"
        + "    set background_color=black\n"
        + f"    linux {kernel} {APPEND}\n"
        + f"    initrd {initrd}\n"
        + "}\n"
    )


def refresh_checksums(tree: Path) -> None:
    checksums = tree / "md5sum.txt"
    if not checksums.exists():
        return
    changed = {"./isolinux/isolinux.cfg", "./boot/grub/grub.cfg"}
    kept = [
        line for line in checksums.read_text().splitlines()
        if len(line) < 34 or line[34:] not in changed
    ]
    for relative in sorted(changed):
        path = tree / relative[2:]
        digest = hashlib.md5(path.read_bytes()).hexdigest()
        kept.append(f"{digest}  {relative}")
    checksums.write_text("\n".join(kept) + "\n")


def build(source: Path, output: Path, public_key: Path) -> None:
    with tempfile.TemporaryDirectory(prefix="debian-lvm-") as temporary:
        tree = Path(temporary) / "tree"
        run([XORRISO, "-osirrox", "on", "-indev", str(source), "-extract", "/", str(tree)])
        for path in (tree, *tree.rglob("*")):
            path.chmod(path.stat().st_mode | 0o200)

        preseed = tree / "preseed"
        preseed.mkdir()
        for name in ("preseed.cfg", "late-command"):
            shutil.copy(ROOT / "preseed" / name, preseed / name)
        (preseed / "late-command").chmod(0o755)
        shutil.copy(public_key, preseed / "authorized_key")

        kernel, initrd = boot_paths(tree)
        patch_isolinux(tree, kernel, initrd)
        patch_grub(tree, kernel, initrd)
        refresh_checksums(tree)

        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            XORRISO, "-as", "mkisofs", "-o", str(output), "-r",
            *boot_arguments(source), str(tree),
        ]
        run(command)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
    print(f"{digest}  {output}")


def public_key(argument: Path | None) -> Path:
    """Read one OpenSSH public key from a file or SSH_PUBLIC_KEY."""
    if argument is not None:
        text = argument.read_text()
        source = argument
    else:
        text = os.environ.get("SSH_PUBLIC_KEY", "")
        source = "SSH_PUBLIC_KEY"
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if len(lines) != 1 or not lines[0].startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        sys.exit(f"{source} must contain exactly one OpenSSH public key")
    path = Path(tempfile.mkdtemp(prefix="ssh-key-")) / "authorized_key"
    path.write_text(lines[0] + "\n")
    path.chmod(0o600)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="official Debian amd64 netinst ISO")
    parser.add_argument("output", type=Path, help="where to write the remastered ISO")
    parser.add_argument("--ssh-public-key", type=Path,
                        help="public key file; omit to read SSH_PUBLIC_KEY")
    args = parser.parse_args()
    if not args.source.is_file():
        sys.exit(f"source ISO not found: {args.source}")
    build(args.source, args.output, public_key(args.ssh_public_key))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
