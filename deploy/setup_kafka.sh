#!/bin/sh
# Install Kafka and the arrivals services on the e2-micro VM. Safe to re-run.
#
# Run on the VM from the deployed code folder:
#   cd ~/tfl-pipeline && sh deploy/setup_kafka.sh
# Then start in order and watch memory (see docs/endpoints/arrivals.md):
#   sudo systemctl enable --now tfl-kafka
#   sudo systemctl enable --now tfl-arrivals-producer tfl-arrivals-detector tfl-arrivals-archiver
set -eu

KAFKA_VERSION=4.0.0
SCALA_VERSION=2.13
ARCHIVE="kafka_${SCALA_VERSION}-${KAFKA_VERSION}.tgz"
URL="https://archive.apache.org/dist/kafka/${KAFKA_VERSION}/${ARCHIVE}"

cd "$(dirname "$0")/.."

# 1. Free memory: the image's mail server is not used by anything here (~21 MB).
#    Disabled, not removed: `sudo systemctl enable --now exim4` restores it.
sudo systemctl disable --now exim4 2>/dev/null || true

# 2. Java 21 from Debian (Kafka 4.0 needs Java 17+; Debian 13 ships 21).
sudo apt-get install -y --no-install-recommends openjdk-21-jre-headless

# 3. Kafka from the Apache archive, verified against Apache's published SHA-512.
if [ ! -d "/opt/kafka_${SCALA_VERSION}-${KAFKA_VERSION}" ]; then
  work=$(mktemp -d)
  curl -fsSL -o "$work/$ARCHIVE" "$URL"
  curl -fsSL -o "$work/$ARCHIVE.sha512" "$URL.sha512"
  expected=$(sed 's/^[^:]*://' "$work/$ARCHIVE.sha512" | tr -d ' \n' | tr 'A-F' 'a-f')
  actual=$(sha512sum "$work/$ARCHIVE" | cut -d' ' -f1)
  if [ "$expected" != "$actual" ]; then
    echo "SHA-512 mismatch for $ARCHIVE: not installing" >&2
    exit 1
  fi
  sudo tar -xzf "$work/$ARCHIVE" -C /opt
  rm -rf "$work"
fi
sudo ln -sfn "/opt/kafka_${SCALA_VERSION}-${KAFKA_VERSION}" /opt/kafka

# 4. Its own unprivileged user, config, data and log folders.
id kafka >/dev/null 2>&1 || sudo useradd --system --no-create-home --shell /usr/sbin/nologin kafka
sudo mkdir -p /etc/kafka /var/lib/kafka/data /var/log/kafka
sudo cp deploy/kafka/server.properties /etc/kafka/server.properties
sudo chown -R kafka:kafka /var/lib/kafka /var/log/kafka

# 5. Format the storage once (KRaft needs a cluster ID before the first start).
if ! sudo test -f /var/lib/kafka/data/meta.properties; then
  cluster_id=$(sudo -u kafka /opt/kafka/bin/kafka-storage.sh random-uuid)
  sudo -u kafka /opt/kafka/bin/kafka-storage.sh format -t "$cluster_id" -c /etc/kafka/server.properties
fi

# 6. The Kafka client for the Python services.
.venv/bin/python -m pip install -q confluent-kafka==2.10.0

# 7. Services. The collector units gain OOMScoreAdjust=-500; apply it to the running
#    collectors too, without restarting them (a restart would leave a gap in their data).
sudo cp deploy/tfl-kafka.service deploy/tfl-arrivals-*.service \
        deploy/tfl-line-status.service deploy/tfl-stop-disruptions.service /etc/systemd/system/
sudo systemctl daemon-reload
for unit in tfl-line-status tfl-stop-disruptions; do
  pid=$(systemctl show -p MainPID --value "$unit")
  [ "$pid" -gt 0 ] && sudo choom -n -500 -p "$pid" >/dev/null
done

echo "Installed. Start with: sudo systemctl enable --now tfl-kafka"
