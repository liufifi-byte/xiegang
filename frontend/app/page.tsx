"use client";

import { useState } from "react";
import { useRouter } from "next/navigation";
import { HomeSceneTabs } from "@/components/HomeSceneTabs";
import { createParliament, RateLimitError } from "@/lib/api";

export default function HomePage() {
  const router = useRouter();
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const handleSubmit = async (
    topic: string,
    category: string | null,
    background: string
  ) => {
    setLoading(true);
    setError(null);
    try {
      const result = await createParliament(
        topic,
        category || undefined,
        background || undefined
      );
      router.push(`/parliament/${result.parliament_id}`);
    } catch (e) {
      if (e instanceof RateLimitError) {
        setError(e.message);
      } else {
        setError(e instanceof Error ? e.message : String(e));
      }
      setLoading(false);
    }
  };

  return (
    <main>
      <HomeSceneTabs onSubmit={handleSubmit} loading={loading} />
      {error && (
        <div className="max-w-3xl mx-auto px-6 pb-8">
          <div className="p-4 bg-stance-againstBg border border-stance-againstText/30 rounded-md text-stance-againstText text-sm">
            <strong>创建议会失败：</strong>
            {error}
          </div>
        </div>
      )}
    </main>
  );
}
