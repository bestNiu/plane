/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

export function MaintenanceMessage() {
  return (
    <>
      <div className="flex flex-col gap-2.5">
        <h1 className="text-left text-18 font-semibold text-primary">
          &#x1F6A7; Looks like UnionclinTask didn&apos;t start up correctly!
        </h1>
        <span className="text-left text-14 font-medium text-secondary">
          Some services might have failed to start. Please check your container logs to identify and resolve the issue.
        </span>
      </div>
    </>
  );
}
