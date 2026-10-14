# Copyright (c) 2023-present Plane Software, Inc. and contributors
# SPDX-License-Identifier: AGPL-3.0-only
# See the LICENSE file for details.

# Python imports
import html
import os
import re
from email.mime.image import MIMEImage

# Django imports
from django.utils.html import strip_tags

# Inline brand logo. Referenced from templates as `cid:unionclintask-logo` so the
# logo renders without depending on any external / third-party image host.
BRAND_LOGO_CID = "unionclintask-logo"
# Resolved relative to this file (apps/api/plane/utils/) so it works without
# staticfiles finders or a prior `collectstatic` run.
_PACKAGE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BRAND_LOGO_PATH = os.path.join(_PACKAGE_DIR, "static", "logos", "unionclintask-logo.png")


def get_brand_logo_path():
    """Return the absolute filesystem path of the inline brand logo."""
    return BRAND_LOGO_PATH


def attach_brand_logo(msg):
    """Attach the UnionclinTask logo to ``msg`` as an inline CID image.

    Templates reference it via ``<img src="cid:unionclintask-logo">``. The outer
    MIME subtype is switched to ``related`` so mail clients resolve the CID.
    Delivery is never blocked if the logo cannot be read.
    """
    try:
        path = get_brand_logo_path()
        with open(path, "rb") as logo_file:
            data = logo_file.read()
        msg.mixed_subtype = "related"
        image = MIMEImage(data, name=os.path.basename(path))
        image.add_header("Content-ID", f"<{BRAND_LOGO_CID}>")
        image.add_header("Content-Disposition", "inline", filename=os.path.basename(path))
        msg.attach(image)
    except Exception:
        pass


def generate_plain_text_from_html(html_content):
    """
    Generate clean plain text from HTML email template.
    Removes all HTML tags, CSS styles, and excessive whitespace, and decodes
    HTML character references so the text/plain part reads as literal text.

    Args:
        html_content (str): The HTML content to convert to plain text

    Returns:
        str: Clean plain text without HTML tags, styles, excessive whitespace,
            or HTML character references
    """
    # Remove style tags and their content
    html_content = re.sub(r"<style[^>]*>.*?</style>", "", html_content, flags=re.DOTALL | re.IGNORECASE)

    # Strip HTML tags, then decode character references. `strip_tags` re-emits
    # entity refs verbatim, so URLs escaped for the HTML part would otherwise
    # reach the text/plain part as `?a=1&amp;b=2` and break when pasted.
    text_content = html.unescape(strip_tags(html_content))

    # Remove excessive empty lines
    text_content = re.sub(r"\n\s*\n\s*\n+", "\n\n", text_content)

    # Ensure there's a leading and trailing whitespace
    text_content = "\n\n" + text_content.lstrip().rstrip() + "\n\n"

    return text_content
