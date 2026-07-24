export type CouncilPhase = "understanding" | "research" | "debate" | "summary";

const PHASES: Array<{ id: CouncilPhase; label: string }> = [
  { id: "understanding", label: "理解问题" },
  { id: "research", label: "自主调研" },
  { id: "debate", label: "观点交锋" },
  { id: "summary", label: "主席总结" },
];

export function CouncilPhaseBar({ phase }: { phase: CouncilPhase }) {
  const activeIndex = PHASES.findIndex((item) => item.id === phase);
  return (
    <div className="grid grid-cols-4 gap-2 border-b border-[#e8e3e5] bg-[#fffdfb] px-4 py-2.5 sm:px-7">
      {PHASES.map((item, index) => (
        <div
          key={item.id}
          className={`text-[10px] sm:text-xs ${
            index === activeIndex
              ? "font-bold text-[#4b1c62]"
              : index < activeIndex
              ? "text-[#71646e]"
              : "text-[#aaa0a6]"
          }`}
        >
          <span
            className={`mb-1.5 block h-1 rounded-full ${
              index <= activeIndex ? "bg-[#4b1c62]" : "bg-[#e5dde1]"
            }`}
          />
          {item.label}
        </div>
      ))}
    </div>
  );
}
