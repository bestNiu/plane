/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { observer } from "mobx-react";
import { Tooltip } from "@makeplane/propel/components/tooltip";
import packageJson from "package.json";
// hooks
import { usePlatformOS } from "@/hooks/use-platform-os";

export const WorkspaceEditionBadge = observer(function WorkspaceEditionBadge() {
  const { isMobile } = usePlatformOS();

  return (
    <Tooltip label={`Version: v${packageJson.version}`} disabled={isMobile}>
      <span className="px-2 py-1 text-13 font-medium text-secondary">Community</span>
    </Tooltip>
  );
});
