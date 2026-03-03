#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

    # Ensure: `python manage.py runserver` starts on 0.0.0.0:5000
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver":
        # If no addr:port provided, default to 0.0.0.0:5000
        has_addrport = any(
            (":" in arg) or (arg.count(".") == 3) for arg in sys.argv[2:]
        )
        if not has_addrport:
            sys.argv.append("0.0.0.0:5000")

    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and available on your PYTHONPATH?"
        ) from exc

    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()