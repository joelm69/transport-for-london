#!/bin/sh
# Sample container memory every 30 s for N minutes (default 60) into docker/kafka_test/memory.csv,
# and report any container the kernel killed for running out of memory.
cd "$(dirname "$0")"
minutes=${1:-60}
out=memory.csv
echo "time,container,mem_usage,mem_percent,cpu_percent" > "$out"
end=$(( $(date +%s) + minutes * 60 ))
while [ "$(date +%s)" -lt "$end" ]; do
  docker stats --no-stream --format "{{.Name}},{{.MemUsage}},{{.MemPerc}},{{.CPUPerc}}" \
    | sed "s/^/$(date -u +%H:%M:%S),/" >> "$out"
  sleep 30
done
for c in $(docker compose -f compose.yaml ps -aq); do
  docker inspect -f '{{.Name}} OOMKilled={{.State.OOMKilled}} restarts={{.RestartCount}} status={{.State.Status}}' "$c"
done
