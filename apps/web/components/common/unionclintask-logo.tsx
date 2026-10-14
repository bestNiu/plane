/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

import logoUrl from "@/app/assets/icons/icon-180x180.png?url";

type TUnionclinTaskLogoProps = {
  className?: string;
  height?: number;
  width?: number;
};

/** Icon-only UnionclinTask mark. */
export const UnionclinTaskLogo = ({ className = "", height, width }: TUnionclinTaskLogoProps) => (
  <img
    src={logoUrl}
    alt="UnionclinTask"
    className={className}
    style={{
      height: height ? `${height}px` : undefined,
      width: width ? `${width}px` : undefined,
      display: "block",
      objectFit: "contain",
    }}
  />
);

/** UnionclinTask icon + wordmark. */
export const UnionclinTaskLockup = ({ className = "", height }: TUnionclinTaskLogoProps) => (
  <span
    className={`inline-flex items-center gap-2 ${className}`}
    style={height ? { height: `${height}px` } : undefined}
  >
    <img src={logoUrl} alt="" style={{ height: "100%", width: "auto", display: "block", objectFit: "contain" }} />
    <span className="font-semibold whitespace-nowrap" style={{ fontSize: "1.05em", lineHeight: 1 }}>
      UnionclinTask
    </span>
  </span>
);
