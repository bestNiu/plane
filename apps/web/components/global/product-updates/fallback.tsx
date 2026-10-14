/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import { EmptyStateDetailed } from "@plane/blocks/empty-state";

type TProductUpdatesFallbackProps = {
  description: string;
  variant: "cloud" | "self-managed";
};

export function ProductUpdatesFallback(props: TProductUpdatesFallbackProps) {
  const { description } = props;

  return (
    <div className="py-8">
      <EmptyStateDetailed assetKey="changelog" description={description} align="center" />
    </div>
  );
}
