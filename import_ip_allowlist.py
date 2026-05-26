#!/usr/bin/env .venv/bin/python3
"""
Import IP addresses from a CSV file into a GitHub organisation IP allow list.

Usage:
    Copy .env.example to .env and fill in GITHUB_TOKEN, then:
    python3 import_ip_allowlist.py --org DNDE-AEC --file ips.csv

CSV format (header row required):
    ip,name
    192.168.1.0/24,Office Network
    10.0.0.1/32,VPN Gateway
"""

import argparse
import csv
import ipaddress
import os
import sys
import time
from typing import Optional

import requests

# Load .env file — use python-dotenv if available, otherwise parse it directly
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    _env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.isfile(_env_path):
        with open(_env_path) as _f:
            for _line in _f:
                _line = _line.strip()
                if _line and not _line.startswith("#") and "=" in _line:
                    _k, _, _v = _line.partition("=")
                    os.environ.setdefault(_k.strip(), _v.strip())

GITHUB_GRAPHQL_URL = "https://api.github.com/graphql"


def graphql_request(token: str, query: str, variables: dict) -> dict:
    """Execute a GitHub GraphQL request and return the parsed response."""
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    response = requests.post(
        GITHUB_GRAPHQL_URL,
        json={"query": query, "variables": variables},
        headers=headers,
        timeout=30,
    )
    response.raise_for_status()
    data = response.json()
    if "errors" in data:
        errors = "; ".join(e["message"] for e in data["errors"])
        raise RuntimeError(f"GraphQL error: {errors}")
    return data


def get_org_node_id(token: str, org: str) -> str:
    """Resolve an organisation login to its GraphQL node ID."""
    query = """
    query($login: String!) {
      organization(login: $login) {
        id
        name
      }
    }
    """
    data = graphql_request(token, query, {"login": org})
    org_data = data.get("data", {}).get("organization")
    if not org_data:
        raise RuntimeError(
            f"Organisation '{org}' not found or the token lacks the required scope."
        )
    return org_data["id"]


def get_existing_entries(token: str, org: str) -> set[str]:
    """Return the set of IP/CIDR values already on the allow list."""
    query = """
    query($login: String!, $after: String) {
      organization(login: $login) {
        ipAllowListEntries(first: 100, after: $after) {
          pageInfo { hasNextPage endCursor }
          nodes { allowListValue }
        }
      }
    }
    """
    existing: set[str] = set()
    after: Optional[str] = None

    while True:
        data = graphql_request(token, query, {"login": org, "after": after})
        entries = data["data"]["organization"]["ipAllowListEntries"]
        for node in entries["nodes"]:
            existing.add(node["allowListValue"])
        if entries["pageInfo"]["hasNextPage"]:
            after = entries["pageInfo"]["endCursor"]
        else:
            break

    return existing


CREATE_MUTATION = """
mutation($ownerId: ID!, $allowListValue: String!, $name: String!, $isActive: Boolean!) {
  createIpAllowListEntry(input: {
    ownerId: $ownerId,
    allowListValue: $allowListValue,
    name: $name,
    isActive: $isActive
  }) {
    ipAllowListEntry {
      id
      allowListValue
      name
      isActive
    }
  }
}
"""


def validate_ip_or_cidr(value: str) -> bool:
    """Return True if value is a valid IP address or CIDR range."""
    try:
        ipaddress.ip_network(value, strict=False)
        return True
    except ValueError:
        return False


def load_csv(path: str) -> list[dict]:
    """Read the CSV file and return a list of dicts with 'ip' and 'name' keys."""
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        required = {"ip", "name"}
        if reader.fieldnames is None or not required.issubset(
            {f.strip().lower() for f in reader.fieldnames}
        ):
            raise ValueError(
                f"CSV must have 'ip' and 'name' columns. Found: {reader.fieldnames}"
            )
        for lineno, row in enumerate(reader, start=2):
            ip = row.get("ip", "").strip()
            name = row.get("name", "").strip()
            if not ip:
                print(f"  [WARN] Line {lineno}: empty IP, skipping.")
                continue
            rows.append({"ip": ip, "name": name, "lineno": lineno})
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Import IP addresses into a GitHub organisation IP allow list."
    )
    parser.add_argument(
        "--org", required=True, help="GitHub organisation login (e.g. DNDE-AEC)"
    )
    parser.add_argument(
        "--file", required=True, help="Path to the CSV file containing IPs to import"
    )
    parser.add_argument(
        "--active",
        action="store_true",
        default=True,
        help="Add entries as active (default: true)",
    )
    parser.add_argument(
        "--inactive",
        dest="active",
        action="store_false",
        help="Add entries as inactive",
    )
    parser.add_argument(
        "--skip-existing",
        action="store_true",
        default=True,
        help="Skip entries that already exist on the allow list (default: true)",
    )
    parser.add_argument(
        "--no-skip-existing",
        dest="skip_existing",
        action="store_false",
        help="Attempt to add all entries even if they already exist",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate and print what would be added without making any changes",
    )
    args = parser.parse_args()

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if not token:
        print("ERROR: GITHUB_TOKEN environment variable is not set.", file=sys.stderr)
        return 1

    # --- Load and validate CSV ---
    print(f"Reading '{args.file}'...")
    try:
        rows = load_csv(args.file)
    except (OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    if not rows:
        print("No rows found in CSV. Nothing to import.")
        return 0

    invalid = [r for r in rows if not validate_ip_or_cidr(r["ip"])]
    if invalid:
        print("ERROR: The following entries are not valid IP addresses or CIDR ranges:")
        for r in invalid:
            print(f"  Line {r['lineno']}: {r['ip']!r}")
        return 1

    print(f"Found {len(rows)} entr{'y' if len(rows) == 1 else 'ies'} to process.")

    if args.dry_run:
        print("\n[DRY RUN] Would add the following entries:")
        for r in rows:
            state = "active" if args.active else "inactive"
            print(f"  {r['ip']:<25}  {r['name']!r}  ({state})")
        return 0

    # --- Resolve org node ID ---
    print(f"\nResolving organisation '{args.org}'...")
    try:
        org_id = get_org_node_id(token, args.org)
        print(f"  Node ID: {org_id}")
    except (requests.HTTPError, RuntimeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    # --- Fetch existing entries ---
    existing: set[str] = set()
    if args.skip_existing:
        print("Fetching existing IP allow list entries...")
        try:
            existing = get_existing_entries(token, args.org)
            print(f"  {len(existing)} existing entr{'y' if len(existing) == 1 else 'ies'} found.")
        except (requests.HTTPError, RuntimeError) as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1

    # --- Import ---
    print("\nImporting entries...")
    added = skipped = failed = 0

    for r in rows:
        ip, name = r["ip"], r["name"]

        if args.skip_existing and ip in existing:
            print(f"  [SKIP]  {ip:<25}  already exists")
            skipped += 1
            continue

        try:
            graphql_request(
                token,
                CREATE_MUTATION,
                {
                    "ownerId": org_id,
                    "allowListValue": ip,
                    "name": name,
                    "isActive": args.active,
                },
            )
            state = "active" if args.active else "inactive"
            print(f"  [OK]    {ip:<25}  {name!r}  ({state})")
            added += 1
        except (requests.HTTPError, RuntimeError) as exc:
            print(f"  [FAIL]  {ip:<25}  {exc}")
            failed += 1

        # Respect GitHub's secondary rate limits (avoid bursting)
        time.sleep(0.3)

    print(
        f"\nDone. Added: {added}, Skipped: {skipped}, Failed: {failed}"
    )
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
