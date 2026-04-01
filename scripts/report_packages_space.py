#!/usr/bin/env python3
"""
Analyze disk space usage by APT and PIP packages.
"""

import os
import subprocess
import sys


def run_command(cmd, timeout=30):
    """Execute shell command and return output."""
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        return result.stdout, result.returncode
    except subprocess.TimeoutExpired:
        return "", 1
    except Exception as e:
        return str(e), 1


def format_size(size_kb):
    """Format KB to human readable string."""
    if size_kb >= 1024 * 1024:
        return f"{size_kb / (1024*1024):.2f} GB"
    elif size_kb >= 1024:
        return f"{size_kb / 1024:.2f} MB"
    else:
        return f"{size_kb} KB"


def get_directory_size(path):
    """Calculate total size of a directory in KB."""
    total = 0
    try:
        for dirpath, dirnames, filenames in os.walk(path):
            for filename in filenames:
                filepath = os.path.join(dirpath, filename)
                try:
                    total += os.path.getsize(filepath)
                except (OSError, IOError):
                    pass
    except (OSError, IOError):
        pass
    return total // 1024  # Convert to KB


def get_apt_packages():
    """Query APT packages and display top 20 by size."""
    print("=" * 61)
    print("APT PACKAGES")
    print("=" * 61)

    # Get installed packages with their sizes
    stdout, _ = run_command("dpkg-query -W -f='${Package} ${Installed-Size}\n' 2>/dev/null")

    packages = []
    total_size = 0

    if stdout:
        for line in stdout.strip().split("\n"):
            parts = line.split()
            if len(parts) >= 2:
                name = parts[0]
                try:
                    size_kb = int(parts[1])
                    packages.append((name, size_kb))
                    total_size += size_kb
                except ValueError:
                    pass

    # Sort by size (largest first)
    packages.sort(key=lambda x: x[1], reverse=True)

    print(f"\nTotal packages: {len(packages)}")
    print(f"Total disk usage: {format_size(total_size)}")

    # Show top 20 largest packages
    print(f"\n{'Package Name':<50} {'Size':>10}")
    print("-" * 61)
    for name, size_kb in packages[:20]:
        print(f"{name:<50} {format_size(size_kb):>10}")

    # Show smaller packages count
    if len(packages) > 30:
        print(f"\n... and {len(packages) - 20} smaller packages")

    return packages, total_size


def get_package_size_from_record(dist_info_path):
    """Extract package size from dist-info/RECORD file (in bytes)."""
    total_size = 0
    record_path = os.path.join(dist_info_path, "RECORD")

    if not os.path.isfile(record_path):
        return None  # Cannot determine size from RECORD

    try:
        with open(record_path, "r") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue

                # RECORD format: path,sha256=...,size (or path,, for pycache/empty)
                # Parse each line: find the last numeric field (size)
                parts = line.split(",")

                # Find size: scan parts reversed, first valid int is size
                size = None
                for part in reversed(parts):
                    if part:  # Skip empty strings
                        try:
                            size = int(part)
                            break  # Found the size
                        except ValueError:
                            pass  # Not a number, keep looking

                if size is not None:
                    total_size += size
    except (OSError, IOError):
        return None

    return total_size // 1024  # Convert to KB


def get_pip_packages_from_record():
    """Query PIP packages from dist-info/RECORD files."""
    print("\n" + "=" * 61)
    print("PIP PACKAGES")
    print("=" * 61)

    import site

    # Find site-packages directories (system, user, and sys.path)
    site_packages_dirs = set()

    if hasattr(site, "getsitepackages"):
        site_packages_dirs.update(site.getsitepackages())

    user_site = site.getusersitepackages()
    if user_site:
        site_packages_dirs.add(user_site)

    for p in sys.path:
        if "site-packages" in p and os.path.isdir(p):
            site_packages_dirs.add(p)

    # Scan dist-info directories: try RECORD file, fallback to directory scan
    all_packages = {}  # name -> size_kb

    for sp_dir in sorted(site_packages_dirs):
        if not os.path.isdir(sp_dir):
            continue

        dir_size = get_directory_size(sp_dir)
        print(f"\nDirectory: {sp_dir} ({format_size(dir_size)})")

        try:
            items = os.listdir(sp_dir)

            for item in items:
                item_path = os.path.join(sp_dir, item)

                # Look for dist-info directories
                if os.path.isdir(item_path) and ".dist-info" in item:
                    # Try to get size from RECORD file
                    size_kb = get_package_size_from_record(item_path)

                    if size_kb is None:
                        # Fallback to directory scanning if RECORD not available
                        size_kb = get_directory_size(item_path)

                    # Extract package name from name-version.dist-info
                    pkg_name = item.split("-")[0]

                    if pkg_name not in all_packages:
                        all_packages[pkg_name] = size_kb
                    else:
                        # Keep larger size if package already exists
                        all_packages[pkg_name] = max(all_packages[pkg_name], size_kb)

        except PermissionError:
            print(f"Permission denied")

    # Convert to list and sort by size
    packages = [(name, size) for name, size in all_packages.items()]
    packages.sort(key=lambda x: x[1], reverse=True)

    total_size = sum(size for _, size in packages)

    print(f"\nTotal pip packages: {len(packages)}")
    print(f"Total disk usage: {format_size(total_size)}")

    # Show top 20 largest packages
    print(f"\nTop 20 packages:")
    print(f"\n{'Package Name':<50} {'Size':>10}")
    print("-" * 61)
    for name, size_kb in packages[:20]:
        print(f"{name:<50} {format_size(size_kb):>10}")

    # Show smaller packages count
    if len(packages) > 20:
        remaining = packages[20:]
        remaining_total = sum(s for _, s in remaining)
        print(f"\n... and {len(remaining)} smaller packages ({format_size(remaining_total)})")

    return packages, total_size


def main():
    """Run package disk space analysis."""
    print("\n" + "=" * 61)
    print("       PACKAGE DISK SPACE ANALYZER")
    print("       (APT & PIP packages)")
    print("=" * 61)

    # Analyze APT packages
    apt_packages, apt_total = get_apt_packages()

    # Analyze PIP packages
    pip_packages, pip_total = get_pip_packages_from_record()

    # Summary
    print("\n" + "=" * 61)
    print("SUMMARY")
    print("=" * 61)
    print(f"{'APT packages:':<33} {len(apt_packages):>6} packages  {format_size(apt_total):>10}")
    print(f"{'PIP packages:':<33} {len(pip_packages):>6} packages  {format_size(pip_total):>10}")
    print("-" * 61)
    print(f"{'TOTAL':<33} {len(apt_packages) + len(pip_packages):>6} packages  {format_size(apt_total + pip_total):>10}")
    print("=" * 61)
    print()


if __name__ == "__main__":
    main()
