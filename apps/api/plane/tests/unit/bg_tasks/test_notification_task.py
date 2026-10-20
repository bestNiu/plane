# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import json
import uuid

import pytest

from plane.bgtasks.notification_task import notifications
from plane.db.models import (
    EmailNotificationLog,
    Issue,
    IssueAssignee,
    IssueSubscriber,
    Notification,
    Project,
    ProjectMember,
    State,
    User,
    Workspace,
)


def make_user(email, first_name, username):
    return User.objects.create(email=email, username=username, first_name=first_name)


@pytest.fixture
def project(db, create_user):
    workspace = Workspace.objects.create(name="W", owner=create_user, slug="w")
    project = Project.objects.create(name="P", identifier="P", workspace=workspace, created_by=create_user)
    # the notification payload reads issue.state, so every issue needs one
    State.objects.create(
        name="Todo", color="#000000", group="unstarted", default=True, project=project, workspace=workspace
    )
    return project


def add_member(project, user, role=15):
    ProjectMember.objects.create(project=project, member=user, role=role, is_active=True)


def serialized_activity(issue, actor, field="assignees", new_value="Assignee"):
    """Shape produced by IssueActivitySerializer and consumed by the notification task."""
    activity_id = str(uuid.uuid4())
    return {
        "id": activity_id,
        "issue_detail": {"id": str(issue.id), "name": issue.name, "sequence_id": issue.sequence_id},
        "field": field,
        "verb": "updated",
        "actor_id": str(actor.id),
        "actor_detail": {"id": str(actor.id), "display_name": actor.display_name},
        "new_value": new_value,
        "old_value": "",
        "old_identifier": None,
        "new_identifier": None,
        "issue_comment": None,
        # the in-app notification row uses the activity comment as its title
        "comment": "added assignee ",
        "created_at": "2024-01-01T00:00:00.000000Z",
    }


def mention_html(user):
    return f'<p><mention-component entity_identifier="{user.id}" entity_name="user_mention"></mention-component></p>'


@pytest.mark.unit
class TestMentionNotifications:
    """A mention must notify the mentioned user only -- never a bystander or the assignee."""

    def test_mention_email_goes_to_the_mentioned_user(self, db, project, create_user):
        actor = create_user  # the member who creates the work item
        assignee = make_user("assignee@plane.so", "Assignee", "assignee-user")
        mentioned = make_user("mentioned@plane.so", "Mentioned", "mentioned-user")
        bystander = make_user("bystander@plane.so", "Bystander", "bystander-user")
        for member in (actor, assignee, mentioned, bystander):
            add_member(project, member)

        issue = Issue.objects.create(
            name="Issue with mention",
            project=project,
            workspace=project.workspace,
            created_by=actor,
            state=State.objects.get(project=project),
        )
        # what track_assignees does before the notification task runs
        IssueAssignee.objects.create(issue=issue, project=project, workspace=project.workspace, assignee=assignee)
        IssueSubscriber.objects.create(issue=issue, project=project, workspace=project.workspace, subscriber=assignee)
        IssueSubscriber.objects.create(issue=issue, project=project, workspace=project.workspace, subscriber=actor)

        notifications(
            type="issue.activity.created",
            issue_id=str(issue.id),
            project_id=str(project.id),
            actor_id=str(actor.id),
            subscriber=False,
            issue_activities_created=json.dumps([serialized_activity(issue, actor)]),
            requested_data=json.dumps({"id": str(issue.id), "description_html": mention_html(mentioned)}),
            current_instance=None,
        )

        email_receivers = {log.receiver.email for log in EmailNotificationLog.objects.all()}
        assert "mentioned@plane.so" in email_receivers
        assert "bystander@plane.so" not in email_receivers

        in_app = {(n.receiver.email, n.sender) for n in Notification.objects.all()}
        assert ("mentioned@plane.so", "in_app:issue_activities:mentioned") in in_app
        assert ("assignee@plane.so", "in_app:issue_activities:assigned") in in_app
        assert all(receiver != "bystander@plane.so" for receiver, _ in in_app)

        # the mentioned member is subscribed for further updates
        assert IssueSubscriber.objects.filter(issue=issue, subscriber=mentioned).exists()

    def test_assignee_without_mention_is_not_sent_a_mention_email(self, db, project, create_user):
        actor = create_user
        assignee = make_user("assignee2@plane.so", "Assignee", "assignee-user-2")
        for member in (actor, assignee):
            add_member(project, member)

        issue = Issue.objects.create(
            name="Plain issue",
            project=project,
            workspace=project.workspace,
            created_by=actor,
            state=State.objects.get(project=project),
        )
        IssueAssignee.objects.create(issue=issue, project=project, workspace=project.workspace, assignee=assignee)
        IssueSubscriber.objects.create(issue=issue, project=project, workspace=project.workspace, subscriber=assignee)

        notifications(
            type="issue.activity.created",
            issue_id=str(issue.id),
            project_id=str(project.id),
            actor_id=str(actor.id),
            subscriber=False,
            issue_activities_created=json.dumps([serialized_activity(issue, actor)]),
            requested_data=json.dumps({"id": str(issue.id), "description_html": "<p>no mention</p>"}),
            current_instance=None,
        )

        fields = [log.data["issue_activity"]["field"] for log in EmailNotificationLog.objects.all()]
        assert "mention" not in fields
        assert fields == ["assignees"]
