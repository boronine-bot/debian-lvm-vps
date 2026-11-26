#!/usr/bin/env python3
"""Remaster a Debian amd64 netinst ISO so it installs itself.

The result boots the text installer with the preseed on the disc. It does
not start a virtual machine and it does not talk to Ginernet.
"""

import argparse
import hashlib
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


def run(command, **kwargs):
    subprocess.run(command, check=True, **kwargs)


def boot_arguments(source: Path) -> list[str]:
    """Replay the source ISO's boot layout with a new volume id."""
    result = subprocess.run(
        ["xorriso", "-indev", str(source), "-report_el_torito", "as_mkisofs"],
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


def patch_isolinux(tree: Path) -> None:
    (tree / "isolinux/isolinux.cfg").write_text(
        "# D-I config version 2.0\n"
        "path\n"
        "prompt 0\n"
        "timeout 1\n"
        "default auto\n"
        "label auto\n"
        "    kernel /install.amd/vmlinuz\n"
        f"    append initrd=/install.amd/initrd.gz {APPEND}\n"
    )


def patch_grub(tree: Path) -> None:
    config = tree / "boot/grub/grub.cfg"
    original = config.read_text()
    preamble, separator, _ = original.partition("set theme=")
    if not separator:
        raise ValueError("Debian GRUB configuration has no theme marker to replace")
    config.write_text(
        preamble
        + "set default=0\n"
        + "set timeout=1\n"
        + "menuentry 'Automated Debian install' {\n"
        + "    set background_color=black\n"
        + f"    linux /install.amd/vmlinuz {APPEND}\n"
        + "    initrd /install.amd/initrd.gz\n"
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
        run(["xorriso", "-osirrox", "on", "-indev", str(source), "-extract", "/", str(tree)])
        for path in tree.rglob("*"):
            path.chmod(path.stat().st_mode | 0o200)

        preseed = tree / "preseed"
        preseed.mkdir()
        for name in ("preseed.cfg", "late-command"):
            shutil.copy(ROOT / "preseed" / name, preseed / name)
        (preseed / "late-command").chmod(0o755)
        shutil.copy(public_key, preseed / "authorized_key")

        patch_isolinux(tree)
        patch_grub(tree)
        refresh_checksums(tree)

        output.parent.mkdir(parents=True, exist_ok=True)
        command = [
            "xorriso", "-as", "mkisofs", "-o", str(output), "-r",
            *boot_arguments(source), str(tree),
        ]
        run(command)
    digest = hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix + ".sha256").write_text(f"{digest}  {output.name}\n")
    print(f"{digest}  {output}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="official Debian amd64 netinst ISO")
    parser.add_argument("output", type=Path, help="where to write the remastered ISO")
    parser.add_argument("--ssh-public-key", required=True, type=Path)
    args = parser.parse_args()
    key = args.ssh_public_key.read_text().strip()
    if not key.startswith(("ssh-ed25519 ", "ssh-rsa ", "ecdsa-sha2-")):
        sys.exit(f"{args.ssh_public_key} is not an OpenSSH public key")
    if not args.source.is_file():
        sys.exit(f"source ISO not found: {args.source}")
    build(args.source, args.output, args.ssh_public_key)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
