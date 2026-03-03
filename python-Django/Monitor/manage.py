#!/usr/bin/env python
import os
import sys


def main():
    os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mysite.settings")

    # Ensure `python manage.py runserver` binds to localhost:3000 by default.
    # - If user provides an addr:port explicitly, we respect it.
    if len(sys.argv) >= 2 and sys.argv[1] == "runserver":
        # argv forms:
        #   ["manage.py", "runserver"]
        #   ["manage.py", "runserver", "8001"]
        #   ["manage.py", "runserver", "0.0.0.0:8001"]
        if len(sys.argv) == 2:
            sys.argv.append("localhost:3000")

    from django.core.management import execute_from_command_line
    execute_from_command_line(sys.argv)


if __name__ == "__main__":
    main()