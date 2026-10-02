"use client";

import { useEffect } from "react";

// Keep this legacy route as a compatibility redirect to the owner-first Home experience.
export default function DashboardRedirect() {
  useEffect(() => {
    location.replace("/home/");
  }, []);

  return (
    <div className="center">
      <p className="muted">Opening WatchLog...</p>
    </div>
  );
}
