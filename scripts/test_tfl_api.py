import csv
import json
import os
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE_URL = "https://api.tfl.gov.uk"
SPEC_FILE = Path("/tmp/tfl_swagger.json")
CATALOGUE_FILE = Path("config/tfl_api_catalogue.csv")
OUTPUT_FILE = Path("reports/tfl_api_test_results.json")
TIMEOUT = 15
DELAY_SECONDS = 0.2

# Plausible values for common TfL parameters.
# These are test inputs, not guaranteed-valid resource IDs.
SAMPLE_VALUES = {
    "year": "2025",
    "startdate": "2026-10-01",
    "enddate": "2026-10-12",
    "direction": "inbound",
    "mode": "tube",
    "modes": "tube",
    "from": "victoria",
    "to": "waterloo",
    "id": "BikePoints_1",
    "ids": "victoria",
    "naptan": "940GZZLUVIC",
    "naptanid": "940GZZLUVIC",
    "stoppointid": "940GZZLUVIC",
    "stationid": "940GZZLUVIC",
    "count": "5",
    "page": "1",
    "pagesize": "10",
    "query": "victoria",
    "searchquery": "victoria",
    "category": "Tube",
    "severity": "Minor",
}

def load_env():
    """Read .env without printing or exposing secrets."""
    values = {}
    env_file = Path(".env")
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip().strip('"').strip("'")
    values.update(os.environ)
    return values

def normalise(name):
    return re.sub(r"[^a-z0-9]", "", name.lower())

def sample_value(parameter):
    name = parameter.get("name", "")
    key = normalise(name)

    if "enum" in parameter and parameter["enum"]:
        return str(parameter["enum"][0])
    if "default" in parameter:
        return str(parameter["default"])
    if "example" in parameter:
        return str(parameter["example"])
    if key in SAMPLE_VALUES:
        return SAMPLE_VALUES[key]

    schema = parameter.get("schema", {})
    if "enum" in schema and schema["enum"]:
        return str(schema["enum"][0])
    if "default" in schema:
        return str(schema["default"])

    # No invented value for unknown required parameters.
    return None

def classify(status, error=None):
    if error:
        return "ERROR"
    if 200 <= status < 300:
        return "PASS"
    if status in (401, 403):
        return "AUTH_ERROR"
    if status == 404:
        return "NOT_FOUND"
    if status == 429:
        return "RATE_LIMITED"
    if status in (400, 422):
        return "NEEDS_PARAMETER_OR_INVALID_INPUT"
    return "HTTP_ERROR"

