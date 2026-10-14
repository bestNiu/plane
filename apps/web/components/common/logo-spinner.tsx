/**
 * Copyright (c) 2023-present Plane Software, Inc. and contributors
 * SPDX-License-Identifier: AGPL-3.0-only
 * See the LICENSE file for details.
 */

// assets
import LogoSpinnerMark from "@/app/assets/images/unionclintask-spinner.png?url";

export function LogoSpinner() {
  return (
    <div className="flex items-center justify-center">
      <img src={LogoSpinnerMark} alt="UnionclinTask" className="h-6 w-auto animate-pulse object-contain sm:h-11" />
    </div>
  );
}
