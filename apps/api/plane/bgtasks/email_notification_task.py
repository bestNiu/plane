# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

import logging
import re
from datetime import datetime, timedelta

from bs4 import BeautifulSoup

# Third party imports
from celery import shared_task
from django.conf import settings
from django.core.exceptions import ObjectDoesNotExist
from django.core.mail import EmailMultiAlternatives, get_connection
from django.db.models import F, Q
from django.template.loader import render_to_string

# Django imports
from django.utils import timezone

# Module imports
from plane.db.models import EmailNotificationLog, Issue, User
from plane.license.utils.instance_value import get_email_configuration
from plane.settings.redis import redis_instance
from plane.utils.email import attach_brand_logo, generate_plain_text_from_html
from plane.utils.exception_logger import log_exception

# A delivery that never confirms is retried on later beat runs; after this many attempts the
# log is dropped, so a broken SMTP relay cannot build an unbounded retry backlog.
MAX_EMAIL_RETRIES = 3
RETRY_AFTER = timedelta(minutes=5)


def remove_unwanted_characters(input_text):
    # Remove only control characters and potentially problematic characters for email subjects
    processed_text = re.sub(r"[\x00-\x1F\x7F-\x9F]", "", input_text)
    return processed_text


# acquire and delete redis lock
def acquire_lock(lock_id, expire_time=300):
    redis_client = redis_instance()
    """Attempt to acquire a lock with a specified expiration time."""
    return redis_client.set(lock_id, "true", nx=True, ex=expire_time)


def release_lock(lock_id):
    """Release a lock."""
    redis_client = redis_instance()
    redis_client.delete(lock_id)


def get_base_api(issue_id):
    """Absolute URL the email links point at.

    The origin the client used is cached in redis with a short TTL, so a slow or retried batch
    would otherwise be dropped; fall back to the configured web URL instead.
    """
    origin = redis_instance().get(str(issue_id))
    if origin:
        return origin.decode() if isinstance(origin, bytes) else origin
    return settings.WEB_URL or settings.APP_BASE_URL


def drop_email_notifications(email_notification_ids):
    """Stop retrying a batch that can never be delivered."""
    EmailNotificationLog.objects.filter(pk__in=email_notification_ids).update(
        retry_count=MAX_EMAIL_RETRIES, processed_at=timezone.now()
    )


@shared_task
def stack_email_notification():
    now = timezone.now()
    # get all email notifications that still need a send: never confirmed, attempts left,
    # and either untouched or claimed long enough ago for a retry
    email_notifications = list(
        EmailNotificationLog.objects.filter(sent_at__isnull=True, retry_count__lt=MAX_EMAIL_RETRIES)
        .filter(Q(processed_at__isnull=True) | Q(processed_at__lt=now - RETRY_AFTER))
        .order_by("receiver")
        .values()
    )

    # Create the below format for each of the issues
    # {"issue_id" : { "actor_id1": [ { data }, { data } ], "actor_id2": [ { data }, { data } ] }}

    # Convert to unique receivers list
    receivers = list({str(notification.get("receiver_id")) for notification in email_notifications})
    # Loop through all the issues to create the emails
    for receiver_id in receivers:
        # Notification triggered for the receiver
        receiver_notifications = [
            notification for notification in email_notifications if str(notification.get("receiver_id")) == receiver_id
        ]
        # create payload for all issues
        payload = {}
        ids_by_issue = {}
        for receiver_notification in receiver_notifications:
            issue_id = receiver_notification.get("entity_identifier")
            payload.setdefault(issue_id, {}).setdefault(str(receiver_notification.get("triggered_by_id")), []).append(
                receiver_notification.get("data")
            )
            ids_by_issue.setdefault(issue_id, []).append(receiver_notification.get("id"))

        # Create emails for all the issues; each issue only claims its own logs, so one failed
        # issue no longer marks the receiver's other issues as sent.
        for issue_id, notification_data in payload.items():
            email_notification_ids = ids_by_issue[issue_id]
            if issue_id is None:
                drop_email_notifications(email_notification_ids)
                continue
            EmailNotificationLog.objects.filter(pk__in=email_notification_ids).update(
                processed_at=now, retry_count=F("retry_count") + 1
            )
            send_email_notification.delay(
                issue_id=issue_id,
                notification_data=notification_data,
                receiver_id=receiver_id,
                email_notification_ids=email_notification_ids,
            )


