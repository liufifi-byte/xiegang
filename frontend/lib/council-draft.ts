export interface CouncilDraft {
  topic: string;
  topicCategory: string | null;
  userBackground: string;
  createdAt: number;
}

const STORAGE_KEY = "xiegang-council-draft";
const MAX_AGE_MS = 24 * 60 * 60 * 1000;

export function saveCouncilDraft(
  draft: Omit<CouncilDraft, "createdAt">
): CouncilDraft {
  const value = { ...draft, createdAt: Date.now() };
  sessionStorage.setItem(STORAGE_KEY, JSON.stringify(value));
  return value;
}

export function loadCouncilDraft(): CouncilDraft | null {
  const raw = sessionStorage.getItem(STORAGE_KEY);
  if (!raw) return null;
  try {
    const value = JSON.parse(raw) as CouncilDraft;
    if (
      !value.topic ||
      typeof value.createdAt !== "number" ||
      Date.now() - value.createdAt > MAX_AGE_MS
    ) {
      sessionStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return value;
  } catch {
    sessionStorage.removeItem(STORAGE_KEY);
    return null;
  }
}
