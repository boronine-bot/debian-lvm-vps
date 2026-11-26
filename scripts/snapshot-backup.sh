#!/bin/sh
# Take a short-lived read-only snapshot of vg0/root and run a backup of it.
#
# Usage: snapshot-backup.sh -- command [arguments]
# The command runs with SNAPSHOT_MOUNT set to the read-only mount. It must
# write the backup somewhere other than vg0/root, or the snapshot pays
# copy-on-write for the backup itself. The snapshot is removed afterwards,
# including when the command fails.
set -eu

origin=/dev/vg0/root
name=root-snap-$$
size="${SNAPSHOT_SIZE:-5G}"
mountpoint="${SNAPSHOT_MOUNT:-/mnt/root-snap}"

if [ "$(id -u)" -ne 0 ]; then
    echo "snapshot-backup.sh must run as root" >&2
    exit 1
fi
if [ "$#" -lt 2 ] || [ "$1" != "--" ]; then
    echo "usage: snapshot-backup.sh -- backup-command [arguments]" >&2
    exit 1
fi
shift

lvs --noheadings -o vg_free vg0 | grep -qv '[[:space:]]0' || {
    echo "vg0 has no free space for a snapshot" >&2
    exit 1
}

cleanup() {
    umount "$mountpoint" 2>/dev/null || true
    lvremove -f "/dev/vg0/$name" 2>/dev/null || true
    fsfreeze -u / 2>/dev/null || true
}
trap cleanup EXIT

fsfreeze -f /
lvcreate --snapshot --permission r --size "$size" --chunksize 64K \
    --name "$name" "$origin"
fsfreeze -u /

install -d "$mountpoint"
mount -o ro "/dev/vg0/$name" "$mountpoint"
export SNAPSHOT_MOUNT="$mountpoint"
"$@"
