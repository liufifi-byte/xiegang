"use client";

interface Props {
  text: string;
}

/** 居中系统消息：3 轮辩论切换 / 议会启动 / 选举 等 */
export function SystemMessage({ text }: Props) {
  return (
    <div className="my-4 flex items-center gap-3 px-4">
      <span className="flex-1 h-px bg-edge" />
      <span className="text-xs text-ink-muted whitespace-nowrap">
        {text}
      </span>
      <span className="flex-1 h-px bg-edge" />
    </div>
  );
}
