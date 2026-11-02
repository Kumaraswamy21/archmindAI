"use client";

import { ErrorState } from "@/components/states";

export default function ErrorPage({ reset }: { error: Error; reset: () => void }) {
  return <div className="page-stack page-narrow"><ErrorState message="This page could not be rendered. Please try again." onRetry={reset} /></div>;
}
