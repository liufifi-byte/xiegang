"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { HomeSceneTabs } from "@/components/HomeSceneTabs";
import { saveCouncilDraft } from "@/lib/council-draft";
import { ActiveCouncilBanner } from "@/components/council/ActiveCouncilBanner";

export default function HomePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = (
    topic: string,
    category: string | null,
    background: string
  ) => {
    setError(null);
    try {
      saveCouncilDraft({
        topic,
        topicCategory: category,
        userBackground: background,
      });
      router.push("/council/setup");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setLoading(false);
    }
  };

  return (
    <main className="min-h-screen bg-[#fffaf6] text-[#251536]">
      <div className="pt-4">
        <ActiveCouncilBanner />
      </div>
      <HomeSceneTabs onSubmit={handleSubmit} loading={loading} />
      {error && (
        <div className="max-w-3xl mx-auto px-5 sm:px-6 pb-8">
          <div className="p-4 bg-[#fff0ed] border border-[#ec403d]/30 rounded-xl text-[#a43b38] text-sm">
            <strong>暂时无法继续：</strong>
            {error}
          </div>
        </div>
      )}
    </main>
  );
}
