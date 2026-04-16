import shutil
import sqlite3
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from requests.cookies import RequestsCookieJar


def read_firefox_cookies(profile_dir: Path) -> RequestsCookieJar:
    cookies_db = profile_dir / "cookies.sqlite"

    if not cookies_db.exists():
        raise FileNotFoundError(cookies_db)

    jar = RequestsCookieJar()

    # Copy cookies.sqlite and WAL file to temp location.
    with tempfile.TemporaryDirectory() as tmpdir:
        tmp_cookies = Path(tmpdir) / "cookies.sqlite"
        shutil.copy(cookies_db, tmp_cookies)

        # Copy WAL file if it exists
        if (wal_file := profile_dir / "cookies.sqlite-wal").exists():
            shutil.copy(wal_file, tmp_cookies.with_suffix(".sqlite-wal"))

        # Connect to the copied database
        conn = sqlite3.connect(str(tmp_cookies))
        cursor = conn.cursor()

        now = datetime.now(timezone.utc).timestamp()

        # Directly query the columns we need (works for modern Firefox)
        cursor.execute("""
            SELECT name, value, host, path, expiry, isSecure
            FROM moz_cookies
        """)

        for row in cursor.fetchall():
            name, value, host, path, expiry, is_secure = row

            # Skip session cookies (expiry <= 0)
            if expiry is None or expiry <= 0:
                continue

            # Skip expired cookies
            # Firefox stores expiry in milliseconds since Unix epoch
            try:
                exp_ts = expiry / 1e3  # Convert to seconds
                if exp_ts < now:
                    continue
            except (OSError, OverflowError, ValueError):
                continue

            # Add cookie to jar
            # domain should not have leading dot for requests
            domain = host.lstrip(".") if host else ""
            cookie_path = path if path else "/"

            jar.set(  # type: ignore[reportUnknownMemberType]
                name=name,
                value=value,
                domain=domain,
                path=cookie_path,
                secure=bool(is_secure),
            )

        conn.close()

    return jar
