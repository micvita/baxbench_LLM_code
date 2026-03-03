#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

    # Make `python manage.py runserver` bind to 0.0.0.0:5000 by default.
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver":
        if len(sys.argv) == 2:
            sys.argv.append("0.0.0.0:5000")

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()