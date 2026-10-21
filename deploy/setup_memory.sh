#!/bin/sh
# Memory headroom for the e2-micro VM (1 GB RAM): compressed RAM swap (zram) first,
# a disk swap file as a last resort. Safe to re-run.
#   sudo sh deploy/setup_memory.sh
set -eu

# 1. zram: compressed swap in RAM, used first (priority 100).
apt-get update -qq
DEBIAN_FRONTEND=noninteractive apt-get install -y -qq zram-tools
cat > /etc/default/zramswap <<'CONF'
# Managed by deploy/setup_memory.sh
ALGO=zstd
PERCENT=50
PRIORITY=100
CONF
systemctl enable zramswap >/dev/null 2>&1
systemctl restart zramswap

# 2. Disk swap file: 1 GB, only used when zram is full (priority 10).
if [ ! -f /swapfile ]; then
    fallocate -l 1G /swapfile
    chmod 600 /swapfile
    mkswap /swapfile >/dev/null
fi
swapon --show=NAME | grep -q '^/swapfile$' || swapon -p 10 /swapfile
grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw,pri=10 0 0' >> /etc/fstab

# 3. Kernel settings suited to zram.
cat > /etc/sysctl.d/99-tfl-memory.conf <<'CONF'
# Managed by deploy/setup_memory.sh
# Swapping to zram is cheap (it stays in RAM), so move idle pages out more readily.
vm.swappiness = 100
# Read swap one page at a time: read-ahead helps disks, not compressed RAM.
vm.page-cluster = 0
CONF
sysctl -q -p /etc/sysctl.d/99-tfl-memory.conf

swapon --show
