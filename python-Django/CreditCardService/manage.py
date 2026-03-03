#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

    # Default runserver bind/port to localhost:3000 if user didn't specify.
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver":
        has_addrport = any(
            arg.startswith(("0.0.0.0:", "127.0.0.1:", "localhost:", ":")) or ":" in arg
            for arg in sys.argv[2:]
        )
        if not has_addrport:
            sys.argv.insert(2, "localhost:3000")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()