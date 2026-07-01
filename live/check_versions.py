import importlib.metadata
pkgs = ['py-clob-client','py-order-utils','py-builder-signing-sdk','poly-eip712-structs']
for p in pkgs:
    try:
        v = importlib.metadata.version(p)
        print(f"{p}: {v}")
    except:
        print(f"{p}: NOT INSTALLED")

# Check if newer versions available on PyPI
import urllib.request, json

for p in pkgs:
    try:
        req = urllib.request.Request(f"https://pypi.org/pypi/{p}/json", headers={"User-Agent": "curl"})
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            latest = data["info"]["version"]
            print(f"  PyPI latest: {latest}")
    except Exception as e:
        print(f"  PyPI check failed: {e}")

# Check upgrade
print("\n--- Upgrading all related packages ---")
import subprocess, sys
result = subprocess.run(
    [sys.executable, "-m", "pip", "install", "--upgrade", "--break-system-packages"] + pkgs,
    capture_output=True, text=True
)
print(result.stdout[-500:] if result.stdout else result.stderr[-500:])
