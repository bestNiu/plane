# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import time
from django.db import connections, DEFAULT_DB_ALIAS
from django.db.utils import InterfaceError, OperationalError
from django.core.management import BaseCommand


class Command(BaseCommand):
    """Django command to pause execution until db is available"""

    def add_arguments(self, parser):
        parser.add_argument("--retries", type=int, default=30, help="attempts before giving up")
        parser.add_argument("--delay", type=float, default=2.0, help="seconds between attempts")

    def handle(self, *args, **options):
        retries = options["retries"]
        delay = options["delay"]

        self.stdout.write("Waiting for database...")
        connection = connections[DEFAULT_DB_ALIAS]
        for attempt in range(1, retries + 1):
            try:
                # connections[alias] only returns a lazy wrapper: it is always
                # truthy and never raises, so the old `while not db_conn` loop
                # reported success without ever opening a connection. A real
                # query is what actually probes reachability.
                with connection.cursor() as cursor:
                    cursor.execute("SELECT 1")
            except (OperationalError, InterfaceError) as error:
                if attempt >= retries:
                    self.stderr.write(f"Database still unavailable after {attempt} attempts: {error}")
                    raise
                self.stdout.write(f"Database unavailable (attempt {attempt}/{retries}), retrying in {delay}s...")
                connection.close()
                time.sleep(delay)
            else:
                self.stdout.write(self.style.SUCCESS("Database available!"))
                return

