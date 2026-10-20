# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

from unittest.mock import patch

import pytest

from plane.bgtasks.email_notification_task import (
    MAX_EMAIL_RETRIES,
    get_base_api,
    send_email_notification,
    stack_email_notification,
)
from plane.db.models import EmailNotificationLog, Issue, Project, User, Workspace


def make_activity(name="Test issue"):
    return {
        "issue": {
            "id": "00000000-0000-0000-0000-000000000001",
            "name": name,
            "identifier": "TEST",
            "sequence_id": 1,
            "state_name": "Todo",
            "state_group": "unstarted",
        },
        "issue_activity": {
            "id": "00000000-0000-0000-0000-000000000002",
            "verb": "updated",
            "field": "priority",
            "actor": "00000000-0000-0000-0000-000000000003",
            "new_value": "high",
            "old_value": "low",
            "activity_time": "2024-01-01 00:00:00",
        },
    }


@pytest.fixture
def project(db, create_user):
    workspace = Workspace.objects.create(name="Test workspace", owner=create_user, slug="test-workspace")
    return Project.objects.create(name="Test project", identifier="TEST", workspace=workspace, created_by=create_user)


@pytest.fixture
def issue(db, project, create_user):
    return Issue.objects.create(name="Test issue", project=project, workspace=project.workspace, created_by=create_user)


@pytest.fixture
def receiver(db, project, create_user):
    return User.objects.create(email="receiver@plane.so", username="receiver-user", first_name="Receiver")


@pytest.mark.unit
class TestStackEmailNotification:
    """The beat task that turns logs into one email per receiver and issue."""

    def test_marks_attempt_and_only_claims_that_issue(self, issue, project, create_user, receiver):
        other_issue = Issue.objects.create(
            name="Other issue", project=project, workspace=project.workspace, created_by=create_user
        )
        first = EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
        )
        second = EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=other_issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
        )

        with patch("plane.bgtasks.email_notification_task.send_email_notification") as send:
            stack_email_notification()

        assert send.delay.call_count == 2
        # each issue is dispatched with its own logs, never the receiver's full set
        sent_ids = [set(call.kwargs["email_notification_ids"]) for call in send.delay.call_args_list]
        assert {first.id} in sent_ids
        assert {second.id} in sent_ids

        first.refresh_from_db()
        second.refresh_from_db()
        for log in (first, second):
            assert log.processed_at is not None
            assert log.retry_count == 1
            assert log.sent_at is None

    def test_skips_logs_that_exhausted_their_retries(self, issue, create_user, receiver):
        EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
            retry_count=MAX_EMAIL_RETRIES,
        )

        with patch("plane.bgtasks.email_notification_task.send_email_notification") as send:
            stack_email_notification()

        send.delay.assert_not_called()

    def test_skips_logs_that_were_already_sent(self, issue, create_user, receiver):
        from django.utils import timezone

        EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
            sent_at=timezone.now(),
        )

        with patch("plane.bgtasks.email_notification_task.send_email_notification") as send:
            stack_email_notification()

        send.delay.assert_not_called()


@pytest.mark.unit
class TestSendEmailNotification:
    """Delivery: only a confirmed send marks the log, and failures must stay retryable."""

    def test_failure_leaves_the_log_unsent_and_releases_the_lock(self, issue, create_user, receiver):
        log = EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
        )
        with (
            patch("plane.bgtasks.email_notification_task.acquire_lock", return_value=True),
            patch("plane.bgtasks.email_notification_task.release_lock") as release,
            patch("plane.bgtasks.email_notification_task.EmailMultiAlternatives") as message,
        ):
            message.return_value.send.side_effect = Exception("smtp down")
            send_email_notification(
                issue_id=str(issue.id),
                notification_data={},
                receiver_id=str(receiver.id),
                email_notification_ids=[log.id],
            )

        log.refresh_from_db()
        assert log.sent_at is None
        release.assert_called_once()

    def test_duplicate_dispatch_is_skipped_without_marking(self, issue, create_user, receiver):
        log = EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier=issue.id,
            entity_name="issue",
            entity="issue",
            data=make_activity(),
        )
        with (
            patch("plane.bgtasks.email_notification_task.acquire_lock", return_value=False),
            patch("plane.bgtasks.email_notification_task.EmailMultiAlternatives") as message,
        ):
            send_email_notification(
                issue_id=str(issue.id),
                notification_data={},
                receiver_id=str(receiver.id),
                email_notification_ids=[log.id],
            )

        message.assert_not_called()
        log.refresh_from_db()
        assert log.sent_at is None

    def test_missing_issue_drops_the_batch_instead_of_retrying(self, create_user, receiver):
        log = EmailNotificationLog.objects.create(
            triggered_by=create_user,
            receiver=receiver,
            entity_identifier="00000000-0000-0000-0000-0000000000ff",
            entity_name="issue",
            entity="issue",
            data=make_activity(),
        )
        with patch("plane.bgtasks.email_notification_task.acquire_lock", return_value=True):
            send_email_notification(
                issue_id=str(log.entity_identifier),
                notification_data={},
                receiver_id=str(receiver.id),
                email_notification_ids=[log.id],
            )

        log.refresh_from_db()
        assert log.sent_at is None
        assert log.retry_count == MAX_EMAIL_RETRIES


@pytest.mark.unit
class TestGetBaseApi:
    def test_falls_back_to_the_configured_web_url(self, settings):
        settings.WEB_URL = "https://plane.example.com"
        with patch("plane.bgtasks.email_notification_task.redis_instance") as redis:
            redis.return_value.get.return_value = None
            assert get_base_api("00000000-0000-0000-0000-000000000001") == "https://plane.example.com"

    def test_prefers_the_origin_the_client_used(self, settings):
        settings.WEB_URL = "https://plane.example.com"
        with patch("plane.bgtasks.email_notification_task.redis_instance") as redis:
            redis.return_value.get.return_value = b"https://tenant.example.com"
            assert get_base_api("00000000-0000-0000-0000-000000000001") == "https://tenant.example.com"