def create_payload(notification_data):
    # return format {"actor_id":  { "key": { "old_value": [], "new_value": [] } }}
    data = {}
    for actor_id, changes in notification_data.items():
        for change in changes:
            issue_activity = change.get("issue_activity")
            if issue_activity:  # Ensure issue_activity is not None
                field = issue_activity.get("field")
                old_value = str(issue_activity.get("old_value"))
                new_value = str(issue_activity.get("new_value"))

                # Append old_value if it's not empty and not already in the list
                if old_value:
                    (
                        data.setdefault(actor_id, {})
                        .setdefault(field, {})
                        .setdefault("old_value", [])
                        .append(old_value)
                        if old_value not in data.setdefault(actor_id, {}).setdefault(field, {}).get("old_value", [])
                        else None
                    )

                # Append new_value if it's not empty and not already in the list
                if new_value:
                    (
                        data.setdefault(actor_id, {})
                        .setdefault(field, {})
                        .setdefault("new_value", [])
                        .append(new_value)
                        if new_value not in data.setdefault(actor_id, {}).setdefault(field, {}).get("new_value", [])
                        else None
                    )

                if not data.get("actor_id", {}).get("activity_time", False):
                    data[actor_id]["activity_time"] = str(
                        datetime.fromisoformat(issue_activity.get("activity_time").rstrip("Z")).strftime(
                            "%Y-%m-%d %H:%M:%S"
                        )
                    )

    return data


def process_mention(mention_component):
    soup = BeautifulSoup(mention_component, "html.parser")
    mentions = soup.find_all("mention-component")
    for mention in mentions:
        user_id = mention["entity_identifier"]
        user = User.objects.get(pk=user_id)
        user_name = user.display_name
        highlighted_name = f"@{user_name}"
        mention.replace_with(highlighted_name)
    return str(soup)


def process_html_content(content):
    if content is None:
        return None
    processed_content_list = []
    for html_content in content:
        processed_content = process_mention(html_content)
        processed_content_list.append(processed_content)
    return processed_content_list


