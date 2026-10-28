// Lanes: where a request ended up. The one bold device in the design system.
export type Lane = "done" | "review" | "schedule" | "urgent";

export const laneLabel: Record<Lane, string> = {
  done: "Handled",
  review: "Needs a person",
  schedule: "Scheduled",
  urgent: "Urgent",
};

export const chipClass: Record<Lane | "neutral", string> = {
  done: "bg-done-soft text-done",
  review: "bg-review-soft text-review",
  schedule: "bg-schedule-soft text-schedule",
  urgent: "bg-urgent-soft text-urgent",
  neutral: "bg-sunken text-ink-soft",
};

/** Map the API's tones onto lanes. */
export function toneToLane(tone: string): Lane | "neutral" {
  switch (tone) {
    case "success": return "done";
    case "warning": return "review";
    case "info": return "schedule";
    case "destructive": return "urgent";
    default: return "neutral";
  }
}