def main():
    if not SPEC_FILE.exists():
        raise SystemExit(
            "Swagger file missing: /tmp/tfl_swagger.json. "
            "Download the specification first."
        )

    env = load_env()
    api_key = env.get("TFL_API_KEY", "")
    if not api_key:
        raise SystemExit(
            "TFL_API_KEY was not found in .env or the environment. "
            "Check the variable name; do not paste the key into chat."
        )

    spec = json.loads(SPEC_FILE.read_text())
    operations = []

    for path, path_details in spec.get("paths", {}).items():
        for method, operation in path_details.items():
            if method.lower() not in {
                "get", "post", "put", "patch", "delete", "options", "head"
            }:
                continue
            operations.append({
                "method": method.upper(),
                "path": path,
                "operation": operation,
                "path_details": path_details,
            })

    results = []
    total = len(operations)
    print(f"Swagger operations found: {total}")
    print("Testing GET operations only; other methods will be skipped.")
    print("Requests are sequential with a short delay.\n")

    for index, item in enumerate(operations, start=1):
        method = item["method"]
        path_template = item["path"]
        operation = item["operation"]
        path_details = item["path_details"]
        result = {
            "method": method,
            "path": path_template,
            "operation_id": operation.get("operationId", ""),
            "summary": operation.get("summary", ""),
            "tested_at_utc": datetime.now(timezone.utc).isoformat(),
            "http_status": None,
            "result": None,
            "duration_ms": None,
            "response_bytes": None,
            "content_type": None,
            "error": None,
            "test_url": None,
        }

        if method != "GET":
            result["result"] = "SKIPPED_NON_GET"
            result["error"] = "Read-only test run; method not executed."
            results.append(result)
            print(f"[{index}/{total}] SKIP {method:6} {path_template}")
            continue

        parameters = (
            path_details.get("parameters", [])
            + operation.get("parameters", [])
        )

        # Fill path placeholders from documented parameter metadata or
        # known safe sample values. Skip when a required value is unknown.
        path = path_template
        unresolved = []

        for match in re.findall(r"\{([^{}]+)\}", path_template):
            parameter = next(
                (
                    p for p in parameters
                    if p.get("name") == match and p.get("in") == "path"
                ),
                {"name": match, "in": "path", "required": True},
            )
            value = sample_value(parameter)
            if value is None:
                unresolved.append(match)
            else:
                path = path.replace(
                    "{" + match + "}",
                    urllib.parse.quote(value, safe=""),
                )

        if unresolved:
            result["result"] = "NEEDS_PARAMETER"
            result["error"] = (
                "No safe sample value for required path parameter(s): "
                + ", ".join(unresolved)
            )
            results.append(result)
            print(f"[{index}/{total}] NEEDS PARAM {path_template}")
            continue

        query = {}
        missing_query_parameters = []

        for parameter in parameters:
            if parameter.get("in") != "query" or not parameter.get("required"):
                continue
            name = parameter.get("name", "")
            value = sample_value(parameter)
            if value is None:
                missing_query_parameters.append(name)
            else:
                query[name] = value

        if missing_query_parameters:
            result["result"] = "NEEDS_PARAMETER"
            result["error"] = (
                "Unknown required query parameter(s): "
                + ", ".join(missing_query_parameters)
            )
            results.append(result)
            print(f"[{index}/{total}] NEEDS PARAM {path_template}")
            continue
        query["app_key"] = api_key
        url = BASE_URL + path + "?" + urllib.parse.urlencode(query)

          # Match the request format that returned HTTP 200.
        request = urllib.request.Request(
            url,
            headers={
                "Accept": "application/json",
                "User-Agent": "Mozilla/5.0 TfLTransportDataProject/1.0",
            },
            method="GET",
        )

        result["test_url"] = BASE_URL + path
        start = time.monotonic()

        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
                body = response.read()
                status = response.status
                content_type = response.headers.get("Content-Type", "")
                result["http_status"] = status
                result["response_bytes"] = len(body)
                result["content_type"] = content_type
                result["result"] = classify(status)
        except urllib.error.HTTPError as exc:
            body = exc.read()
            result["http_status"] = exc.code
            result["response_bytes"] = len(body)
            result["content_type"] = exc.headers.get("Content-Type", "")
            result["result"] = classify(exc.code)
            # Avoid saving error bodies that might contain unnecessary details.
            result["error"] = f"HTTP {exc.code}"
        except Exception as exc:
            result["result"] = "ERROR"
            result["error"] = type(exc).__name__

        result["duration_ms"] = round((time.monotonic() - start) * 1000, 1)
        results.append(result)

        print(
            f"[{index}/{total}] {result['result']:32} "
            f"{str(result['http_status'] or '-'):>4} "
            f"{path_template}"
        )
        time.sleep(DELAY_SECONDS)

        # Save progress after every operation, so an interruption
        # does not discard earlier results.
        OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
        OUTPUT_FILE.write_text(json.dumps(results, indent=2))

    counts = {}
    for result in results:
        counts[result["result"]] = counts.get(result["result"], 0) + 1

    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "base_url": BASE_URL,
        "swagger_path_count": len(spec.get("paths", {})),
        "operation_count": total,
        "results_summary": counts,
        "operations": results,
    }
    OUTPUT_FILE.write_text(json.dumps(report, indent=2))

    print("\n--- SUMMARY ---")
    print(f"Operations discovered: {total}")
    for status, count in sorted(counts.items()):
        print(f"{status}: {count}")
    print(f"\nReport saved to: {OUTPUT_FILE}")

    if CATALOGUE_FILE.exists():
        print(
            "\nExisting CSV catalogue was left unchanged. "
            "We will reconcile it with these results after reviewing them."
        )

if __name__ == "__main__":
    main()