@shared_task
def send_email_notification(issue_id, notification_data, receiver_id, email_notification_ids):
    # Convert UUIDs to a sorted, concatenated string
    sorted_ids = sorted(email_notification_ids)
    ids_str = "_".join(str(id) for id in sorted_ids)
    lock_id = f"send_email_notif_{issue_id}_{receiver_id}_{ids_str}"

    # acquire the lock for sending emails
    if not acquire_lock(lock_id=lock_id):
        logging.getLogger("plane.worker").info("Duplicate email received skipping")
        return

    try:
        base_api = get_base_api(issue_id)
        if not base_api:
            logging.getLogger("plane.worker").info("No base url configured, skipping email")
            drop_email_notifications(email_notification_ids)
            return

        data = create_payload(notification_data=notification_data)

        # Get email configurations
        (
            EMAIL_HOST,
            EMAIL_HOST_USER,
            EMAIL_HOST_PASSWORD,
            EMAIL_PORT,
            EMAIL_USE_TLS,
            EMAIL_USE_SSL,
            EMAIL_FROM,
        ) = get_email_configuration()

        receiver = User.objects.get(pk=receiver_id)
        issue = Issue.objects.get(pk=issue_id)
        template_data = []
        total_changes = 0
        comments = []
        actors_involved = []
        for actor_id, changes in data.items():
            actor = User.objects.get(pk=actor_id)
            total_changes = total_changes + len(changes)
            comment = changes.pop("comment", False)
            mention = changes.pop("mention", False)
            actors_involved.append(actor_id)
            if comment:
                comments.append(
                    {
                        "actor_comments": comment,
                        "actor_detail": {
                            "avatar_url": f"{base_api}{actor.avatar_url}",
                            "first_name": actor.first_name,
                            "last_name": actor.last_name,
                        },
                    }
                )
            if mention:
                mention["new_value"] = process_html_content(mention.get("new_value"))
                mention["old_value"] = process_html_content(mention.get("old_value"))
                comments.append(
                    {
                        "actor_comments": mention,
                        "actor_detail": {
                            "avatar_url": f"{base_api}{actor.avatar_url}",
                            "first_name": actor.first_name,
                            "last_name": actor.last_name,
                        },
                    }
                )
            activity_time = changes.pop("activity_time")
            # Parse the input string into a datetime object
            formatted_time = datetime.strptime(activity_time, "%Y-%m-%d %H:%M:%S").strftime("%H:%M %p")

            if changes:
                template_data.append(
                    {
                        "actor_detail": {
                            "avatar_url": f"{base_api}{actor.avatar_url}",
                            "first_name": actor.first_name,
                            "last_name": actor.last_name,
                        },
                        "changes": changes,
                        "issue_details": {
                            "name": issue.name,
                            "identifier": f"{issue.project.identifier}-{issue.sequence_id}",
                        },
                        "activity_time": str(formatted_time),
                    }
                )

        summary = "Updates were made to the issue by"

        # Send the mail
        subject = f"{issue.project.identifier}-{issue.sequence_id} {remove_unwanted_characters(issue.name)}"
        context = {
            "data": template_data,
            "summary": summary,
            "actors_involved": len(set(actors_involved)),
            "issue": {
                "issue_identifier": f"{str(issue.project.identifier)}-{str(issue.sequence_id)}",
                "name": issue.name,
                "issue_url": f"{base_api}/{str(issue.project.workspace.slug)}/projects/{str(issue.project.id)}/issues/{str(issue.id)}",  # noqa: E501
            },
            "receiver": {"email": receiver.email},
            "issue_url": f"{base_api}/{str(issue.project.workspace.slug)}/projects/{str(issue.project.id)}/issues/{str(issue.id)}",  # noqa: E501
            "project_url": f"{base_api}/{str(issue.project.workspace.slug)}/projects/{str(issue.project.id)}/issues/",  # noqa: E501
            "workspace": str(issue.project.workspace.slug),
            "project": str(issue.project.name),
            "user_preference": f"{base_api}/{str(issue.project.workspace.slug)}/settings/account/notifications/",
            "comments": comments,
            "entity_type": "issue",
        }
        html_content = render_to_string("emails/notifications/issue-updates.html", context)
        text_content = generate_plain_text_from_html(html_content)

        connection = get_connection(
            host=EMAIL_HOST,
            port=int(EMAIL_PORT),
            username=EMAIL_HOST_USER,
            password=EMAIL_HOST_PASSWORD,
            use_tls=EMAIL_USE_TLS == "1",
            use_ssl=EMAIL_USE_SSL == "1",
        )

        msg = EmailMultiAlternatives(
            subject=subject,
            body=text_content,
            from_email=EMAIL_FROM,
            to=[receiver.email],
            connection=connection,
        )
        msg.attach_alternative(html_content, "text/html")
        attach_brand_logo(msg)
        msg.send()
        logging.getLogger("plane.worker").info("Email Sent Successfully")

        # Update the logs
        EmailNotificationLog.objects.filter(pk__in=email_notification_ids).update(sent_at=timezone.now())
    except ObjectDoesNotExist:
        # The issue or receiver is gone: nothing can ever be delivered, stop retrying.
        drop_email_notifications(email_notification_ids)
    except Exception as e:
        # Leave the log unconfirmed so the next beat run retries it, up to MAX_EMAIL_RETRIES.
        log_exception(e)
    finally:
        release_lock(lock_id=lock_id)
