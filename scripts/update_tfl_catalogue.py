
import csv
from pathlib import Path

path = Path("config/tfl_api_catalogue.csv")

retired_exact = {
    "/AccidentStats/{year}",
    "/Occupancy/ChargeConnector",
    "/Occupancy/ChargeConnector/{ids}",
    "/TravelTimes/overlay/{z}/mapcenter/{mapCenterLat}/{mapCenterLon}/pinlocation/{pinLat}/{pinLon}/dimensions/{width}/{height}",
    "/TravelTimes/compareOverlay/{z}/mapcenter/{mapCenterLat}/{mapCenterLon}/pinlocation/{pinLat}/{pinLon}/dimensions/{width}/{height}",
}

with path.open(newline="", encoding="utf-8") as f:
    reader = csv.DictReader(f)
    fieldnames = reader.fieldnames
    rows = list(reader)

changed = 0

for row in rows:
    if row["path"] in retired_exact:
        row["status"] = "RETIRED"
        changed += 1

with path.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=fieldnames)
    writer.writeheader()
    writer.writerows(rows)

print("Catalogue updated:", path)
print("Marked retired:", changed)