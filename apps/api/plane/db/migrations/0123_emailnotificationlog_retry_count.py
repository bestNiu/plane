# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("db", "0122_alter_draftissue_assignees_alter_issue_assignees_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="emailnotificationlog",
            name="retry_count",
            # pi-lens-ignore: reportArgumentType
            field=models.PositiveSmallIntegerField(default=0),
        ),
    ]
