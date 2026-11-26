# Debian 13 VPS image

Builds a Debian 13 (trixie) amd64 installer ISO that installs itself: a
minimal server with ext4 root on LVM, free extents reserved for a short-lived
read-only snapshot, OpenSSH, and Docker. Nothing here creates a Ginernet VPS
or mounts the ISO. Those panel steps are marked TODO below.

The source is Debian's 64 MB netboot `mini.iso`, not the 700 MB netinst image.
The netinst image is large because it carries a package pool; this installer
does not. The installed system is a normal minimal Debian server plus the
packages named below, and it is much smaller than either ISO.

## What the installed system looks like

The preseed uses the normal guided partitioner, not a hand-written disk
layout:

- a small EFI system partition, only when the firmware is UEFI
- a 1 GiB ext4 `/boot` outside LVM
- volume group `vg0` with one ext4 logical volume, `vg0/root`
- the last 5–10 GiB of the disk left unused by the installer

After the base system is installed, `preseed/late-command` turns that unused
tail into a second LVM physical volume and adds it to `vg0`. It does not grow
`vg0/root`. That free space is what a snapshot uses while a backup runs.
Docker comes from Debian's `docker.io` package and uses its default overlay2
driver on ext4. Root has no password; the SSH public key given to the build
is installed for root, and password authentication is disabled.

A 40 GiB disk is the practical minimum. The snapshot reserve is 5 GiB and a
backup snapshot wants another 5 GiB of writes before it fills up.

## Build the ISO

The build needs `xorriso` and Python 3. It downloads nothing. The same build
runs on Gitea Actions: open **Actions → Build ISO → Run workflow**. It runs
only when dispatched, on a disposable runner that is removed with the job.
The result replaces the assets on the `debian-13-lvm` release; this Gitea
does not keep Actions artifacts. The workflow reads the repository secret
`SSH_PUBLIC_KEY`, which must be exactly one OpenSSH public key.

```sh
base=https://deb.debian.org/debian/dists/trixie/main/installer-amd64/current/images
curl -fLO "$base/SHA256SUMS"
curl -fL -o mini.iso "$base/netboot/mini.iso"
grep ' ./netboot/mini.iso$' SHA256SUMS | sed 's| ./netboot/mini.iso$|  mini.iso|' | sha256sum -c -

python3 scripts/build-iso.py \
    mini.iso \
    out/debian-13-lvm.iso \
    --ssh-public-key ~/.ssh/id_ed25519.pub
```

Check `out/debian-13-lvm.iso.sha256` before uploading. The source must be the
current trixie amd64 netboot `mini.iso`. Both BIOS and UEFI boot entries start
the text installer with the preseed, and the public key is stored at
`/preseed/authorized_key`. This environment has no KVM, so the install itself
has not been booted. The installed system downloads its packages from
`deb.debian.org`, so the VPS needs network access during installation.

The image wipes the first disk it finds. Booting it on a machine that already
has data destroys that disk.

## Install on Ginernet

TODO: confirm these panel steps on a throwaway VPS. The Manager documents
mounting a custom ISO, but the click path and the API operation are not in
the public docs.

1. Create a VPS with at least 40 GiB of disk. The stock Debian 13 image is
   fine; it will be overwritten. Give it a public IPv4.
2. TODO: upload `out/debian-13-lvm.iso`, or give the panel a URL it can fetch.
3. TODO: mount the ISO and set the VPS to boot from it. Power it on.
4. Wait for the install. It needs network access to `deb.debian.org` and
   `security.debian.org`. A 40 GiB install is several minutes, not seconds.
5. TODO: when SSH answers, detach the ISO before the next reboot. If it stays
   attached, the machine boots the installer again and wipes itself.

The installer asks the hypervisor to eject the disc and installs GRUB to the
disk. Whether Ginernet honours the eject is the thing to verify in step 5.

Log in as root with the key baked into the image:

```sh
ssh root@THE_VPS_ADDRESS
findmnt /
lvs
vgs
docker info
```

`findmnt` should show ext4 on `/dev/mapper/vg0-root`. `vgs` should show free
space in `vg0`.

## Back up from the running system

`scripts/snapshot-backup.sh` freezes the filesystem, creates a read-only
5 GiB snapshot, unfreezes, and mounts it. The command after `--` does the
actual copy and receives the mount path in `SNAPSHOT_MOUNT`. The snapshot is
removed when the command finishes or fails.

```sh
install -m 755 scripts/snapshot-backup.sh /usr/local/sbin/snapshot-backup
snapshot-backup -- borg create ssh://backup-host/repo::{now} "$SNAPSHOT_MOUNT"
```

The backup destination must not be on `vg0/root`. Reading the snapshot is
free; every write to the live volume while the snapshot exists consumes the
5 GiB. Set `SNAPSHOT_SIZE` to raise it. If that space runs out, LVM drops the
snapshot and the live volume keeps running. Do not convert `vg0` to a thin
pool: a full thin pool returns errors on the live root.

The snapshot is crash-consistent. Freeze a database with its own tool before
running this if a clean database backup matters. A snapshot on the same disk
is not a backup until the copy leaves the machine. Ginernet's nightly
whole-disk backup is a separate copy and is not application-consistent either.
