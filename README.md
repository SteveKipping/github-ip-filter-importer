# GitHub Organisation IP Allow List Importer

Imports IP addresses / CIDR ranges from a CSV file into a GitHub organisation's IP allow list
(the **Security** settings page at `https://github.com/organizations/<ORG>/settings/security`).

Uses the [GitHub GraphQL API](https://docs.github.com/en/graphql) — no extra dependencies
beyond `requests`.

---

## Requirements

- Python 3.10+
- A GitHub **Personal Access Token** (classic or fine-grained) with the
  `admin:org` scope (needed to manage the IP allow list)

### Set up the virtual environment

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

---

## CSV format

The input file must have a header row with at least two columns: `ip` and `name`.

```csv
ip,name
192.168.1.0/24,Office Network
10.0.0.1/32,VPN Gateway
203.0.113.42/32,Developer Home
198.51.100.0/28,CI/CD Runners
```

- **`ip`** — an IPv4/IPv6 address or CIDR range (e.g. `10.0.0.0/8`, `2001:db8::/32`)
- **`name`** — a human-readable label shown in the GitHub UI

---

## Usage

```bash
# 1. Set your token
export GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxx

# 2. Run a dry-run first to validate without making changes
python3 import_ip_allowlist.py --org DNDE-AEC --file ips.csv --dry-run

# 3. Import for real (active entries, skipping duplicates)
python3 import_ip_allowlist.py --org DNDE-AEC --file ips.csv

# 4. Import as inactive (disabled) entries
python3 import_ip_allowlist.py --org DNDE-AEC --file ips.csv --inactive

# 5. Re-import everything (don't skip entries that already exist)
python3 import_ip_allowlist.py --org DNDE-AEC --file ips.csv --no-skip-existing
```

### All options

| Flag | Default | Description |
|---|---|---|
| `--org` | *(required)* | GitHub organisation login |
| `--file` | *(required)* | Path to the CSV file |
| `--active` / `--inactive` | active | Whether new entries are enabled or disabled |
| `--skip-existing` / `--no-skip-existing` | skip | Skip IPs already on the allow list |
| `--dry-run` | off | Print what would be added without calling the API |

---

## Token scopes

| Token type | Required scope |
|---|---|
| Classic PAT | `admin:org` |
| Fine-grained PAT | `Organization IP allow list` → **Read and write** |

---

## Notes

- The script adds a small delay (300 ms) between API calls to avoid GitHub's secondary rate limits.
- Validation of IP/CIDR syntax is done locally before any API calls are made.
- The script exits with code `0` on full success and `1` if any entries failed to import.
