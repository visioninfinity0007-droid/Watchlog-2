"use client";

import { useEffect } from "react";

// WatchLog is AI-first. Keep this legacy route only as a compatibility redirect so
// old bookmarks and older onboarding links always land on the primary experience.
export default function DashboardRedirect() {
  useEffect(() => {
    location.replace("/ai/");
  }, []);

  return (
    <div className="center">
      <p className="muted">Opening WatchLog AI...</p>
    </div>
  );
}
